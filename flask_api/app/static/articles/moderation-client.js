/*
 * BONC moderation client.
 *
 * Framework-free and reusable: the platform can copy this file as-is. It covers every surface
 * in the dashboard, not just Articles —
 *
 *   moderateFields(...)  any form: Videos, Requests, Proposals, Business Proposals, Add Business
 *   moderateArticle(...) the Articles editor (title + rich-text body + link URLs)
 *   moderateFile(...)    one attachment: image, video or PDF
 *   showDecision(...)    the "before you post" popup: the moderation rating and what the
 *                        moderator found, over the marked-up form
 *
 * A surface is wired up by listing its inputs and blocking submit unless the result allows:
 *
 *   const {result, parts, content} = await BoncModeration.moderateFields(
 *       [{name: "title", text: title}, {name: "description", text: description}],
 *       {contentType: "video", contentId: id});
 *   if (result.decision === "allow") return publish();
 *   const issues = BoncModeration.mapIssues(result.feedback, parts, content);
 *   showIssues(issues);                                  // highlight the inputs in place
 *   BoncModeration.showDecision({result, issues, noun: "video", onClose: () => focusFirst()});
 *
 * It turns an article (title, rich-text body, link URLs) into ONE moderation
 * request, and maps the response's feedback offsets back onto those fields so
 * the editor can highlight exactly what the author needs to change.
 *
 * Content sent to POST /v1/moderate:
 *
 *     <title>\n\n<body as plain text>[\n\n<link url 1>\n<link url 2>...]
 *
 * Link URLs are included because they aren't part of the visible text: a scam
 * domain hidden behind "click here" must still be checked.
 *
 * Attachments are sent separately to POST /v1/moderate/media, one per request
 * (see moderateFile). An article is only published when the text and every
 * attachment are allowed.
 */
(function (global) {
  "use strict";

  const SEP = "\n\n";
  const BLOCKS = new Set(["P", "DIV", "H1", "H2", "H3", "H4", "LI", "UL", "OL", "BLOCKQUOTE", "PRE"]);

  /**
   * Plain text of a contenteditable element, plus a map from text offsets back to
   * DOM text nodes (needed to highlight feedback ranges without touching the DOM).
   * Block elements and <br> become "\n", like the author sees them.
   */
  function serializeEditor(root) {
    let text = "";
    const pieces = [];   // {node, start, end}
    const newline = () => { if (text && !text.endsWith("\n")) text += "\n"; };
    (function walk(node) {
      for (const child of node.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) {
          if (!child.nodeValue) continue;
          pieces.push({ node: child, start: text.length, end: text.length + child.nodeValue.length });
          text += child.nodeValue;
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          if (child.tagName === "BR") { text += "\n"; continue; }
          const block = BLOCKS.has(child.tagName);
          if (block) newline();
          walk(child);
          if (block) newline();
        }
      }
    })(root);
    const trimmed = text.replace(/\s+$/, "");
    return { text: trimmed, pieces: pieces.filter((p) => p.start < trimmed.length) };
  }

  /** Link URLs in the body, in document order (deduplicated). */
  function editorLinks(root) {
    const seen = new Set();
    return [...root.querySelectorAll("a[href]")]
      .map((a) => a.getAttribute("href").trim())
      .filter((h) => h && !seen.has(h) && seen.add(h));
  }

  /**
   * Join a form's fields into the single text the moderator scores, remembering where each
   * field lives in it so feedback can be mapped back to the input it came from.
   *
   * `fields` is an ordered list of {name, text}, so this works for any surface — an article's
   * title and body, a video's title and description, a request's requirement and budget, a
   * proposal's terms. Empty fields are skipped. Everything is checked together because harm
   * can be split across inputs: a clean title with the scam in the description is still a scam.
   *
   *   buildContent([{name: "title", text: t}, {name: "description", text: d}], urls)
   *     -> {content, parts: {fields: {title: [0, 12], description: [14, 96]}, links: [...]}}
   */
  function buildContent(fields, links = []) {
    const parts = { fields: {}, links: [] };
    let content = "";
    for (const { name, text } of fields) {
      const value = (text || "").trim();
      if (!value) continue;
      if (content) content += SEP;
      parts.fields[name] = [content.length, content.length + value.length];
      content += value;
    }
    if (links.length) {
      content += content ? SEP : "";
      links.forEach((url, i) => {
        if (i) content += "\n";
        parts.links.push({ index: i, url, range: [content.length, content.length + url.length] });
        content += url;
      });
    }
    return { content, parts };
  }

  /**
   * A hashtag as the words it is made of: "#FirstCopyRolex" -> "First Copy Rolex".
   *
   * Tags run words together, and the moderator reads sentences, so the joined form is close to
   * invisible to it. Measured on v5 with a clean article plus three tags:
   *
   *   tags                                  as typed        split into words
   *   #FirstCopyRolex #ReplicaWatches       allow 0.000     reject 1.000
   *   #EscortService #CallGirls             allow 0.001     reject 0.999
   *   #CottonSaris #Wholesale #Surat        allow 0.000     allow 0.000
   *
   * Leaving the "#" on is actively worse than dropping it (the counterfeit set scores 0.000
   * with the hashes kept, against 1.000 without), so what goes to the moderator is the words.
   * Only camelCase, digit and underscore boundaries can be split - "#firstcopyrolex" typed all
   * in lower case stays one word and is still weak. The title and body remain the main defence.
   */
  function hashtagWords(tag) {
    return String(tag).replace(/^#/, "")
      .replace(/_+/g, " ")
      .replace(/(?<=[a-z0-9])(?=[A-Z])/g, " ")
      .replace(/(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  /**
   * "#first_copy rolex!" -> "firstCopyRolex". Letters and digits only, like LinkedIn.
   *
   * Separators become camelCase humps instead of vanishing. Deleting them would collapse the tag
   * to "firstcopyrolex", and hashtagWords could no longer recover the words - which is the
   * difference between the moderator scoring that tag 1.000 and scoring it 0.000.
   */
  function normalizeHashtag(raw, maxLength = 60) {
    const words = String(raw).replace(/^#/, "").split(/[^\p{L}\p{N}]+/u).filter(Boolean);
    if (!words.length) return "";
    const joined = words.map((w, i) => (i ? w.charAt(0).toUpperCase() + w.slice(1) : w)).join("");
    return joined.slice(0, maxLength);
  }

  /** Build the single moderation text for an article. Thin wrapper over buildContent that also
   *  exposes the original `parts.title` / `parts.body` shape (contracts/moderation_result.md). */
  function buildArticleContent({ title, bodyText, hashtags = [], links = [] }) {
    // Hashtags are member-written text and are moderated with everything else, as the words they
    // spell rather than as the joined tags (see hashtagWords).
    const tagText = hashtags.map(hashtagWords).filter(Boolean).join(" ");
    const { content, parts } = buildContent(
      [{ name: "title", text: title }, { name: "body", text: bodyText },
       { name: "hashtags", text: tagText }], links);
    // The article editor highlights inside the body even when the title is empty, so these two
    // always exist, unlike the generic `fields` map which omits empty inputs.
    const t = (title || "").trim();
    parts.title = parts.fields.title || [0, t.length];
    parts.body = parts.fields.body || [content.length, content.length];
    return { content, parts };
  }

  /**
   * Map each feedback issue (offsets into the combined content) onto a field:
   *   {field: "title" | "body" | "link", start, end (relative to that field), linkIndex?, message, source, text,
   *    words: [{start, end, text}]}   // trigger words inside the issue (word-by-word scan), same field
   * An issue that spans the title/body separator is split into one entry per field.
   */
  function mapIssues(feedback, parts, content) {
    if (!feedback || !feedback.issues) return [];
    const out = [];
    const clip = (issue, field, [a, b], extra = {}) => {
      const s = Math.max(issue.start, a), e = Math.min(issue.end, b);
      const words = (issue.words || [])
        .filter(([ws, we]) => ws >= s && we <= e)
        .map(([ws, we]) => ({ start: ws - a, end: we - a, text: content.slice(ws, we) }));
      if (e > s) out.push({ field, start: s - a, end: e - a, message: issue.message, source: issue.source,
                            ruleId: issue.rule_id, text: content.slice(s, e), words, ...extra });
    };
    for (const issue of feedback.issues) {
      // Generic surfaces carry every input in `fields`; the article editor also has the
      // original title/body pair, which must not be reported twice.
      const named = parts.fields || { title: parts.title, body: parts.body };
      for (const [name, range] of Object.entries(named)) {
        if (range) clip(issue, name, range);
      }
      parts.links.forEach((l) => clip(issue, "link", l.range, { linkIndex: l.index, url: l.url }));
    }
    return out;
  }

  /** Turn [start, end) offsets in serializeEditor() text into a DOM Range (null if not mappable). */
  function rangeFor(serialized, start, end) {
    const { pieces } = serialized;
    const first = pieces.find((p) => p.end > start);
    let last = null;
    for (const p of pieces) if (p.start < end) last = p;
    if (!first || !last) return null;
    const range = document.createRange();
    range.setStart(first.node, Math.max(0, start - first.start));
    range.setEnd(last.node, Math.min(last.node.nodeValue.length, end - last.start));
    return range.collapsed ? null : range;
  }

  /**
   * Moderate an article. Resolves to {result, parts, content}.
   * Throws an Error with .code on HTTP/API errors (e.g. "model_not_ready", "network").
   */
  async function moderateArticle({ title, bodyText, hashtags = [], links = [], contentId, endpoint = "/v1/moderate" }) {
    const { content, parts } = buildArticleContent({ title, bodyText, hashtags, links });
    let resp;
    try {
      resp = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, content_type: "article", content_id: contentId }),
      });
    } catch (e) {
      throw Object.assign(new Error("Could not reach the moderation service."), { code: "network" });
    }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = body.error || {};
      throw Object.assign(new Error(err.message || `HTTP ${resp.status}`), { code: err.code || `http_${resp.status}` });
    }
    return { result: body, parts, content };
  }

  /**
   * Moderate one attachment. Resolves to the usual result payload plus a `media` block
   * ({kind, text_found, visual_content_checked, …}).
   *
   * Attachments go one per request so each gets its own decision and its own audit row.
   * Anything published with a post has to be checked, or the scam simply moves into the
   * picture — and what can be checked differs by kind, which is why the server answers
   * "refused" for video rather than pretending.
   */
  async function moderateFile(file, { contentId, contentType = "article", endpoint = "/v1/moderate/media" } = {}) {
    const form = new FormData();
    form.append("file", file);
    form.append("content_type", contentType);
    if (contentId) form.append("content_id", contentId);
    let resp;
    try {
      resp = await fetch(endpoint, { method: "POST", body: form });
    } catch (e) {
      throw Object.assign(new Error("Could not reach the moderation service."), { code: "network" });
    }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = body.error || {};
      throw Object.assign(new Error(err.message || `HTTP ${resp.status}`), { code: err.code || `http_${resp.status}` });
    }
    return body;
  }

  /**
   * Moderate any form. Resolves to {result, parts, content}; `parts.fields` maps each input
   * name to its range so mapIssues can point at the right box.
   *
   *   await moderateFields([{name: "title", text: title}, {name: "description", text: desc}],
   *                        {contentType: "video", contentId: id});
   *
   * `contentType` is one of the dashboard's surfaces (article, video, request, proposal,
   * business_proposal, business_profile, product_listing, advertisement, post). It doesn't
   * change how the text is scored — it selects the wording the author sees and is stored with
   * the decision — so send the one the member is actually using.
   *
   * Attachments go separately through moderateFile, one per file.
   */
  async function moderateFields(fields, { contentType = "post", links = [], contentId, endpoint = "/v1/moderate" } = {}) {
    const { content, parts } = buildContent(fields, links);
    let resp;
    try {
      resp = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, content_type: contentType, content_id: contentId }),
      });
    } catch (e) {
      throw Object.assign(new Error("Could not reach the moderation service."), { code: "network" });
    }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = body.error || {};
      throw Object.assign(new Error(err.message || `HTTP ${resp.status}`), { code: err.code || `http_${resp.status}` });
    }
    return { result: body, parts, content };
  }

  // ---------------- the "before you post" popup ----------------
  // Senior review (2026-10-04): tell the author what the moderator found, with a rating, in a
  // popup rather than only a banner they may scroll past. There is no "post anyway" - content
  // the moderator objects to is not published, and the only way on is to change it.
  //
  // The dialog builds its own DOM, so a surface needs no markup of its own: call showDecision
  // and handle onClose. Styling comes from .mod-ask / .mod-rating in articles.css.

  const RATING_TEXT = {
    low: "A small thing to look at before this goes out.",
    medium: "The moderator is not comfortable with part of this.",
    high: "The moderator is confident this breaks policy.",
  };
  let dialog = null;     // built on first use

  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function buildDialog() {
    const el = document.createElement("div");
    el.className = "backdrop mod-ask";
    el.id = "mod-ask";
    el.hidden = true;
    el.innerHTML = `
      <div class="modal modal-sm" role="dialog" aria-modal="true" aria-labelledby="mod-ask-title">
        <div class="modal-head">
          <h2 id="mod-ask-title">Before you post</h2>
          <button class="icon-btn" data-act="change" aria-label="Close">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>
          </button>
        </div>
        <div class="modal-body">
          <p class="mod-rating">Moderation rating <b data-el="severity">—</b>
            <span class="mod-rating-note" data-el="note"></span></p>
          <p class="mod-message" data-el="message"></p>
          <ol class="issues" data-el="issues"></ol>
        </div>
        <div class="modal-foot">
          <p class="mod-foot-note" data-el="footnote"></p>
          <button class="btn btn-primary" data-act="change">Change the content</button>
        </div>
      </div>`;
    document.body.appendChild(el);
    return el;
  }

  /**
   * Render one issue as a list item: which input it is in, the sentence quoted with its trigger
   * words marked, and the instruction.
   */
  function issueLi(issue, labelOf) {
    let quote = escapeHtml(issue.field === "link" ? issue.url : issue.text);
    if (issue.field !== "link" && issue.words && issue.words.length) {
      let html = "", pos = issue.start;
      for (const w of issue.words) {
        html += escapeHtml(issue.text.slice(pos - issue.start, w.start - issue.start)) +
                `<b class="trigger">${escapeHtml(w.text)}</b>`;
        pos = w.end;
      }
      quote = html + escapeHtml(issue.text.slice(pos - issue.start));
    }
    return `<li><span class="where">${escapeHtml(labelOf(issue.field))}</span>` +
           `<span class="quote">“${quote}”</span>: ${escapeHtml(issue.message)}</li>`;
  }

  /**
   * Show what the moderator found, over the form it has already marked up.
   *
   *   showDecision({result, issues, noun: "article", onClose: () => focusFirstIssue()});
   *
   * `issues` are mapIssues() entries (so the quoted text and trigger words are already mapped
   * onto the author's own inputs); `labels` renames the fields for display. `onClose` runs once,
   * whichever way the author dismisses it - the button, the close icon, Escape, or a click
   * outside - because there is only one thing to do next: change the content and try again.
   */
  function showDecision({ result, issues = [], noun = "post", labels = {}, onClose = () => {} }) {
    dialog = dialog || buildDialog();
    const fb = result.feedback || {};
    const severity = fb.severity || "medium";
    const el = (name) => dialog.querySelector(`[data-el="${name}"]`);
    const labelOf = (field) => labels[field] || field;

    dialog.querySelector("#mod-ask-title").textContent = fb.title || `This ${noun} can't be published`;
    el("severity").textContent = severity;
    el("severity").className = severity;
    el("note").textContent = RATING_TEXT[severity] || "";
    el("message").textContent = fb.message || "";
    el("issues").innerHTML = issues.map((i) => issueLi(i, labelOf)).join("");
    el("footnote").textContent = `This ${noun} hasn't been published.`;

    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      dialog.hidden = true;
      dialog.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey, true);
      onClose();
    };
    const onClick = (e) => {
      if (e.target === dialog || e.target.closest("[data-act]")) finish();
    };
    // Captured, so Escape closes this dialog and not the editor modal underneath it.
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      finish();
    };
    dialog.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey, true);
    dialog.hidden = false;
    dialog.querySelector('[data-act="change"].btn').focus();
  }

  global.BoncModeration = { serializeEditor, editorLinks, buildContent, buildArticleContent, mapIssues,
                            rangeFor, moderateArticle, moderateFields, moderateFile,
                            hashtagWords, normalizeHashtag, showDecision };
})(window);
