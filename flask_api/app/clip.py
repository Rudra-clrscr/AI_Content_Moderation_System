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

# CLIP's own preprocessing, from the model card's preprocessor_config.json. These are not free
# parameters: the weights were trained on images normalised exactly this way.
IMAGE_SIZE = 224
MEAN = (0.48145466, 0.4578275, 0.40821073)
STD = (0.26862954, 0.26130258, 0.27577711)
# CLIP's learned logit scale (exp(0.07^-1) rounded, as shipped in every ViT-B/32 checkpoint).
# It turns cosine similarities, which all sit in a narrow band, into a usable distribution.
LOGIT_SCALE = 100.0


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
    label: str = ""                       # best-matching label, safe or unsafe
    category: str = ""                    # policy category for an unsafe label
    score: float = 0.0                    # probability of `label`
    unsafe_score: float = 0.0             # total probability across every unsafe label
    scores: dict[str, float] = field(default_factory=dict)
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
            if self.category:
                d["visual"]["category"] = self.category
        return d


class VisualCheck:
    """The vision encoder plus the prompt embeddings it is scored against.

    Built once and shared: the session is thread-safe for concurrent `Run` calls, and the
    prompt matrix is read-only. Loading is lazy so an import of this module never costs 352 MB.
    """

    def __init__(self, config: VisualConfig, root: Path | None = None):
        self.config = config
        self.root = Path(root) if root else Path(__file__).resolve().parent.parent
        self._lock = threading.Lock()
        self._session = None
        self._labels: list[str] = []          # one entry per prompt row
        self._categories: dict[str, str] = {}
        self._prompt_embeds = None            # (n_prompts, 512), L2-normalised
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
                if embeds.shape[0] != len(self._labels):
                    raise ValueError(f"{embeds.shape[0]} embeddings for {len(self._labels)} labels")
                # Normalised at build time, but a re-normalise here is cheap insurance against a
                # hand-edited file scoring everything at once.
                norms = np.linalg.norm(embeds, axis=1, keepdims=True)
                self._prompt_embeds = embeds / np.maximum(norms, 1e-12)

                opts = ort.SessionOptions()
                opts.intra_op_num_threads = self.config.intra_op_threads
                opts.inter_op_num_threads = self.config.inter_op_threads
                _preload_gpu_dlls(ort, self.config.device)
                providers = _providers(self.config.device, ort.get_available_providers())
                self._session = ort.InferenceSession(str(model), opts, providers=providers)
                self._provider = self._session.get_providers()[0]
            except Exception as e:
                self._unavailable = f"could not load the visual model: {e}"
                log.exception("visual check off")
                return False

            self._loaded = True
            log.info("visual check ready on %s: %d prompts over %d labels from %s",
                     self._provider, len(self._labels), len(set(self._labels)), self.model_dir)
            if self.config.device not in ("auto", "cpu") and self._provider == "CPUExecutionProvider":
                log.warning("visual.device is %r but onnxruntime gave CPUExecutionProvider - "
                            "is the GPU build of onnxruntime installed?", self.config.device)
            return True

    @property
    def ready(self) -> bool:
        return self._load()

    @property
    def status(self) -> dict:
        """For GET /ready, so an operator can see whether pictures are actually being looked at
        rather than discovering it from a published result."""
        if self._load():
            return {"enabled": True, "ready": True, "labels": sorted(set(self._labels)),
                    "prompts": len(self._labels), "reject_min": self.config.reject_min,
                    "provider": self._provider}
        return {"enabled": self.config.enabled, "ready": False, "reason": self._unavailable}

    # ---- scoring ----------------------------------------------------------------

    def preprocess(self, image):
        """A PIL image to CLIP's input tensor: shortest edge to 224 (bicubic), centre crop,
        rescale, normalise. Matching the training preprocessing matters more than it looks -
        a plain resize that ignores aspect ratio measurably moves the scores."""
        import numpy as np
        from PIL import Image

        img = image.convert("RGB")
        w, h = img.size
        scale = IMAGE_SIZE / min(w, h)
        img = img.resize((max(IMAGE_SIZE, round(w * scale)), max(IMAGE_SIZE, round(h * scale))),
                         Image.BICUBIC)
        w, h = img.size
        left, top = (w - IMAGE_SIZE) // 2, (h - IMAGE_SIZE) // 2
        img = img.crop((left, top, left + IMAGE_SIZE, top + IMAGE_SIZE))
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
        return arr.transpose(2, 0, 1)[None]

    def classify(self, image) -> VisualResult:
        """Score one PIL image against the policy. Never raises."""
        if not self._load():
            return VisualResult(checked=False)
        try:
            import numpy as np

            pixels = self.preprocess(image)
            embed = self._session.run(None, {"pixel_values": pixels})[0]
            embed = embed / np.maximum(np.linalg.norm(embed, axis=1, keepdims=True), 1e-12)
            sims = (embed @ self._prompt_embeds.T)[0]

            # Best phrasing per label, then a softmax across the labels. Taking the best rather
            # than the mean is the usual zero-shot ensemble: one wording can catch a picture
            # that the others miss, and averaging would dilute exactly that.
            names = sorted(set(self._labels))
            labels = np.asarray(self._labels)
            best = np.array([sims[labels == n].max() for n in names], dtype=np.float32)
            logits = LOGIT_SCALE * (best - best.max())
            probs = np.exp(logits)
            probs /= probs.sum()

            scores = {n: float(p) for n, p in zip(names, probs)}
            top = max(scores, key=scores.get)
            unsafe_total = float(sum(p for n, p in scores.items() if n in self._categories))
            return VisualResult(checked=True, label=top,
                                category=self._categories.get(top, ""),
                                score=scores[top], unsafe_score=unsafe_total, scores=scores)
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
        if not result.checked or not result.category:
            return False
        return max(result.score, result.unsafe_score) >= self.config.reject_min
