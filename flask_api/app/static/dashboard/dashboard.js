/*
 * Business Dashboard — the shell, wired to the moderation service.
 *
 * This is a front-end reference, like /articles. The point of it is the composer: a post is
 * NOT published until the service has seen the text and every attachment, and a refusal shows
 * the author what was found rather than a bare "failed". There is no "post anyway" — that was
 * removed deliberately, and nothing here puts it back.
 *
 * Everything else (counts, feed, products) lives in this browser's localStorage. There is no
 * platform behind this page.
 *
 * Publishing is three calls, in this order:
 *
 *   1. POST /v1/moderate        the text, as the chosen content_type
 *   2. POST /v1/moderate/media  each attachment, one request each
 *   3. publish locally          only if every one of the above allowed
 *
 * Attachments go one per request because each needs its own decision and its own audit row,
 * and because what can be checked differs by kind: a photo with no writing comes back
 * "not inspected" rather than "clean", and the dashboard says so instead of implying it passed.
 */
(function () {
  "use strict";

  const M = window.BoncModeration;
  const STORE = "bonc-dashboard-v1";
  const MAX = 5000;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const SAMPLE_PRODUCTS = [
    { name: "Cotton bedsheet set, 300 TC", meta: "Home furnishing · ₹780 / piece · MOQ 50" },
    { name: "Blackout curtains, 7 ft", meta: "Home furnishing · ₹1,150 / pair · MOQ 20" },
    { name: "Handloom cushion covers", meta: "Home furnishing · ₹240 / piece · MOQ 100" },
  ];

  let state = load();
  let pending = [];      // {file, status: "wait"|"ok"|"note"|"bad", note}

  function load() {
    try {
      const raw = JSON.parse(localStorage.getItem(STORE) || "{}");
      return { posts: Array.isArray(raw.posts) ? raw.posts : [], blocked: raw.blocked || 0 };
    } catch (e) {
      return { posts: [], blocked: 0 };
    }
  }

  function save() {
    try {
      localStorage.setItem(STORE, JSON.stringify(state));
    } catch (e) { /* private browsing: the page still works, it just forgets */ }
  }

  // ---------------- rendering ----------------

  function renderTiles() {
    const live = state.posts.length;
    const week = state.posts.filter((p) => Date.now() - p.at < 7 * 864e5).length;
    const tiles = [
      { n: live, k: "Published items", d: week ? `${week} this week` : "" },
      { n: state.blocked, k: "Blocked before posting", d: state.blocked ? "Not published" : "", bad: true },
      { n: SAMPLE_PRODUCTS.length, k: "Product listings", d: "" },
      { n: "1,248", k: "Profile views", d: "+6% this month" },
    ];
    $("tiles").innerHTML = tiles.map((t) => `
      <div class="tile">
        <div class="n">${esc(t.n)}</div>
        <div class="k">${esc(t.k)}</div>
        ${t.d ? `<div class="d${t.bad ? " bad" : ""}">${esc(t.d)}</div>` : ""}
      </div>`).join("");
  }

  function renderFeed() {
    const el = $("feed");
    if (!state.posts.length) {
      el.innerHTML = `<li class="empty">Nothing published from this browser yet.</li>`;
      return;
    }
    el.innerHTML = state.posts.map((p) => `
      <li>
        <div class="meta">
          <span class="badge plain">${esc(p.type.replace(/_/g, " "))}</span>
          <span class="badge allow">published</span>
          <span>${esc(new Date(p.at).toLocaleString())}</span>
          ${p.files ? `<span>${p.files} attachment${p.files > 1 ? "s" : ""}</span>` : ""}
          ${p.unchecked ? `<span class="badge revise">${esc(p.unchecked)} not inspected</span>` : ""}
        </div>
        <p class="body">${esc(p.text)}</p>
      </li>`).join("");
  }

  function renderProducts() {
    $("products-list").innerHTML = SAMPLE_PRODUCTS.map((p) => `
      <li>
        <span class="thumb" aria-hidden="true">${esc(p.name.slice(0, 1))}</span>
        <span class="t"><b>${esc(p.name)}</b><span>${esc(p.meta)}</span></span>
        <button class="btn-link" type="button">Edit</button>
      </li>`).join("");
  }

  function renderFiles() {
    const el = $("files");
    el.innerHTML = pending.map((f, i) => `
      <li>
        <span class="name">${esc(f.file.name)}</span>
        <span class="state ${f.status}">${esc(f.note || f.status)}</span>
        <button class="btn-link" type="button" data-rm="${i}">Remove</button>
      </li>`).join("");
  }

  function strip(kind, text) {
    const el = $("strip");
    if (!kind) { el.hidden = true; return; }
    el.className = `strip ${kind}`;
    el.textContent = text;
    el.hidden = false;
  }

  function renderLast(result, label) {
    const d = result.decision;
    const rows = [
      ["Decision", `<span class="badge ${esc(d)}">${esc(d)}</span>`],
      ["Surface", esc(label)],
      ["Decided by", esc(result.decided_by || "—")],
      ["Risk score", result.risk_score == null ? "—" : result.risk_score.toFixed(4)],
      ["Model", esc(result.model_version || "—")],
      ["Latency", `${esc((result.latency_ms && result.latency_ms.total) ?? "—")} ms`],
    ];
    $("last").innerHTML = rows.map(([k, v]) =>
      `<li><span class="k">${esc(k)}</span><span class="v">${v}</span></li>`).join("");
  }

  // ---------------- publishing ----------------

  /**
   * Check every attachment. Returns {blocked, unchecked} and marks each row.
   *
   * Three outcomes, kept distinct on purpose:
   *   allow + visual_content_checked   the picture itself was looked at
   *   allow + !visual_content_checked  nothing could be checked (no writing, or a video)
   *   reject                           stops the post
   */
  async function checkFiles(contentType, contentId) {
    let blocked = 0, unchecked = 0;
    for (const entry of pending) {
      entry.status = "wait";
      entry.note = "checking…";
      renderFiles();
      let res;
      try {
        res = await M.moderateFile(entry.file, { contentType, contentId });
      } catch (e) {
        // A file the service could not judge is not a file that passed.
        entry.status = "bad";
        entry.note = e.code === "network" ? "service unreachable" : (e.message || "could not check");
        blocked++;
        renderFiles();
        continue;
      }
      const media = res.media || {};
      if (res.decision === "reject") {
        entry.status = "bad";
        entry.note = "blocked";
        blocked++;
      } else if (media.visual_content_checked === false && !media.text_found) {
        entry.status = "note";
        entry.note = "not inspected";
        unchecked++;
      } else {
        entry.status = "ok";
        entry.note = "checked";
      }
      renderFiles();
    }
    return { blocked, unchecked };
  }

  async function publish() {
    const btn = $("publish-btn");
    const text = $("post-text").value.trim();
    const type = $("post-type").value;
    const label = $("post-type").selectedOptions[0].textContent;
    if (!text && !pending.length) {
      strip("warn", "Write something or attach a file first.");
      return;
    }

    btn.disabled = true;
    strip("warn", "Checking with the moderation service…");
    const contentId = `dash-${Date.now()}`;

    try {
      // 1. the text
      let textOk = true, result = null, parts = null, content = "";
      if (text) {
        const r = await M.moderateFields([{ name: "post", text }], { contentType: type, contentId });
        result = r.result; parts = r.parts; content = r.content;
        renderLast(result, label);
        textOk = result.decision === "allow";
      }

      // 2. the attachments, whatever the text said - the author should see every problem at
      //    once rather than fixing the words only to be stopped again by the picture.
      const { blocked, unchecked } = pending.length
        ? await checkFiles(type, contentId) : { blocked: 0, unchecked: 0 };

      if (!textOk || blocked) {
        state.blocked += 1;
        save();
        renderTiles();
        if (!textOk) {
          const issues = M.mapIssues(result.feedback, parts, content);
          strip("bad", (result.feedback && result.feedback.message) || "This can't be published.");
          M.showDecision({
            result, issues, noun: label.toLowerCase(), labels: { post: "Your post" },
            onClose: () => $("post-text").focus(),
          });
        } else {
          strip("bad", `${blocked} attachment${blocked > 1 ? "s" : ""} can't be published. `
                     + "Remove or replace them and try again.");
        }
        return;
      }

      // 3. published
      state.posts.unshift({ text, type, at: Date.now(), files: pending.length, unchecked });
      state.posts = state.posts.slice(0, 50);
      save();
      $("post-text").value = "";
      pending = [];
      renderFiles();
      renderTiles();
      renderFeed();
      count();
      strip("ok", unchecked
        ? `Published. ${unchecked} attachment${unchecked > 1 ? "s" : ""} carried no text or footage that could be checked.`
        : "Published.");
    } catch (e) {
      // Nothing is published when the checker cannot be reached. Failing open here would make
      // an outage indistinguishable from a clean post.
      strip("bad", e.code === "network"
        ? "The moderation service is unreachable, so nothing was published."
        : `Could not publish: ${e.message}`);
    } finally {
      btn.disabled = false;
    }
  }

  // ---------------- service panel ----------------

  async function serviceStatus() {
    const set = (cls, text, extra = []) => {
      $("svc-status").innerHTML = `<span class="dot ${cls}"></span>${esc(text)}`;
      $("svc").innerHTML = $("svc").children[0].outerHTML + extra.map(([k, v]) =>
        `<li><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></li>`).join("");
    };
    try {
      const r = await fetch("/ready");
      const b = await r.json();
      const visual = b.visual || {};
      set(r.ok ? "up" : "down", r.ok ? "ready" : (b.status || "not ready"), [
        ["Mode", b.mode || "—"],
        ["Text model", b.model_version || (b.model_ready === false ? "not loaded" : "—")],
        ["Gate rules", `${b.gate_rules ?? "—"} (${b.gate_version || "—"})`],
        ["Pictures checked", visual.ready ? `yes · ${visual.version || "img"}` : "no"],
        ["Reject at", b.thresholds ? b.thresholds.reject_min : "—"],
      ]);
    } catch (e) {
      set("down", "unreachable", [["Posting", "blocked until it returns"]]);
    }
  }

  // ---------------- wiring ----------------

  function count() {
    const n = $("post-text").value.length;
    const el = $("count");
    el.textContent = `${n} / ${MAX}`;
    el.classList.toggle("over", n >= MAX);
  }

  function addFiles(list) {
    for (const file of list) pending.push({ file, status: "wait", note: "not checked yet" });
    renderFiles();
  }

  $("post-text").addEventListener("input", count);
  $("publish-btn").addEventListener("click", publish);
  $("post-file").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  $("files").addEventListener("click", (e) => {
    const i = e.target.dataset.rm;
    if (i == null) return;
    pending.splice(Number(i), 1);
    renderFiles();
  });
  $("clear-btn").addEventListener("click", () => {
    state = { posts: [], blocked: 0 };
    save();
    renderTiles();
    renderFeed();
  });
  for (const id of ["list-product-btn", "add-product-btn"]) {
    $(id).addEventListener("click", () => {
      $("post-type").value = "product_listing";
      $("post-text").focus();
      strip("warn", "Describe the product below, then publish — it is checked like anything else.");
    });
  }

  renderTiles();
  renderFeed();
  renderProducts();
  renderFiles();
  count();
  serviceStatus();
})();
