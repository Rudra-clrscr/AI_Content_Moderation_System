/*
 * Articles tab + "Write article" modal with publish-time moderation.
 *
 *   Publish -> POST /v1/moderate (title + body + link URLs, content_type "article")
 *     allow  -> published immediately
 *     revise / reject -> not published. The parts to fix are highlighted in place, and the
 *               "before you post" popup (moderation-client.js showDecision) shows the
 *               moderation rating and what was found. Dismissing it leaves the modal open on
 *               the first issue, and the article is kept under "Needs changes" (revise) or
 *               "Rejected" (reject) so the author can come back, rewrite and publish again.
 *   Save as Draft -> no moderation (drafts aren't public).
 *
 * browser (localStorage) because this is a front-end reference, not the platform.
 */
(function () {
  "use strict";
  const M = window.BoncModeration;
  const MAX_BODY = 20000;
  const MAX_TAGS = 10;
  const STORE_KEY = "bonc-articles-demo-v1";
  const TABS = [
    { id: "published", label: "Published" },
    { id: "draft", label: "Drafts" },
    { id: "needs_changes", label: "Needs changes" },
    { id: "rejected", label: "Rejected" },
  ];
  const EMPTY = {
    published: ["No articles yet", "Write your first article to share it with the BONC Network community.", true],
    draft: ["No drafts", "Articles you save as drafts appear here. Drafts aren't checked or published.", true],
    needs_changes: ["Nothing needs changes right now", "If an article needs edits before it can be published, it's kept here with the parts to fix highlighted.", false],
    rejected: ["No rejected articles", "Articles that can't be published are listed here with the reason.", false],
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));

  // ---------------- storage ----------------
  function load() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || { articles: [] }; }
    catch { return { articles: [] }; }
  }
  function save() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch { /* storage unavailable: keep in memory */ }
  }
  const state = load();
  let tab = "published";
  let editing = null;        // article being edited (object), or null for a new one
  let feedbackState = null;  // {issues, serialized} while moderation feedback is on screen
  // Attachments in the open modal: {name, size, kind, file, state, detail}. `state` is
  // "new" | "checking" | "ok" | "unchecked" | "bad" — publishing needs every one of them
  // past the moderator, or the article's media becomes the way round it.
  let attachments = [];
  let hashtags = [];

  // ---------------- list ----------------
  function render() {
    $("pills").innerHTML = TABS.map((t) => {
      const n = state.articles.filter((a) => a.status === t.id).length;
      return `<button class="pill ${t.id === tab ? "active" : ""}" role="tab" aria-selected="${t.id === tab}" data-tab="${t.id}">` +
        `${t.label}${n ? `<span class="count">(${n})</span>` : ""}</button>`;
    }).join("");

    const items = state.articles.filter((a) => a.status === tab).sort((a, b) => b.updatedAt - a.updatedAt);
    if (!items.length) {
      const [head, text, cta] = EMPTY[tab];
      $("list").innerHTML = `<div class="empty">
          <div class="empty-icon"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/></svg></div>
          <h3>${esc(head)}</h3><p>${esc(text)}</p>
          ${cta ? `<button class="btn btn-primary" data-action="new">Write Article</button>` : ""}
        </div>`;
      return;
    }
    $("list").innerHTML = `<div class="list">${items.map(rowHtml).join("")}</div>`;
  }

  function rowHtml(a) {
    const label = { published: "Published", draft: "Draft", needs_changes: "Needs changes", rejected: "Rejected" }[a.status];
    const date = new Date(a.updatedAt).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    let reason = "";
    const fb = a.moderation?.result?.feedback;
    if (a.status === "needs_changes" && fb) {
      const n = (fb.issues || []).length;
      reason = `<div class="row-reason">${n} ${n === 1 ? "part needs" : "parts need"} changes before this can be published.</div>`;
    } else if (a.status === "rejected" && fb) {
      const n = (fb.issues || []).length;
      const rewrite = n ? `${n} ${n === 1 ? "part" : "parts"} to rewrite. ` : "";
      reason = `<div class="row-reason danger">${rewrite}${esc(fb.message || "Can't be published.")}</div>`;
    }
    return `<div class="row">
        <div class="row-main">
          <p class="row-title">${esc(a.title || "Untitled")}</p>
          <div class="row-meta"><span class="badge ${a.status}">${label}</span>${esc(a.type)} · ${esc(a.visibility)} · ${date}</div>
          ${(a.hashtags || []).length ? `<div class="row-tags">${a.hashtags.map((t) => `<span>#${esc(t)}</span>`).join("")}</div>` : ""}
          ${reason}
        </div>
        <div class="row-actions">
          <button class="btn btn-outline" data-action="edit" data-id="${a.id}">${a.status === "published" ? "Edit" : "Open"}</button>
          <button class="btn btn-outline" data-action="delete" data-id="${a.id}">Delete</button>
        </div>
      </div>`;
  }

  $("pills").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) { tab = b.dataset.tab; render(); }
  });
  $("list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-action]");
    if (!b) return;
    const a = state.articles.find((x) => x.id === b.dataset.id);
    if (b.dataset.action === "new") openModal(null);
    if (b.dataset.action === "edit" && a) openModal(a);
    if (b.dataset.action === "delete" && a && confirm(`Delete "${a.title || "Untitled"}"?`)) {
      state.articles = state.articles.filter((x) => x.id !== a.id);
      save(); render(); toast("Article deleted");
    }
  });
  $("write-btn").addEventListener("click", () => openModal(null));

  // ---------------- modal ----------------
  const body = $("body");

  function openModal(article) {
    editing = article;
    attachments = (article?.attachments || []).map((a) => ({ ...a, file: null }));
    renderAttachments();
    $("modal-title").textContent = article ? "Edit article" : "Write article";
    $("title").value = article?.title || "";
    body.innerHTML = article?.bodyHtml || "";
    hashtags = [...(article?.hashtags || [])];
    renderTags();
    tagInput.value = "";
    $("type").value = article?.type || "Case Study";
    $("visibility").value = article?.visibility || "Short Description";
    $("publish-btn").textContent = article?.status === "published" ? "Update" : "Publish";
    clearFeedback();
    clearErrors();
    updateCounter();
    $("modal").hidden = false;
    // Re-show stored feedback when reopening an article that needs changes / was rejected.
    const mod = article?.moderation;
    if (mod && (article.status === "needs_changes" || article.status === "rejected")) {
      const ser = M.serializeEditor(body);
      if (M.buildArticleContent({ title: $("title").value, bodyText: ser.text, links: M.editorLinks(body) }).content === mod.content) {
        showFeedback(mod.result, mod.parts, mod.content, ser);
      }
    }
    $("title").focus();
  }

  function closeModal() {
    $("modal").hidden = true;
    clearFeedback();
    editing = null;
  }
  $("close-btn").addEventListener("click", closeModal);
  $("cancel-btn").addEventListener("click", closeModal);
  $("modal").addEventListener("mousedown", (e) => { if (e.target === $("modal")) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("modal").hidden) closeModal(); });

  // ---------------- editor ----------------
  document.querySelector(".toolbar").addEventListener("mousedown", (e) => {
    if (e.target.closest("button")) e.preventDefault();   // keep the text selection
  });
  document.querySelector(".toolbar").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    body.focus();
    if (b.dataset.cmd) document.execCommand(b.dataset.cmd, false, null);
    if (b.dataset.block) {
      const current = (document.queryCommandValue("formatBlock") || "").toLowerCase();
      document.execCommand("formatBlock", false, current === b.dataset.block ? "p" : b.dataset.block);
    }
    if (b.hasAttribute("data-link")) {
      const url = (prompt("Link URL") || "").trim();
      if (url && !/^\s*(javascript|data|vbscript):/i.test(url)) document.execCommand("createLink", false, url);
    }
    onEdit();
  });
  body.addEventListener("paste", (e) => {        // plain text only: no pasted scripts or styles
    e.preventDefault();
    document.execCommand("insertText", false, (e.clipboardData || window.clipboardData).getData("text/plain"));
  });
  body.addEventListener("input", onEdit);
  $("title").addEventListener("input", onEdit);

  function bodyLength() { return M.serializeEditor(body).text.length; }
  function updateCounter() {
    const n = bodyLength();
    $("counter").textContent = `${n} / ${MAX_BODY}`;
    $("counter").classList.toggle("over", n > MAX_BODY);
    return n;
  }
  function onEdit() {
    updateCounter();
    clearErrors();
    if (feedbackState && !feedbackState.stale) {      // offsets no longer match the text
      feedbackState.stale = true;
      clearHighlights();
      const note = document.createElement("p");
      note.className = "stale";
      note.textContent = "You've edited your article. Publish again to re-check it.";
      $("banner").querySelector(".banner")?.appendChild(note);
    }
  }

  // ---------------- validation ----------------
  function clearErrors() {
    $("title-msg").innerHTML = "";
    $("body-msg").innerHTML = "";
  }
  function validate() {
    clearErrors();
    let ok = true;
    if (!$("title").value.trim()) { $("title-msg").innerHTML = `<div class="field-error">Title is required.</div>`; ok = false; }
    const n = bodyLength();
    if (!n) { $("body-msg").innerHTML = `<div class="field-error">Body is required.</div>`; ok = false; }
    if (n > MAX_BODY) { $("body-msg").innerHTML = `<div class="field-error">Body is over ${MAX_BODY} characters.</div>`; ok = false; }
    return ok;
  }

  // ---------------- save / publish ----------------
  function snapshot(status, extra = {}) {
    const now = Date.now();
    const a = editing || { id: uid(), createdAt: now };
    Object.assign(a, {
      title: $("title").value.trim(), bodyHtml: body.innerHTML, type: $("type").value,
      visibility: $("visibility").value, hashtags: [...hashtags], status, updatedAt: now, ...extra,
    });
    if (!state.articles.includes(a)) state.articles.push(a);
    editing = a;
    save();
    return a;
  }

  $("draft-btn").addEventListener("click", () => {
    if (!$("title").value.trim() && !bodyLength()) { closeModal(); return; }
    snapshot("draft", { moderation: null });
    closeModal(); tab = "draft"; render(); toast("Saved as draft");
  });


  // ---------------- hashtags ----------------
  // LinkedIn-style: what you type is committed on Enter, comma or space, normalised to a single
  // word and shown as a removable chip. The tags go to the moderator with the rest of the
  // article (moderation-client.js buildArticleContent), so a scam tag stops a publish.
  const tagInput = $("hashtag-input");

  function renderTags() {
    $("tag-list").innerHTML = hashtags.map((t, i) => `<li class="tag">
        <span>#${esc(t)}</span>
        <button type="button" data-tag="${i}" aria-label="Remove #${esc(t)}">\u00d7</button>
      </li>`).join("");
    tagInput.placeholder = hashtags.length >= MAX_TAGS ? "Tag limit reached" : "Add a hashtag and press Enter";
    tagInput.disabled = hashtags.length >= MAX_TAGS;
  }

  function addTag(raw) {
    const tag = M.normalizeHashtag(raw);
    if (!tag) return false;
    // Case-insensitive duplicates, the way a reader would see them.
    if (hashtags.some((t) => t.toLowerCase() === tag.toLowerCase())) return false;
    if (hashtags.length >= MAX_TAGS) return false;
    hashtags.push(tag);
    renderTags();
    return true;
  }

  tagInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === "," || e.key === " ") {
      e.preventDefault();
      if (tagInput.value.trim()) { addTag(tagInput.value); tagInput.value = ""; }
    } else if (e.key === "Backspace" && !tagInput.value && hashtags.length) {
      hashtags.pop(); renderTags();
    }
  });
  // Committing on blur too, so a tag typed and left behind is not silently dropped on publish.
  tagInput.addEventListener("blur", () => {
    if (tagInput.value.trim()) { addTag(tagInput.value); tagInput.value = ""; }
  });
  // Pasting "a, b, c" adds three tags.
  tagInput.addEventListener("paste", (e) => {
    const text = (e.clipboardData || window.clipboardData).getData("text");
    if (!/[,\s]/.test(text)) return;
    e.preventDefault();
    text.split(/[,\s]+/).forEach(addTag);
    tagInput.value = "";
  });
  $("tag-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tag]");
    if (!b) return;
    hashtags.splice(Number(b.dataset.tag), 1);
    renderTags();
    tagInput.focus();
  });

  // ---------------- article media ----------------
  const dropzone = $("dropzone");
  const mediaInput = $("media-input");

  dropzone.addEventListener("click", () => mediaInput.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); mediaInput.click(); }
  });
  ["dragenter", "dragover"].forEach((ev) => dropzone.addEventListener(ev, (e) => {
    e.preventDefault(); dropzone.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((ev) => dropzone.addEventListener(ev, (e) => {
    e.preventDefault(); dropzone.classList.remove("over");
  }));
  dropzone.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
  mediaInput.addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });

  function addFiles(fileList) {
    for (const file of fileList) {
      attachments.push({ name: file.name, size: file.size, file, state: "new", detail: "" });
    }
    renderAttachments();
    checkAttachments();
  }

  $("attachments").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-remove]");
    if (!btn) return;
    attachments.splice(Number(btn.dataset.remove), 1);
    renderAttachments();
  });

  const KB = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  const STATE_LABEL = { new: ["att-warn", "not checked"], checking: ["att-checking", "checking…"],
                        ok: ["att-ok", "ok"], unchecked: ["att-warn", "not inspected"],
                        bad: ["att-bad", "blocked"] };

  function renderAttachments() {
    $("attachments").innerHTML = attachments.map((a, i) => {
      const [cls, label] = STATE_LABEL[a.state] || STATE_LABEL.new;
      return `<li>
          <span class="att-state ${cls}">${label}</span>
          <span class="grow"><span class="name">${esc(a.name)}</span>
            <div class="meta">${KB(a.size)}${a.detail ? ` · ${esc(a.detail)}` : ""}</div></span>
          <button class="att-remove" data-remove="${i}" aria-label="Remove ${esc(a.name)}">✕</button>
        </li>`;
    }).join("");
  }

  /** Check every attachment that hasn't been checked yet. Resolves when all are settled. */
  async function checkAttachments() {
    const pending = attachments.filter((a) => a.state === "new" && a.file);
    await Promise.all(pending.map(async (a) => {
      a.state = "checking"; a.detail = ""; renderAttachments();
      try {
        const result = await M.moderateFile(a.file, { contentId: editing?.id });
        const media = result.media || {};
        if (result.decision !== "allow") {
          a.state = "bad";
          a.detail = firstIssue(result) || (result.feedback?.message ?? "Can't be published.");
        } else if (media.text_found === true && media.text_readable === false) {
          // There is writing here that OCR could not read, and the deployment publishes those
          // anyway (media.allow_unreadable_image). Nothing in it has been checked, so it must
          // not get a tick - this is what let a Devanagari threat through looking clean.
          a.state = "unchecked";
          a.detail = "there is writing here the moderator couldn't read";
        } else if (media.visual_content_checked === false && media.text_found === false) {
          // Allowed, but nothing in it could actually be read. Say so rather than showing a tick.
          a.state = "unchecked";
          a.detail = media.kind === "video" ? "video isn't checked by the moderator"
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

  /** Attachment records for storage. The File objects themselves aren't kept: this page is a
   *  front-end reference with no upload service behind it, so reopening re-checks them. */
  function storedAttachments() {
    return attachments.map(({ name, size, state, detail }) => ({ name, size, state, detail }));
  }

  function firstIssue(result) {
    const issue = (result.feedback?.issues || [])[0];
    if (!issue) return "";
    const quoted = (result.content || "").slice(issue.start, issue.end).trim();
    return quoted ? `“${quoted.slice(0, 70)}”` : issue.message;
  }

  $("publish-btn").addEventListener("click", publish);

  async function publish() {
    if (!validate()) return;
    const alreadyPublished = editing?.status === "published";
    const btn = $("publish-btn");
    const label = btn.textContent;
    btn.disabled = true; $("draft-btn").disabled = true;
    btn.innerHTML = `<span class="spinner"></span>Checking…`;
    clearFeedback();

    const serialized = M.serializeEditor(body);
    try {
      // Attachments first: an article is published as a whole, so a blocked picture must stop
      // it just as a blocked sentence does. Nothing is published while any of them is unchecked.
      await checkAttachments();
      const blocked = attachments.filter((a) => a.state === "bad");
      if (blocked.length) {
        showError("Some attachments can't be published",
                  `${blocked.map((a) => a.name).join(", ")} — remove or replace ${blocked.length === 1 ? "it" : "them"}, then publish again.`);
        return;
      }

      const { result, parts, content } = await M.moderateArticle({
        title: $("title").value, bodyText: serialized.text, hashtags,
        links: M.editorLinks(body), contentId: editing?.id,
      });
      const moderation = { result, parts, content, decidedAt: result.decided_at };
      if (result.status === "pending") {
        showError("Still checking", "Your article is being checked. Please try publishing again in a moment.");
      } else if (result.decision === "allow") {
        snapshot("published", { moderation, publishedAt: Date.now(), attachments: storedAttachments() });
        closeModal(); tab = "published"; render();
        toast(alreadyPublished ? "Article updated" : "Article published");
      } else {
        // An already-published article stays live as it was; only the attempted edit is rejected.
        if (!alreadyPublished) {
          snapshot(result.decision === "revise" ? "needs_changes" : "rejected",
                   { moderation, attachments: storedAttachments() });
          render();
        }
        // The fields are marked first, so they are already waiting underneath the popup and the
        // author lands on the first one when they dismiss it.
        const issues = showFeedback(result, parts, content, serialized);
        M.showDecision({
          result, issues, noun: "article",
          labels: { title: "Title", body: "Body", link: "Link", hashtags: "Hashtags" },
          onClose: focusFirstIssue,
        });
      }
    } catch (e) {
      // Fail closed: nothing is published if the check couldn't run.
      showError("We couldn't check your article", `${e.message} Your article hasn't been published; please try again.`);
    } finally {
      btn.disabled = false; $("draft-btn").disabled = false; btn.textContent = label;
    }
  }

  // ---------------- feedback UI ----------------
  function clearHighlights() {
    if (window.CSS && CSS.highlights) { ["mod-revise", "mod-focus", "mod-word"].forEach((h) => CSS.highlights.delete(h)); }
    $("title").classList.remove("has-issue");
    $("title-msg").innerHTML = "";
    $("tag-box").classList.remove("has-issue");
    $("hashtags-msg").innerHTML = "";
  }
  function clearFeedback() {
    feedbackState = null;
    clearHighlights();
    $("banner").innerHTML = "";
  }
  function showError(head, text) {
    $("banner").innerHTML = `<div class="banner error"><h3>${esc(head)}</h3><p>${esc(text)}</p></div>`;
  }

  function linkRange(index) {
    const url = M.editorLinks(body)[index];
    const a = [...body.querySelectorAll("a[href]")].find((x) => x.getAttribute("href").trim() === url);
    if (!a) return null;
    const r = document.createRange();
    r.selectNodeContents(a);
    return r;
  }

  /** Render the decision in the modal and mark the inputs. Returns the mapped issues. */
  function showFeedback(result, parts, content, serialized) {
    const fb = result.feedback || {};
    const issues = M.mapIssues(fb, parts, content);
    feedbackState = { issues, serialized, stale: false };
    const where = { title: "Title", body: "Body", link: "Link", hashtags: "Hashtags" };

    if (result.decision === "reject") {
      $("banner").innerHTML = `<div class="banner reject" role="alert">
          <h3>⛔ ${esc(fb.title || "Your article can't be published")}</h3>
          <p>${esc(fb.message || "")}</p>
          ${issues.length ? `<ol class="issues">${issues.map((i, k) => issueHtml(i, k, where)).join("")}</ol>` : ""}
        </div>`;
    } else {
      $("banner").innerHTML = `<div class="banner revise" role="alert">
          <h3>✎ ${esc(fb.title || "Your article needs a few changes")}</h3>
          <p>${esc(fb.message || "")}</p>
          <ol class="issues">${issues.map((i, k) => issueHtml(i, k, where)).join("")}</ol>
        </div>`;
    }

    // Highlight in place: body/link ranges via the CSS Custom Highlight API, title via the input style.
    const ranges = issues.map((i) =>
      i.field === "body" ? M.rangeFor(serialized, i.start, i.end) : i.field === "link" ? linkRange(i.linkIndex) : null
    ).filter(Boolean);
    if (ranges.length && window.CSS && CSS.highlights && window.Highlight) CSS.highlights.set("mod-revise", new Highlight(...ranges));
    // Trigger words (word-by-word scan) get a stronger highlight inside their sentence.
    const wordRanges = issues.filter((i) => i.field === "body")
      .flatMap((i) => i.words.map((w) => M.rangeFor(serialized, w.start, w.end))).filter(Boolean);
    if (wordRanges.length && window.CSS && CSS.highlights && window.Highlight) CSS.highlights.set("mod-word", new Highlight(...wordRanges));
    const titleIssues = issues.filter((i) => i.field === "title");
    if (titleIssues.length) {
      $("title").classList.add("has-issue");
      $("title-msg").innerHTML = titleIssues.map((i) => `<div class="field-hint">${esc(i.message)}</div>`).join("");
    }
    // Hashtags are marked at the field, not character by character: what the moderator read is
    // the words the tags spell, which is not the text in the box (see hashtagWords).
    const tagIssues = issues.filter((i) => i.field === "hashtags");
    if (tagIssues.length) {
      $("tag-box").classList.add("has-issue");
      $("hashtags-msg").innerHTML = tagIssues.map((i) => `<div class="field-hint">${esc(i.message)}</div>`).join("");
    }
    $("banner").scrollIntoView({ block: "nearest" });
    return issues;
  }

  /** Put the cursor where the first issue is, so "change the content" lands on the problem. */
  function focusFirstIssue() {
    const first = (feedbackState?.issues || [])[0];
    if (!first) return;
    if (first.field === "title") { $("title").focus(); return; }
    if (first.field === "hashtags") { $("hashtag-input").focus(); return; }
    const r = M.rangeFor(feedbackState.serialized, first.start, first.end);
    body.focus();
    if (r) {
      const sel = window.getSelection();
      sel.removeAllRanges(); sel.addRange(r);
      (r.startContainer.parentElement || body).scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  function issueHtml(i, k, where) {
    const noSelection = i.field === "link" || i.field === "hashtags";
    const show = noSelection ? "" : ` <button class="btn-link" data-issue="${k}">Show</button>`;
    let quote = i.field === "link" ? esc(i.url) : esc(i.text);
    if (i.field !== "link" && i.words && i.words.length) {
      // Bold the trigger words inside the quoted sentence (offsets are relative to the field).
      let html = "", pos = i.start;
      for (const w of i.words) {
        html += esc(i.text.slice(pos - i.start, w.start - i.start)) + `<b class="trigger">${esc(w.text)}</b>`;
        pos = w.end;
      }
      quote = html + esc(i.text.slice(pos - i.start));
    }
    return `<li><span class="where">${where[i.field]}</span><span class="quote">“${quote}”</span>: ${esc(i.message)}${show}</li>`;
  }

  $("banner").addEventListener("click", (e) => {
    const b = e.target.closest("[data-issue]");
    if (!b || !feedbackState || feedbackState.stale) return;
    const i = feedbackState.issues[Number(b.dataset.issue)];
    if (i.field === "title") {
      const input = $("title");
      const lead = input.value.length - input.value.trimStart().length;   // offsets are on the trimmed title
      input.focus();
      input.setSelectionRange(lead + i.start, lead + i.end);
      return;
    }
    const r = M.rangeFor(feedbackState.serialized, i.start, i.end);
    if (!r) return;
    body.focus();
    const sel = window.getSelection();
    sel.removeAllRanges(); sel.addRange(r);
    if (window.CSS && CSS.highlights && window.Highlight) CSS.highlights.set("mod-focus", new Highlight(r));
    (r.startContainer.parentElement || body).scrollIntoView({ block: "center", behavior: "smooth" });
  });

  // ---------------- toast ----------------
  let toastTimer;
  function toast(text) {
    $("toast").textContent = text;
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 2600);
  }

  render();
})();
