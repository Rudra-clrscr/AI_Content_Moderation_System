/*
 * BONC moderation client for Articles.
 *
 * Framework-free and reusable: the platform can copy this file as-is.
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

  /** Build the single moderation text and remember where each part lives in it. */
  function buildArticleContent({ title, bodyText, links = [] }) {
    const t = title.trim();
    let content = t + SEP + bodyText;
    const parts = { title: [0, t.length], body: [t.length + SEP.length, t.length + SEP.length + bodyText.length], links: [] };
    if (links.length) {
      content += SEP;
      links.forEach((url, i) => {
        if (i) content += "\n";
        parts.links.push({ index: i, url, range: [content.length, content.length + url.length] });
        content += url;
      });
    }
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
      clip(issue, "title", parts.title);
      clip(issue, "body", parts.body);
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
  async function moderateArticle({ title, bodyText, links = [], contentId, endpoint = "/v1/moderate" }) {
    const { content, parts } = buildArticleContent({ title, bodyText, links });
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

  global.BoncModeration = { serializeEditor, editorLinks, buildArticleContent, mapIssues, rangeFor, moderateArticle };
})(window);
