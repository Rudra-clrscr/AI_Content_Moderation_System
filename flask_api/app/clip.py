"""Looking at the picture, not just reading the words in it.

Everything else in this service reads text. That left a hole the contract has always been
explicit about: an image was OCR'd for writing and the *picture* was never judged, so a
photograph of anything at all published as long as it carried no harmful words. This is the
piece that closes it.

CLIP (Contrastive Language-Image Pre-training) embeds pictures and sentences into one space, so
an image can be scored against a written description instead of against trained classes. The
policy is therefore `config/visual_policy.yaml` - a few phrasings per label - and adding a
label needs no retraining, no labelled pictures and no code change. What it costs is that
nothing is learned from BONC's own uploads: a label is only as good as its wordings, which is
why `scripts/eval_clip_images.py` exists and why the shipped threshold was measured rather
than guessed.

**Only the vision encoder is loaded here.** The text side runs once, offline, in
`scripts/build_clip_prompts.py`, which writes the prompt embeddings to `prompts.npz`. Serving
therefore needs no tokenizer and no text model: 352 MB instead of 606 MB, and a matrix multiply
against a few dozen 512-float vectors instead of a second forward pass. Changing the policy
file means re-running that script, which is the point at which the prompts are version-stamped
so a stale `prompts.npz` is detected rather than silently scored against.

ONNX Runtime, like the moderation model and OCR: one inference stack, no PyTorch, nothing
fetched while a request is in flight.

**It fails open on absence and closed on error.** With no model files the check is simply off
and results say `visual_content_checked: false`, exactly as before this existed - it must never
become a hard dependency that takes the Articles tab down. But a model that is present and
*throws* is a different thing: that returns `failed`, and the caller refuses the upload rather
than publishing a picture nothing looked at.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Preprocessing and scoring are properties of the BUNDLE, not of this file: CLIP and SigLIP
# normalise differently, resize differently and score differently, and a bundle that lies about
# any of it scores every picture wrongly while looking fine. build_clip_prompts.py writes these
# into prompts.npz; the values here are only the fallback for a bundle built before it did.
IMAGE_SIZE = 224
MEAN = (0.48145466, 0.4578275, 0.40821073)      # CLIP
STD = (0.26862954, 0.26130258, 0.27577711)
# CLIP's learned logit scale. It turns cosine similarities, which all sit in a narrow band,
# into a usable distribution before the softmax across labels.
LOGIT_SCALE = 100.0

# How a bundle turns similarities into scores.
#
#   softmax   CLIP. Labels COMPETE: one softmax across every label, so probabilities sum to 1.
#             Raising one label lowers the others, which is why adding safe prompts has cost
#             weapon recall three times in this project's history.
#   sigmoid   SigLIP. Each label is scored INDEPENDENTLY, sigmoid(cos * scale + bias). A picture
#             can be both weapon and gore, and a new safe prompt cannot drag an unsafe one down.
#             The thresholds do not carry over between the two - they mean different things.
SOFTMAX, SIGMOID = "softmax", "sigmoid"


# Execution providers by name, best first. CUDA for NVIDIA; DirectML is the Windows fallback
# that works on any DX12 GPU without a CUDA install; CPU always exists.
_DEVICES: dict[str, tuple[str, ...]] = {
    "auto": ("CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"),
    "cuda": ("CUDAExecutionProvider", "CPUExecutionProvider"),
    "directml": ("DmlExecutionProvider", "CPUExecutionProvider"),
    "dml": ("DmlExecutionProvider", "CPUExecutionProvider"),
    "cpu": ("CPUExecutionProvider",),
}


def _preload_gpu_dlls(ort, device: str) -> None:
    """Make the CUDA and cuDNN libraries findable before a session asks for them.

    The GPU wheel does not carry them; they arrive as separate `nvidia-*` pip packages whose
    DLLs are not on PATH, so onnxruntime reports CUDA "available" and then fails to create the
    provider with a bare "cublasLt64_13.dll is missing". `preload_dlls` loads them out of those
    packages. It is a no-op on a CPU-only install and absent from older onnxruntime, so both
    cases are tolerated rather than assumed.
    """
    if str(device).strip().lower() == "cpu":
        return
    preload = getattr(ort, "preload_dlls", None)
    if preload is None:
        return
    try:
        preload()
    except Exception:                       # nothing here is worth failing a startup over
        log.debug("onnxruntime.preload_dlls() failed", exc_info=True)


def _providers(device: str, available) -> list[str]:
    """The provider list to hand onnxruntime, filtered to what this install actually has.

    A GPU provider that is named but not installed is dropped rather than raising: the stock
    `onnxruntime` wheel is CPU-only, and a deployment that has not swapped it for
    `onnxruntime-gpu` should still moderate pictures, just more slowly.
    """
    wanted = _DEVICES.get(str(device).strip().lower())
    if wanted is None:
        log.warning("unknown visual.device %r - using auto (%s)", device, ", ".join(_DEVICES["auto"]))
        wanted = _DEVICES["auto"]
    have = [p for p in wanted if p in available]
    return have or ["CPUExecutionProvider"]


@dataclass(frozen=True)
class VisualConfig:
    enabled: bool = False
    model_dir: str = "models/img_v1"
    # Above this, an unsafe label is acted on. Measured, not guessed - see
    # scripts/eval_clip_images.py and the table in contracts/moderation_result.md.
    reject_min: float = 0.90
    # The trained probe's own boundary (training/train_image_probe.py, probe.npz). Separate
    # from reject_min because the two answer differently-shaped questions: the prompts compete
    # in a softmax, the probe gives a calibrated P(unsafe) learned from BONC's own pictures.
    # A picture is refused if EITHER crosses its own line.
    #
    # Measured out-of-fold with grouped cross-validation, so near-duplicate video frames never
    # straddle a fold: at 0.70 the probe catches 98% of gore against the prompts' 93%, for
    # 3 false positives in 368. It does NOT beat the prompts on weapons (73% against 77%) -
    # 300 frames from a handful of videos is too little variety to generalise from - so the
    # two are kept together rather than one replacing the other.
    probe_reject_min: float = 0.70
    # How close an upload must be to a picture an admin marked "refuse things like this".
    # Set from this corpus: within a coherent category image-to-image similarity averages
    # 0.58-0.66 and across categories 0.42-0.44, so 0.60 sits in the gap. It is NOT comparable
    # to reject_min above - that one scores against text, this against pictures, and the two
    # live on different scales (0.277 vs 0.663 for the same wedding photograph).
    exemplar_reject_min: float = 0.60
    max_pixels: int = 40_000_000
    intra_op_threads: int = 4
    inter_op_threads: int = 1
    # Where to run the vision encoder. "auto" takes the best provider onnxruntime actually
    # offers, which is a CPU-only install's CPUExecutionProvider unless the GPU build is
    # present. Naming one explicitly ("cuda", "directml", "cpu") is for pinning a host; an
    # unavailable one falls back with a warning rather than failing to start, because a
    # missing GPU must not take the Articles tab down.
    device: str = "auto"

    def __post_init__(self) -> None:
        if not 0.0 <= self.reject_min <= 1.0:
            raise ValueError("visual.reject_min must be in [0, 1]")
        if min(self.intra_op_threads, self.inter_op_threads, self.max_pixels) < 1:
            raise ValueError("visual limits must be positive")


@dataclass(frozen=True)
class VisualResult:
    """What the visual check made of one picture.

    `checked` is the honest bit: false means nothing looked at the picture, whether because the
    check is switched off or because its files are missing. A caller must not report an
    unchecked image as clean - that is the same mistake as reporting unreadable OCR as "no
    text", which is how a Devanagari threat once published with a clean record.
    """

    checked: bool = False
    # How close this picture came to one an admin marked "refuse/allow things like this".
    # Scored against pictures, not phrasings, so not comparable to `score` - see
    # VisualCheck.reload_exemplars for why the two cannot share a scale.
    exemplar_block: float = 0.0
    exemplar_allow: float = 0.0
    exemplar_match: dict | None = None     # the refusing exemplar, for the audit trail
    label: str = ""                       # best-matching label, safe or unsafe
    category: str = ""                    # policy category for an unsafe label
    score: float = 0.0                    # probability of `label`
    unsafe_score: float = 0.0             # total probability across every unsafe label
    scores: dict[str, float] = field(default_factory=dict)
    # The trained probe's view, when the bundle carries one. Empty otherwise.
    probe_scores: dict[str, float] = field(default_factory=dict)
    probe_unsafe: float = 0.0
    # Set when this picture belongs to a category the admin has withdrawn from the image
    # model. The probe's reading is still reported - it is useful to see - but it does not
    # get a vote, and neither do that category's exemplars.
    withdrawn_category: str = ""
    failed: bool = False                  # the model is present but threw

    @property
    def unsafe(self) -> bool:
        return self.checked and self.category != ""

    def as_dict(self) -> dict:
        d = {"visual_content_checked": self.checked}
        if self.checked:
            d["visual"] = {"label": self.label, "score": round(self.score, 4),
                           "unsafe_score": round(self.unsafe_score, 4),
                           "scores": {k: round(v, 4) for k, v in self.scores.items()}}
            if self.probe_scores:
                d["visual"]["probe"] = {"unsafe": round(self.probe_unsafe, 4),
                                        "scores": {k: round(v, 4)
                                                   for k, v in self.probe_scores.items()}}
                # A reading of 0.83 next to a published picture looks like a bug unless the
                # record says the probe was stood down and why.
                if self.withdrawn_category:
                    d["visual"]["probe"]["withheld"] = self.withdrawn_category
            if self.category:
                d["visual"]["category"] = self.category
            # WHICH CHANNEL OBJECTED, and to what. Without this the record of an
            # exemplar refusal reads as a picture that should have been published: the
            # label is `safe` at 0.46, the probe is 0.59 against a bar of 0.70, and
            # nothing anywhere names the thing that refused it. That happened - a cloud
            # photograph was refused by a rainbow somebody had marked weeks earlier, and
            # the only way to find out was to score the image by hand. An admin reading
            # this result should be able to see the cause and go straight to it.
            if self.exemplar_block or self.exemplar_allow:
                ex = {"block": round(self.exemplar_block, 4),
                      "allow": round(self.exemplar_allow, 4)}
                if self.exemplar_match:
                    # The id is the point: it is the row in the dashboard to go and change.
                    ex["matched"] = self.exemplar_match
                d["visual"]["exemplar"] = ex
        return d

    def refused_by(self, config) -> str | None:
        """Which channel refuses this picture, or None if nothing does.

        Mirrors the order in VisualCheck.decides_reject rather than re-deciding, so the
        name reported is always the reason actually used. Kept beside as_dict because a
        verdict with no attributable cause is what made the cloud case take an hour.
        """
        if not self.checked:
            return None
        if self.exemplar_allow >= config.exemplar_reject_min and \
                self.exemplar_allow >= self.exemplar_block:
            return None
        if self.exemplar_block >= config.exemplar_reject_min:
            return "exemplar"
        if (self.probe_scores and not self.withdrawn_category
                and self.probe_unsafe >= config.probe_reject_min):
            return "probe"
        return "policy" if self.unsafe else None


class VisualCheck:
    """The vision encoder plus the prompt embeddings it is scored against.

    Built once and shared: the session is thread-safe for concurrent `Run` calls, and the
    prompt matrix is read-only. Loading is lazy so an import of this module never costs 352 MB.
    """

    def __init__(self, config: VisualConfig, root: Path | None = None):
        self.config = config
        self.root = Path(root) if root else Path(__file__).resolve().parent.parent
        self._lock = threading.RLock()
        self._session = None
        self._labels: list[str] = []          # one entry per prompt row
        self._categories: dict[str, str] = {}
        self._thresholds: dict[str, float] = {}   # per-label reject_min, falling back to config
        self._scoring = SOFTMAX
        self._scale, self._bias = LOGIT_SCALE, 0.0
        self._image_size = IMAGE_SIZE
        self._mean, self._std = MEAN, STD
        self._fit = "crop"                    # "crop" (CLIP) or "stretch" (SigLIP)
        self._prompt_embeds = None            # (n_prompts, dim), L2-normalised
        self._image_output = "image_embeds"   # which vision output carries the embedding
        self._probe = None                    # (W, b, classes) from probe.npz, when present
        self._exemplars = None                # (embeds, actions, categories, ids) when present
        self._withdrawn: set[str] = set()     # categories the admin took away from the model
        self._loaded = False
        self._provider = ""                   # the execution provider actually in use
        self._unavailable = ""                # why it is off, for /ready

    # ---- loading ----------------------------------------------------------------

    @property
    def model_dir(self) -> Path:
        p = Path(self.config.model_dir)
        return p if p.is_absolute() else self.root / p

    def _load(self) -> bool:
        """True once the encoder and prompts are in memory. Never raises: a missing model
        turns the check off, it does not take the service down."""
        if self._loaded:
            return True
        with self._lock:
            if self._loaded:
                return True
            if not self.config.enabled:
                self._unavailable = "visual.enabled is false"
                return False
            try:
                import numpy as np
                import onnxruntime as ort
            except ImportError as e:
                self._unavailable = f"{e.name} is not installed"
                log.warning("visual check off: %s", self._unavailable)
                return False

            model = self.model_dir / "vision_model.onnx"
            prompts = self.model_dir / "prompts.npz"
            missing = [p.name for p in (model, prompts) if not p.exists()]
            if missing:
                self._unavailable = (f"{', '.join(missing)} not in {self.model_dir} - run "
                                     "scripts/fetch_clip.py and scripts/build_clip_prompts.py")
                log.warning("visual check off: %s", self._unavailable)
                return False

            try:
                data = np.load(prompts, allow_pickle=False)
                embeds = data["embeds"].astype(np.float32)
                self._labels = [str(x) for x in data["labels"]]
                self._categories = dict(zip((str(x) for x in data["category_labels"]),
                                            (str(x) for x in data["categories"])))
                if "threshold_labels" in data.files:
                    self._thresholds = {str(k): float(v) for k, v in
                                        zip(data["threshold_labels"], data["threshold_values"])}
                if embeds.shape[0] != len(self._labels):
                    raise ValueError(f"{embeds.shape[0]} embeddings for {len(self._labels)} labels")
                # Normalised at build time, but a re-normalise here is cheap insurance against a
                # hand-edited file scoring everything at once.
                norms = np.linalg.norm(embeds, axis=1, keepdims=True)
                self._prompt_embeds = embeds / np.maximum(norms, 1e-12)

                # Everything about how this bundle wants to be run. Defaults are CLIP's, so a
                # prompts.npz built before these existed still loads and behaves identically.
                def opt(key, default):
                    return data[key].item() if key in data.files else default

                self._scoring = str(opt("scoring", SOFTMAX))
                self._scale = float(opt("logit_scale", LOGIT_SCALE))
                self._bias = float(opt("logit_bias", 0.0))
                self._image_size = int(opt("image_size", IMAGE_SIZE))
                self._fit = str(opt("fit", "crop"))
                if "mean" in data.files and "std" in data.files:
                    self._mean = tuple(float(x) for x in data["mean"])
                    self._std = tuple(float(x) for x in data["std"])
                if self._scoring not in (SOFTMAX, SIGMOID):
                    raise ValueError(f"unknown scoring {self._scoring!r} in {prompts.name}")

                # Optional: a linear probe trained on this encoder's embeddings. Absent on a
                # bundle that has never been fitted, and the check then behaves exactly as it
                # did before probes existed.
                probe_file = self.model_dir / "probe.npz"
                if probe_file.exists():
                    pr = np.load(probe_file, allow_pickle=False)
                    if int(pr["dim"]) != self._prompt_embeds.shape[1]:
                        log.warning("probe.npz is %d-d but the prompts are %d-d - ignoring it; "
                                    "it was fitted on a different encoder",
                                    int(pr["dim"]), self._prompt_embeds.shape[1])
                    else:
                        self._probe = (pr["W"].astype(np.float32), pr["b"].astype(np.float32),
                                       [str(x) for x in pr["classes"]])
                    self._withdrawn = self._read_withdrawn(pr)

                opts = ort.SessionOptions()
                opts.intra_op_num_threads = self.config.intra_op_threads
                opts.inter_op_num_threads = self.config.inter_op_threads
                _preload_gpu_dlls(ort, self.config.device)
                providers = _providers(self.config.device, ort.get_available_providers())
                self._session = ort.InferenceSession(str(model), opts, providers=providers)
                self._provider = self._session.get_providers()[0]
                outs = [o.name for o in self._session.get_outputs()]
                self._image_output = "image_embeds" if "image_embeds" in outs else "pooler_output"
                if self._image_output not in outs:
                    raise ValueError(f"vision model exposes {outs}, neither image_embeds nor "
                                     "pooler_output")
            except Exception as e:
                self._unavailable = f"could not load the visual model: {e}"
                log.exception("visual check off")
                return False

            self._loaded = True
            log.info("visual check ready on %s: %d prompts over %d labels from %s "
                     "(%s scoring, %dpx %s)", self._provider, len(self._labels),
                     len(set(self._labels)), self.model_dir, self._scoring,
                     self._image_size, self._fit)
            if self.config.device not in ("auto", "cpu") and self._provider == "CPUExecutionProvider":
                log.warning("visual.device is %r but onnxruntime gave CPUExecutionProvider - "
                            "is the GPU build of onnxruntime installed?", self.config.device)
            return True

    @property
    def ready(self) -> bool:
        return self._load()

    @staticmethod
    def _read_withdrawn(pr) -> set[str]:
        """The categories an admin has taken away from the model, as recorded by the trainer.

        Absent on any bundle written before withdrawal existed, and absent is not the same as
        empty only in that it must not crash - either way nothing is withdrawn, which is the
        behaviour those bundles already had.
        """
        try:
            return {str(x).strip().lower() for x in pr["withdrawn"] if str(x).strip()}
        except Exception:
            return set()

    def reload_probe(self, probe_path=None, threshold_overrides=None):
        """Dynamically reload probe weights and/or threshold overrides into memory without restart."""
        self._load()
        with self._lock:
            import numpy as np
            p = Path(probe_path) if probe_path else (self.model_dir / "probe.npz")
            if p.exists():
                try:
                    pr = np.load(p, allow_pickle=False)
                    self._probe = (pr["W"].astype(np.float32), pr["b"].astype(np.float32),
                                   [str(x) for x in pr["classes"]])
                    self._withdrawn = self._read_withdrawn(pr)
                    log.info("VisualCheck reloaded probe from %s with classes: %s%s", p.name,
                             self._probe[2],
                             f", withdrawn: {sorted(self._withdrawn)}" if self._withdrawn else "")
                except Exception:
                    log.exception("VisualCheck failed to reload probe from %s", p)
                    return False
            if threshold_overrides:
                self._thresholds.update({str(k): float(v) for k, v in threshold_overrides.items()})
                log.info("VisualCheck updated thresholds: %s", threshold_overrides)
            return True

    def reload_exemplars(self) -> bool:
        """Load models/img_v1/exemplars.npz: pictures an admin pointed at directly.

        WHY THIS IS A THIRD CHANNEL AND NOT MORE PROMPTS. CLIP puts images and text in one
        space, so an exemplar's vector could in principle sit in prompts.npz beside the
        phrasings. It must not. Measured on this corpus, a wedding photograph scores 0.277
        against "a photo of a wedding ceremony" and 0.663 against another wedding photograph -
        the modality gap - so a single exemplar dropped into that softmax would outscore every
        phrasing in the file and decide almost everything on its own.

        Scored separately, against its own bar, it composes instead of dominating. Same reason
        the probe is separate.

        WHAT IT IS FOR. An admin who uploads a picture and says "refuse things like this" is
        not writing policy and should not have to. The exemplar takes effect on the next
        upload - no retraining, no dataset - and changing their mind is deleting a row rather
        than fitting a model again.
        """
        with self._lock:
            import numpy as np
            p = self.model_dir / "exemplars.npz"
            if not p.exists():
                self._exemplars = None
                return True
            try:
                d = np.load(p, allow_pickle=False)
                E = d["embeds"].astype(np.float32)
                if E.size == 0:
                    self._exemplars = None
                    return True
                E = E / np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-12)
                self._exemplars = (E, [str(x) for x in d["actions"]],
                                   [str(x) for x in d["categories"]],
                                   [int(x) for x in d["ids"]])
            except Exception:
                log.exception("VisualCheck failed to load %s - exemplars left as they were", p)
                return False
            n_block = sum(1 for a in self._exemplars[1] if a == "block")
            log.info("VisualCheck loaded %d exemplars (%d refuse, %d allow)",
                     len(self._exemplars[1]), n_block, len(self._exemplars[1]) - n_block)
            return True

    def match_exemplars(self, embed) -> tuple[float, float, dict | None]:
        """(best refuse similarity, best allow similarity, the refusing exemplar).

        The allow side is a veto, not a score: an admin who marked a picture "allow things
        like this" is overruling the refuse side for things that look like it, which is the
        only way "I changed my mind about this one" can work without deleting the rule that
        refuses the rest.
        """
        ex = getattr(self, "_exemplars", None)
        if ex is None or embed is None:
            return 0.0, 0.0, None
        import numpy as np
        E, actions, cats, ids = ex
        v = np.asarray(embed, dtype=np.float32).reshape(-1)
        v = v / max(float(np.linalg.norm(v)), 1e-12)
        if v.shape[0] != E.shape[1]:
            return 0.0, 0.0, None
        sims = E @ v
        best_block, best_allow, who = 0.0, 0.0, None
        for i, a in enumerate(actions):
            # AN EXEMPLAR OF A WITHDRAWN CATEGORY DOES NOT SPEAK. Deselecting an image for
            # training does not clear the separate mark that says "refuse things like this",
            # so an admin who deselected all 72 gym photographs and retrained still had three
            # of them refusing the other 71 - the model had been told to forget the category
            # and three leftover marks kept enforcing it. Withdrawing a category withdraws
            # its exemplars with it; marks in categories still being trained are untouched.
            if str(cats[i]).strip().lower() in self._withdrawn:
                continue
            s = float(sims[i])
            if a == "block":
                if s > best_block:
                    best_block, who = s, {"id": ids[i], "category": cats[i], "score": round(s, 4)}
            elif s > best_allow:
                best_allow = s
        return best_block, best_allow, who

    def reload_prompts(self) -> bool:
        """Re-read prompts.npz after the policy has been rebuilt, without a restart.

        The policy is config/visual_policy.yaml and the thing actually scored against is
        prompts.npz, which scripts/build_clip_prompts.py bakes from it offline - the text
        encoder is 254 MB and deliberately never enters the serving process. So an admin who
        edits the policy produces a new npz, and this swaps it in: the labels, their
        categories, their per-label thresholds and the embedding matrix, all replaced together
        under the lock.

        Together, not piecemeal, because they are one consistent set. A half-applied reload -
        new embeddings against old labels - would score every picture against the wrong name,
        and `classify` would keep working and keep being wrong, which is worse than failing.
        On any error the previous set is left exactly as it was and False is returned.
        """
        with self._lock:
            if not self._loaded:
                # Nothing is in memory to go stale: the check loads lazily and _load() will
                # read whatever prompts.npz says when the first picture arrives. Reporting
                # failure here would be wrong in the one way that matters - the caller treats
                # it as "the new policy is not live" and rolls a perfectly good save back,
                # which is exactly what the first cut of this did.
                log.info("VisualCheck not loaded yet; the rebuilt policy will be read on "
                         "first use")
                return True
            import numpy as np
            p = self.model_dir / "prompts.npz"
            if not p.exists():
                log.warning("VisualCheck: no %s to reload", p)
                return False
            try:
                data = np.load(p, allow_pickle=False)
                embeds = data["embeds"].astype(np.float32)
                labels = [str(x) for x in data["labels"]]
                if embeds.shape[0] != len(labels):
                    raise ValueError(f"{embeds.shape[0]} embeddings for {len(labels)} labels")
                categories = dict(zip((str(x) for x in data["category_labels"]),
                                      (str(x) for x in data["categories"])))
                thresholds = {}
                if "threshold_labels" in data.files:
                    thresholds = {str(k): float(v) for k, v in
                                  zip(data["threshold_labels"], data["threshold_values"])}
            except Exception:
                log.exception("VisualCheck failed to reload prompts from %s - keeping the "
                              "previous policy in memory", p)
                return False
            # Re-normalised for the same reason _load does it, and with more cause here: an
            # admin-authored policy is exactly the hand-edited file that comment is about.
            norms = np.linalg.norm(embeds, axis=1, keepdims=True)
            self._prompt_embeds = embeds / np.maximum(norms, 1e-12)
            self._labels = labels
            self._categories = categories
            self._thresholds = thresholds
            log.info("VisualCheck reloaded policy: %d prompts over %d labels (%s)",
                     len(labels), len(set(labels)), ", ".join(sorted(set(labels))))
            return True

    @property
    def status(self) -> dict:
        """For GET /ready, so an operator can see whether pictures are actually being looked at
        rather than discovering it from a published result."""
        if self._load():
            return {"enabled": True, "ready": True, "labels": sorted(set(self._labels)),
                    "prompts": len(self._labels), "reject_min": self.config.reject_min,
                    "provider": self._provider, "scoring": self._scoring,
                    "label_thresholds": dict(sorted(self._thresholds.items())),
                    "probe": bool(self._probe), "probe_reject_min": self.config.probe_reject_min}
        return {"enabled": self.config.enabled, "ready": False, "reason": self._unavailable}

    # ---- scoring ----------------------------------------------------------------

    def preprocess(self, image):
        """A PIL image to the encoder's input tensor, the way its own weights expect.

        Matching the training preprocessing matters more than it looks, and the two encoders
        disagree about it: CLIP resizes the shortest edge and centre-crops (bicubic), SigLIP
        stretches straight to a square (bilinear). Using one model's recipe on the other's
        weights measurably moves every score while nothing visibly fails.
        """
        import numpy as np
        from PIL import Image

        size = self._image_size
        img = image.convert("RGB")
        if self._fit == "stretch":
            img = img.resize((size, size), Image.BILINEAR)
        else:
            w, h = img.size
            scale = size / min(w, h)
            img = img.resize((max(size, round(w * scale)), max(size, round(h * scale))),
                             Image.BICUBIC)
            w, h = img.size
            left, top = (w - size) // 2, (h - size) // 2
            img = img.crop((left, top, left + size, top + size))
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - np.array(self._mean, dtype=np.float32)) / np.array(self._std, dtype=np.float32)
        return arr.transpose(2, 0, 1)[None]

    def classify(self, image) -> VisualResult:
        """Score one PIL image against the policy. Never raises."""
        if not self._load():
            return VisualResult(checked=False)
        try:
            import numpy as np

            pixels = self.preprocess(image)
            # CLIP's vision tower emits the projected `image_embeds`; SigLIP's emits
            # `pooler_output` with `last_hidden_state` FIRST, so taking output[0] blindly would
            # hand 196 patch vectors to a cosine against one prompt vector.
            embed = self._session.run([self._image_output], {"pixel_values": pixels})[0]
            embed = embed / np.maximum(np.linalg.norm(embed, axis=1, keepdims=True), 1e-12)
            return self.score_embedding(embed)
        except Exception:
            # Present but broken. Distinct from "off", because the caller should refuse the
            # upload rather than publish a picture nothing looked at.
            log.exception("visual check failed while encoding one image")
            return VisualResult(checked=False, failed=True)

    def score_embedding(self, embed) -> VisualResult:
        """Everything classify() does AFTER the encoder, given an embedding already in hand.

        Split out so a stored embedding can be put through the same decision the Articles tab
        makes, rather than through an imitation of it. The admin dashboard holds thousands of
        embeddings in system.db, and re-encoding those pictures to answer "what would this
        retrain actually refuse?" would cost minutes per retrain while changing none of the
        arithmetic below. An imitation is the thing to avoid here: the reason a retrain could
        report 85% while refusing 59% of the admin's own category is that the number shown
        came from somewhere other than the code that decides.
        """
        if not self._load():
            return VisualResult(checked=False)
        try:
            import numpy as np

            embed = np.asarray(embed, dtype=np.float32)
            if embed.ndim == 1:
                embed = embed[None, :]
            embed = embed / np.maximum(np.linalg.norm(embed, axis=1, keepdims=True), 1e-12)
            sims = (embed @ self._prompt_embeds.T)[0]

            # Best phrasing per label, then a softmax across the labels. Taking the best rather
            # than the mean is the usual zero-shot ensemble: one wording can catch a picture
            # that the others miss, and averaging would dilute exactly that.
            names = sorted(set(self._labels))
            labels = np.asarray(self._labels)
            best = np.array([sims[labels == n].max() for n in names], dtype=np.float32)

            if self._scoring == SIGMOID:
                # SigLIP: every label answered on its own account. sigmoid(cos * scale + bias)
                # with the model's own learned constants, so a score means what the weights
                # were trained to make it mean.
                probs = 1.0 / (1.0 + np.exp(-(best * self._scale + self._bias)))
                scores = {n: float(p) for n, p in zip(names, probs)}
                # Independent, so the unsafe reading is the WORST single label, not a total.
                # Summing would let three indifferent labels add up to a refusal.
                unsafe_total = max((p for n, p in scores.items() if n in self._categories),
                                   default=0.0)
            else:
                # CLIP: one softmax across the labels, so they compete and sum to 1.
                logits = self._scale * (best - best.max())
                probs = np.exp(logits)
                probs /= probs.sum()
                scores = {n: float(p) for n, p in zip(names, probs)}
                unsafe_total = float(sum(p for n, p in scores.items() if n in self._categories))

            top = max(scores, key=scores.get)

            # The probe, where one has been fitted. Softmax over its logits, then the unsafe
            # reading is simply 1 - P(safe): it was trained on the policy's own classes, so it
            # answers the same question the prompts do, from a different direction.
            probe_scores: dict[str, float] = {}
            probe_unsafe = 0.0
            if self._probe is not None:
                W, b, classes = self._probe
                logit = embed[0] @ W.T + b
                e = np.exp(logit - logit.max())
                p = e / e.sum()
                probe_scores = {c: float(v) for c, v in zip(classes, p)}
                probe_unsafe = float(1.0 - probe_scores.get("safe", 1.0))
            # THE PROBE GETS NO VOTE ON A WITHDRAWN CATEGORY. The probe cannot name one - it
            # scores its own classes, and an admin's category dissolves into them - so the
            # prompts are what says which category a picture belongs to, and the best-matching
            # label is that answer. A gym photograph with `physique` withdrawn still reads as
            # `revealing` to the probe; letting that refuse it would mean the withdrawal had
            # changed nothing, which is the whole complaint.
            withdrawn_category = top if top in self._withdrawn else ""

            # What the author is told is still the best-matching label; the probe decides
            # alongside, and names the class when it is the one objecting.
            label, category = top, self._categories.get(top, "")
            if probe_unsafe >= self.config.probe_reject_min and not category:
                probe_top = max((c for c in probe_scores if c != "safe"),
                                key=lambda c: probe_scores[c], default="")
                if probe_top in self._categories:
                    label, category = probe_top, self._categories[probe_top]
            # The same embedding the prompts were scored against, reused: an exemplar match is
            # a dot product over a handful of vectors, so this channel costs nothing measurable.
            ex_block, ex_allow, ex_match = self.match_exemplars(embed[0])
            return VisualResult(checked=True, label=label, category=category,
                                score=scores[top], unsafe_score=unsafe_total, scores=scores,
                                probe_scores=probe_scores, probe_unsafe=probe_unsafe,
                                withdrawn_category=withdrawn_category,
                                exemplar_block=ex_block, exemplar_allow=ex_allow,
                                exemplar_match=ex_match)
        except Exception:
            # Present but broken. Distinct from "off", because the caller should refuse the
            # upload rather than publish a picture nothing looked at.
            log.exception("visual check failed on an image")
            return VisualResult(checked=False, failed=True)

    def decides_reject(self, result: VisualResult) -> bool:
        """Whether this finding is strong enough to act on.

        Two conditions, not one: the best-matching label is an unsafe one *and* it clears
        `reject_min`. A picture that is 0.5 weapon and 0.5 gore is obviously a problem but
        neither label is confident, so the unsafe total is checked too.
        """
        if not result.checked:
            return False
        # EXEMPLARS FIRST, and the allow side wins ties. An admin who pointed at a picture and
        # said "allow things like this" is overruling a rule deliberately and for a specific
        # look, which is the whole mechanism for changing their mind about one kind of picture
        # without deleting the rule that refuses the rest. Checked before everything else so
        # that override actually overrides.
        if result.exemplar_allow >= self.config.exemplar_reject_min and \
                result.exemplar_allow >= result.exemplar_block:
            return False
        if result.exemplar_block >= self.config.exemplar_reject_min:
            return True
        # A WITHDRAWN CATEGORY GETS NO VOTE FROM THE PROBE. What is left is the written
        # policy below, and a category with no refusing rule publishes - which is what an
        # admin means by taking a category away from the model.
        if (result.probe_scores and not result.withdrawn_category
                and result.probe_unsafe >= self.config.probe_reject_min):
            return True
        # Every unsafe label judged at its own bar, not just the winner: the scores are not
        # comparable across labels (a median `minor` is 0.97, a median `violence` 0.57), so one
        # bar either lets violence through or refuses ordinary pictures to catch it.
        for label, score in result.scores.items():
            if label in self._categories and score >= self._thresholds.get(
                    label, self.config.reject_min):
                return True
        # And the original rule still stands underneath. It carries two cases the loop cannot:
        # a result from a bundle with no per-label bars, and a picture split across labels -
        # 0.5 weapon with 0.45 gore clears neither bar alone but is obviously not publishable.
        if result.category:
            return max(result.score, result.unsafe_score) >= self.config.reject_min
        return False
