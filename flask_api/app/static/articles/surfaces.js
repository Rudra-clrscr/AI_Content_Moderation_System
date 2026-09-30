/*
 * The rest of the dashboard's tabs — Add Business, Videos, Requests, Proposals, Business
 * Proposals — each moderated by the same service as Articles, and each on its own
 * `content_type` so the author is told their *proposal* needs changes, not their "post".
 *
 * This is a front-end reference, deliberately plainer than the Articles editor: ordinary
 * inputs, no rich text. Wiring a real tab is the same three steps shown in submit() below —
 * collect the fields, send them as ONE request, block publishing unless the result allows.
 *
 * Attachments go one per request to /v1/moderate/media. What can be read differs by kind, so
 * a file can come back "ok" (its text was read and checked) or "not inspected" (a photo with
 * no writing, or a video — there is no visual model, so the footage is published unwatched
 * and labelled as such). Only "blocked" stops publishing.
 *
 * Everything is kept in this browser's localStorage; there is no platform behind this page.
 */
(function () {
  "use strict";
  const M = window.BoncModeration;
  const STORE_KEY = "bonc-surfaces-demo-v1";

  const TEXTAREA = "textarea";

  /** One entry per dashboard tab. `key` is the API's content_type. */
  const SURFACES = {
    business_profile: {
      heading: "Add Business",
      subtitle: "List your business so buyers on the BONC Network can find it.",
      cardTitle: "New business profile",
      action: "Publish business",
      noun: "business",
      fields: [
        { name: "name", label: "Business name", required: true, maxlength: 150,
          placeholder: "e.g. Bhavani Textiles" },
        { name: "about", label: "About the business", type: TEXTAREA, required: true, maxlength: 4000,
          placeholder: "What you make or sell, since when, and where you ship." },
        { name: "products", label: "Products and services", type: TEXTAREA, maxlength: 4000,
          placeholder: "Cotton bedsheets, curtains, bulk orders…" },
      ],
      media: { label: "Logo or brochure", accept: "image/*,application/pdf,.pdf",
               text: "Upload a logo, photo or PDF brochure" },
    },
    video: {
      heading: "Videos",
      subtitle: "Share a product demo or a tour of your unit.",
      cardTitle: "Upload a video",
      action: "Publish video",
      noun: "video",
      fields: [
        { name: "title", label: "Video title", required: true, maxlength: 150,
          placeholder: "e.g. Warehouse tour — Surat unit" },
        { name: "description", label: "Description", type: TEXTAREA, required: true, maxlength: 5000,
          placeholder: "What the video shows." },
        { name: "tags", label: "Tags", maxlength: 200, placeholder: "cotton, bedsheets, wholesale" },
      ],
      media: { label: "Video file", accept: "video/*,image/*", text: "Upload the video (or a thumbnail)" },
    },
    request: {
      heading: "Requests",
      subtitle: "Tell suppliers what you need and let them come to you.",
      cardTitle: "Post a request",
      action: "Post request",
      noun: "request",
      fields: [
        { name: "title", label: "What are you looking for?", required: true, maxlength: 150,
          placeholder: "e.g. 200 metres of cotton cloth" },
        { name: "requirement", label: "Requirement", type: TEXTAREA, required: true, maxlength: 5000,
          placeholder: "Quantity, quality, delivery timeline, where you need it." },
        { name: "budget", label: "Budget or quantity", maxlength: 150, placeholder: "e.g. 200 m, ₹80–100 per metre" },
      ],
      media: { label: "Specification", accept: "application/pdf,.pdf,image/*", text: "Upload a spec sheet (PDF or image)" },
    },
    proposal: {
      heading: "Proposals",
      subtitle: "Respond to a request with what you can supply and on what terms.",
      cardTitle: "Send a proposal",
      action: "Send proposal",
      noun: "proposal",
      fields: [
        { name: "title", label: "Proposal title", required: true, maxlength: 150,
          placeholder: "e.g. Cotton cloth supply — 200 m" },
        { name: "proposal", label: "Your proposal", type: TEXTAREA, required: true, maxlength: 8000,
          placeholder: "What you will supply, at what price, by when." },
        { name: "terms", label: "Payment terms", type: TEXTAREA, maxlength: 2000,
          placeholder: "e.g. 30% advance, balance before dispatch." },
      ],
      media: { label: "Quotation", accept: "application/pdf,.pdf,image/*", text: "Upload a quotation (PDF or image)" },
    },
    business_proposal: {
      heading: "Business Proposals",
      subtitle: "Propose a partnership, distributorship or joint venture.",
      cardTitle: "Send a business proposal",
      action: "Send proposal",
      noun: "business proposal",
      fields: [
        { name: "title", label: "Proposal title", required: true, maxlength: 150,
          placeholder: "e.g. Distributorship for Maharashtra" },
        { name: "proposal", label: "The proposal", type: TEXTAREA, required: true, maxlength: 8000,
          placeholder: "What you are proposing and what each side brings." },
        { name: "terms", label: "Commercial terms", type: TEXTAREA, maxlength: 2000,
          placeholder: "Margins, exclusivity, duration." },
      ],
      media: { label: "Deck or terms", accept: "application/pdf,.pdf,image/*", text: "Upload a deck (PDF or image)" },
    },
  };

  const STATE_LABEL = { new: ["att-warn", "not checked"], checking: ["att-checking", "checking…"],
                        ok: ["att-ok", "ok"], unchecked: ["att-warn", "not inspected"],
                        bad: ["att-bad", "blocked"] };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
  const KB = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

  function load() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
  }
  function save() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(items)); } catch { /* storage unavailable */ }
  }
  const items = load();            // {surfaceKey: [{id, title, status, reason, at, files}]}
  let current = null;              // key of the open surface, or null while Articles is showing
  let attachments = [];            // same shape as the Articles editor's

  // ---------------- tabs ----------------
  $("tabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (!btn) return;
    for (const t of $("tabs").querySelectorAll(".tab")) {
      t.classList.toggle("active", t === btn);
      if (t === btn) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
    }
    open(btn.dataset.surface, btn.textContent.trim());
  });

  /** Show one tab. "article" hands back to articles.js; "" is a tab this reference doesn't cover. */
  function open(key, label) {
    current = SURFACES[key] ? key : null;
    $("articles-view").hidden = key !== "article";
    $("surface-view").hidden = key === "article";
    if (key === "article") return;
    attachments = [];
    $("surface-view").innerHTML = current ? surfaceHtml(current) : placeholderHtml(label);
    if (current) wire();
  }

  function placeholderHtml(label) {
    return `<h1>${esc(label)}</h1>
      <p class="subtitle">Not part of this moderation reference.</p>
      <section class="card"><div class="empty">
        <h3>Nothing to moderate here</h3>
        <p>${esc(label)} has no member-written content, so it doesn't call the moderator.
           Try Add Business, Videos, Articles, Requests, Proposals or Business Proposals.</p>
      </div></section>`;
  }

  function surfaceHtml(key) {
    const s = SURFACES[key];
    const past = items[key] || [];
    return `<h1>${esc(s.heading)}</h1>
      <p class="subtitle">${esc(s.subtitle)}</p>
      <section class="card">
        <div class="card-head">
          <div>
            <h2>${esc(s.cardTitle)}</h2>
            <p class="quota">Checked by <b>POST /v1/moderate</b> as <b>content_type: ${esc(key)}</b></p>
          </div>
        </div>
        <div id="s-banner" aria-live="polite"></div>
        ${s.fields.map((f) => fieldHtml(key, f)).join("")}
        <div class="field">
          <label id="s-media-label">${esc(s.media.label)}</label>
          <div class="dropzone" id="s-dropzone" tabindex="0" role="button" aria-labelledby="s-media-label">
            <span>${esc(s.media.text)}</span>
          </div>
          <input type="file" id="s-media-input" multiple hidden accept="${esc(s.media.accept)}">
          <p class="hint">Attachments are checked before publishing. Text inside an image or PDF is
             read; the picture itself isn't classified, and video isn't watched.</p>
          <ul class="attachments" id="s-attachments"></ul>
        </div>
        <div class="form-actions">
          <button class="btn btn-outline" id="s-clear">Clear</button>
          <button class="btn btn-primary" id="s-submit">${esc(s.action)}</button>
        </div>
      </section>
      <section class="card" style="margin-top:16px">
        <div class="card-head"><h2>Submitted</h2></div>
        <div id="s-list">${past.length ? `<div class="list">${past.map(rowHtml).join("")}</div>` : emptyHtml()}</div>
      </section>`;
  }

  function fieldHtml(key, f) {
    const common = `class="input" id="s-${f.name}" maxlength="${f.maxlength || 5000}" placeholder="${esc(f.placeholder || "")}"`;
    const control = f.type === TEXTAREA
      ? `<textarea ${common} rows="4"></textarea>`
      : `<input ${common} autocomplete="off">`;
    return `<div class="field">
        <label for="s-${f.name}">${esc(f.label)}${f.required ? '<span class="req">*</span>' : ""}</label>
        ${control}
        <div id="s-${f.name}-msg"></div>
      </div>`;
  }

  function emptyHtml() {
    return `<div class="empty"><h3>Nothing submitted yet</h3>
      <p>What you publish here is listed with the moderator's decision.</p></div>`;
  }

  function rowHtml(it) {
    const label = { published: "Published", rejected: "Rejected", needs_changes: "Needs changes" }[it.status];
    const cls = { published: "published", rejected: "rejected", needs_changes: "needs_changes" }[it.status];
    const date = new Date(it.at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    const files = (it.files || []).length
      ? `<div class="row-reason">${it.files.map((f) => `${esc(f.name)} — ${esc(f.detail)}`).join(" · ")}</div>` : "";
    return `<div class="row">
        <div class="row-main">
          <p class="row-title">${esc(it.title || "Untitled")}</p>
          <div class="row-meta"><span class="badge ${cls}">${label}</span>${date}</div>
          ${it.reason ? `<div class="row-reason ${it.status === "rejected" ? "danger" : ""}">${esc(it.reason)}</div>` : ""}
          ${files}
        </div>
        <div class="row-actions">
          <button class="btn btn-outline" data-delete="${it.id}">Delete</button>
        </div>
      </div>`;
  }

  // ---------------- events inside an open surface ----------------
  function wire() {
    const drop = $("s-dropzone"), input = $("s-media-input");
    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
    input.addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });

    $("s-attachments").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-remove]");
      if (!btn) return;
      attachments.splice(Number(btn.dataset.remove), 1);
      renderAttachments();
    });

    $("s-submit").addEventListener("click", submit);
    $("s-clear").addEventListener("click", () => open(current, ""));
    $("s-list").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-delete]");
      if (!btn) return;
      items[current] = (items[current] || []).filter((x) => x.id !== btn.dataset.delete);
      save();
      renderList();
    });
  }

  function addFiles(fileList) {
    for (const file of fileList) attachments.push({ name: file.name, size: file.size, file, state: "new", detail: "" });
    renderAttachments();
    checkAttachments();
  }

  function renderAttachments() {
    $("s-attachments").innerHTML = attachments.map((a, i) => {
      const [cls, label] = STATE_LABEL[a.state] || STATE_LABEL.new;
      return `<li>
          <span class="att-state ${cls}">${label}</span>
          <span class="grow"><span class="name">${esc(a.name)}</span>
            <div class="meta">${KB(a.size)}${a.detail ? ` · ${esc(a.detail)}` : ""}</div></span>
          <button class="att-remove" data-remove="${i}" aria-label="Remove ${esc(a.name)}">✕</button>
        </li>`;
    }).join("");
  }

  /** Check every attachment not checked yet, with this surface's content_type on each. */
  async function checkAttachments() {
    const pending = attachments.filter((a) => a.state === "new" && a.file);
    await Promise.all(pending.map(async (a) => {
      a.state = "checking"; a.detail = ""; renderAttachments();
      try {
        const result = await M.moderateFile(a.file, { contentType: current });
        const media = result.media || {};
        if (result.decision !== "allow") {
          a.state = "bad";
          a.detail = result.feedback?.message || "Can't be published.";
        } else if (media.text_found === true && media.text_readable === false) {
          // There is writing here that OCR could not read, and the deployment publishes those
          // anyway (media.allow_unreadable_image). Nothing in it has been checked, so it must
          // not get a tick - this is what let a Devanagari threat through looking clean.
          a.state = "unchecked";
          a.detail = "there is writing here the moderator couldn't read";
        } else if (media.visual_content_checked === false && media.text_found === false) {
          // Allowed, but nothing in it could be read. Say so rather than showing a tick.
          a.state = "unchecked";
          a.detail = media.kind === "video" ? "video isn't watched by the moderator"
                                            : "no text found; the picture itself isn't checked";
        } else {
          a.state = "ok";
          a.detail = media.kind === "pdf" ? `${media.page_count} page${media.page_count === 1 ? "" : "s"} read`
                                          : "text read and checked";
        }
      } catch (e) {
        a.state = "bad";
        a.detail = e.message;
      }
      renderAttachments();
    }));
  }

  function renderList() {
    const past = items[current] || [];
    $("s-list").innerHTML = past.length ? `<div class="list">${past.map(rowHtml).join("")}</div>` : emptyHtml();
  }

  // ---------------- publish ----------------
  async function submit() {
    const s = SURFACES[current];
    clearMarks();
    const values = s.fields.map((f) => ({ name: f.name, text: $(`s-${f.name}`).value }));
    const missing = s.fields.filter((f) => f.required && !$(`s-${f.name}`).value.trim());
    if (missing.length) {
      missing.forEach((f) => { $(`s-${f.name}`).classList.add("has-issue");
                               $(`s-${f.name}-msg`).innerHTML = `<div class="field-error">${esc(f.label)} is required.</div>`; });
      return;
    }

    const btn = $("s-submit");
    const label = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>Checking…`;
    try {
      // Attachments first: a post goes out as a whole, so a blocked picture stops it just as a
      // blocked sentence does.
      await checkAttachments();
      const blocked = attachments.filter((a) => a.state === "bad");
      if (blocked.length) {
        banner("error", "Some attachments can't be published",
               `${blocked.map((a) => a.name).join(", ")} — remove or replace ${blocked.length === 1 ? "it" : "them"}, then try again.`);
        return;
      }

      const { result, parts, content } = await M.moderateFields(values, { contentType: current });
      if (result.decision === "allow") {
        publish(values, result);
        return;
      }
      showFeedback(result, parts, content);
      record(values, result);
    } catch (e) {
      // Fail closed: if the moderator can't be reached, nothing is published.
      banner("error", "Couldn't check this right now", `${e.message} Nothing was published.`);
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  function publish(values, result) {
    record(values, result);
    banner("ok", `Your ${SURFACES[current].noun} was published`, "It passed the moderator and is listed below.");
    for (const { name } of values) $(`s-${name}`).value = "";
    attachments = [];
    renderAttachments();
  }

  function record(values, result) {
    const fb = result.feedback || {};
    const status = result.decision === "allow" ? "published"
                 : result.decision === "reject" ? "rejected" : "needs_changes";
    (items[current] = items[current] || []).unshift({
      id: uid(),
      title: (values[0]?.text || "").trim(),
      status,
      reason: status === "published" ? "" : (fb.message || ""),
      at: Date.now(),
      files: attachments.map((a) => ({ name: a.name, detail: STATE_LABEL[a.state][1] })),
    });
    save();
    renderList();
  }

  function banner(kind, head, text) {
    $("s-banner").innerHTML = `<div class="banner ${esc(kind)}" role="status">
        <h3>${esc(head)}</h3><p>${esc(text)}</p></div>`;
    $("s-banner").scrollIntoView({ block: "nearest" });
  }

  function clearMarks() {
    $("s-banner").innerHTML = "";
    for (const f of SURFACES[current].fields) {
      $(`s-${f.name}`).classList.remove("has-issue");
      $(`s-${f.name}-msg`).innerHTML = "";
    }
  }

  /** Show the decision, and mark each input the moderator wants rewritten. */
  function showFeedback(result, parts, content) {
    const fb = result.feedback || {};
    const issues = M.mapIssues(fb, parts, content);
    const s = SURFACES[current];
    const labelOf = (name) => (s.fields.find((f) => f.name === name) || {}).label || name;
    const reject = result.decision === "reject";

    $("s-banner").innerHTML = `<div class="banner ${reject ? "reject" : "revise"}" role="alert">
        <h3>${reject ? "⛔" : "✎"} ${esc(fb.title || `Your ${s.noun} can't be published`)}</h3>
        <p>${esc(fb.message || "")}</p>
        ${issues.length ? `<ol class="issues">${issues.map((i) => issueHtml(i, labelOf)).join("")}</ol>` : ""}
      </div>`;

    for (const i of issues) {
      const input = $(`s-${i.field}`), msg = $(`s-${i.field}-msg`);
      if (!input) continue;
      input.classList.add("has-issue");
      msg.innerHTML = `<div class="field-hint">${esc(i.message)}</div>`;
    }
    $("s-banner").scrollIntoView({ block: "nearest" });
  }

  function issueHtml(i, labelOf) {
    let quote = esc(i.text);
    if (i.words && i.words.length) {          // mark the trigger words inside the quoted sentence
      let html = "", pos = i.start;
      for (const w of i.words) {
        html += esc(i.text.slice(pos - i.start, w.start - i.start)) + `<b class="trigger">${esc(w.text)}</b>`;
        pos = w.end;
      }
      quote = html + esc(i.text.slice(pos - i.start));
    }
    return `<li><span class="where">${esc(labelOf(i.field))}</span>` +
           `<span class="quote">“${quote}”</span>: ${esc(i.message)}</li>`;
  }
})();
