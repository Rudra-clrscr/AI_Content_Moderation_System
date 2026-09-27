/*
 * Articles tab + "Write article" modal with publish-time moderation.
 *
 *   Publish -> POST /v1/moderate (title + body + link URLs, content_type "article")
 *     allow  -> published immediately (uses one free publish)
 *     revise -> not published; the modal stays open with the parts to fix highlighted.
 *               The article is kept under "Needs changes" so the author can come back to it.
 *     reject -> not published; the reason (policy area) is shown. Kept under "Rejected".
 *   Save as Draft -> no moderation (drafts aren't public).
 *
 * Only successful publishes use up the free quota. Articles are stored in this
 * browser (localStorage) because this is a front-end reference, not the platform.
 */
(function () {
  "use strict";
  const M = window.BoncModeration;
  const FREE_PUBLISHES = 2;
  const MAX_BODY = 20000;
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
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || { articles: [], used: 0 }; }
    catch { return { articles: [], used: 0 }; }
  }
  function save() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch { /* storage unavailable: keep in memory */ }
  }
  const state = load();
  let tab = "published";
  let editing = null;        // article being edited (object), or null for a new one
  let feedbackState = null;  // {issues, serialized} while moderation feedback is on screen

  // ---------------- list ----------------
  function render() {
    const left = Math.max(0, FREE_PUBLISHES - state.used);
    $("quota").innerHTML = `<b>${left}</b> of ${FREE_PUBLISHES} free article publishes left`;
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
      reason = `<div class="row-reason danger">${esc(fb.message || "Can't be published.")}</div>`;
    }
    return `<div class="row">
        <div class="row-main">
          <p class="row-title">${esc(a.title || "Untitled")}</p>
          <div class="row-meta"><span class="badge ${a.status}">${label}</span>${esc(a.type)} · ${esc(a.visibility)} · ${date}</div>
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
    $("modal-title").textContent = article ? "Edit article" : "Write article";
    $("title").value = article?.title || "";
    body.innerHTML = article?.bodyHtml || "";
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
      visibility: $("visibility").value, status, updatedAt: now, ...extra,
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

  $("publish-btn").addEventListener("click", publish);

  async function publish() {
    if (!validate()) return;
    const alreadyPublished = editing?.status === "published";
    if (!alreadyPublished && state.used >= FREE_PUBLISHES) {
      showError("No free article publishes left", "You've used your free publishes. Save this article as a draft for now.");
      return;
    }
    const btn = $("publish-btn");
    const label = btn.textContent;
    btn.disabled = true; $("draft-btn").disabled = true;
    btn.innerHTML = `<span class="spinner"></span>Checking…`;
    clearFeedback();

    const serialized = M.serializeEditor(body);
    try {
      const { result, parts, content } = await M.moderateArticle({
        title: $("title").value, bodyText: serialized.text, links: M.editorLinks(body), contentId: editing?.id,
      });
      const moderation = { result, parts, content, decidedAt: result.decided_at };
      if (result.status === "pending") {
        showError("Still checking", "Your article is being checked. Please try publishing again in a moment.");
      } else if (result.decision === "allow") {
        if (!alreadyPublished) state.used += 1;
        snapshot("published", { moderation, publishedAt: Date.now() });
        closeModal(); tab = "published"; render();
        toast(alreadyPublished ? "Article updated" : "Article published");
      } else {
        // An already-published article stays live as it was; only the attempted edit is rejected.
        if (!alreadyPublished) {
          snapshot(result.decision === "revise" ? "needs_changes" : "rejected", { moderation });
          render();
        }
        showFeedback(result, parts, content, serialized);
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
    if (window.CSS && CSS.highlights) { CSS.highlights.delete("mod-revise"); CSS.highlights.delete("mod-focus"); }
    $("title").classList.remove("has-issue");
    $("title-msg").innerHTML = "";
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

  function showFeedback(result, parts, content, serialized) {
    const fb = result.feedback || {};
    const issues = M.mapIssues(fb, parts, content);
    feedbackState = { issues, serialized, stale: false };
    const where = { title: "Title", body: "Body", link: "Link" };

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
    const titleIssues = issues.filter((i) => i.field === "title");
    if (titleIssues.length) {
      $("title").classList.add("has-issue");
      $("title-msg").innerHTML = titleIssues.map((i) => `<div class="field-hint">${esc(i.message)}</div>`).join("");
    }
    $("banner").scrollIntoView({ block: "nearest" });
  }

  function issueHtml(i, k, where) {
    const show = i.field === "link" ? "" : ` <button class="btn-link" data-issue="${k}">Show</button>`;
    const quote = i.field === "link" ? esc(i.url) : esc(i.text);
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
