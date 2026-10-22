/* Admin: login, forgot password, moderation settings, and images policy/fine-tuning dashboard.
 *
 * Talks only to the backend (/v1/admin/...). The login is a cookie the browser keeps and
 * JavaScript cannot read (HttpOnly); no password, token or key is ever stored in the page,
 * localStorage or sessionStorage.
 */
(() => {
  "use strict";

  const LEVEL_TEXT = {
    lenient: "Less restrictive. Only clearly harmful content is refused.",
    medium: "Balanced. The project's standard policy.",
    strict: "Stricter. More content is refused or sent back for changes.",
    level_1: "Ultra strict. Zero tolerance - also refuses ordinary commercial friction and delivery complaints.",
    level_2: "Very strict. Also refuses minor dissatisfaction, supplier disputes and heated pricing remarks.",
    level_3: "Strict. Also refuses support outcries, emotional venting and public callouts of vendors.",
    level_4: "Firm. Also refuses unstructured grievances, hostile venting and aggressive haggling.",
    level_5: "Balanced baseline. Also refuses targeted abuse, disguised scams and off-platform payment schemes.",
    level_6: "Moderate. Also refuses direct harassment, counterfeit buzzwords and advance-fee schemes.",
    level_7: "Relaxed. Also refuses explicit insults aimed at named people or firms, and high-confidence scams.",
    level_8: "Lenient. Also refuses overt fraud, lottery scams and blatant harassment.",
    level_9: "Very lenient. Refuses confirmed fraud, extortion, illegal trade, hate speech and violent threats.",
    level_10: "Maximum permissive. Only the Layer 1 gate rules refuse anything - contraband, malicious URLs, slurs.",
  };
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const levelName = (s) => cap(String(s).replace(/_/g, " "));

  // THE HEAT BAR: the ten cumulative levels as a temperature scale, cold (strict) to hot
  // (permissive). `val` mirrors reject_min in config/moderation_levels.yaml and `name` is the
  // key the API takes, so the bar and the server cannot drift apart. The wording describes
  // what an admin will SEE published rather than the threshold behind it - nobody chooses a
  // moderation policy by reasoning about 0.40.
  const HEAT_LEVELS = [
    { level: 1, val: 0.10, name: "level_1", label: "Ultra Strict", icon: "❄️", color: "#2563eb",
      tone: "Ice Cold — Zero Tolerance",
      allowed: "Only sterile corporate profiles, clean product specifications, neutral B2B listings.",
      blocked: "Everything blocked in L2–L10 + any negative tone, commercial friction, or delivery complaints." },
    { level: 2, val: 0.20, name: "level_2", label: "Very Strict", icon: "🧊", color: "#06b6d4",
      tone: "Cold — High Scrutiny",
      allowed: "Professional business articles, case studies, formal RFQs, polite negotiation enquiries.",
      blocked: "Everything blocked in L3–L10 + minor dissatisfaction, supplier disputes, heated pricing remarks." },
    { level: 3, val: 0.30, name: "level_3", label: "Strict", icon: "🛡️", color: "#0d9488",
      tone: "Cool — Strict Protection",
      allowed: "Standard proposals, contract terms with penalty clauses, neutral market commentary.",
      blocked: "Everything blocked in L4–L10 + support outcries, emotional venting, public vendor callouts." },
    { level: 4, val: 0.40, name: "level_4", label: "Firm", icon: "🧭", color: "#15803d",
      tone: "Temperate — Firm Line",
      allowed: "Honest reviews with a reason, firm negotiation, disputes raised through the proper channel.",
      blocked: "Everything blocked in L5–L10 + unstructured grievances, hostile venting, aggressive haggling." },
    { level: 5, val: 0.50, name: "level_5", label: "Balanced", icon: "⚖️", color: "#65a30d",
      tone: "Balanced — Calibration Baseline",
      allowed: "Standard commercial friction, blunt vendor feedback, delivery delay discussions.",
      blocked: "Everything blocked in L6–L10 + targeted insults, off-platform payment schemes, disguised fraud." },
    { level: 6, val: 0.60, name: "level_6", label: "Moderate", icon: "🌤️", color: "#ca8a04",
      tone: "Mild — Moderate Filtering",
      allowed: "Strong opinions, unhappy reviews, robust public disagreement between members.",
      blocked: "Everything blocked in L7–L10 + direct harassment, counterfeit buzzwords, advance-fee schemes." },
    { level: 7, val: 0.70, name: "level_7", label: "Relaxed", icon: "🌞", color: "#ea580c",
      tone: "Warm — Relaxed",
      allowed: "Heated exchanges, sharp criticism of firms, unpolished language.",
      blocked: "Everything blocked in L8–L10 + explicit insults at named people or firms, high-confidence scams." },
    { level: 8, val: 0.80, name: "level_8", label: "Lenient", icon: "🔆", color: "#dc2626",
      tone: "Hot — Lenient",
      allowed: "Almost all member-to-member friction, including rude and dismissive posts.",
      blocked: "Everything blocked in L9 + overt fraud, lottery scams, blatant harassment." },
    { level: 9, val: 0.90, name: "level_9", label: "Very Lenient", icon: "🔥", color: "#b91c1c",
      tone: "Very Hot — Minimal Filtering",
      allowed: "Nearly everything a member writes, however blunt or unpleasant.",
      blocked: "Confirmed financial fraud, extortion, explicit illegal trade, hate speech, direct violent threats." },
    { level: 10, val: 1.00, name: "level_10", label: "Max Permissive", icon: "🌋", color: "#7f1d1d",
      tone: "Max Heat — Gate Rules Only",
      allowed: "Practically all text passes the AI model.",
      blocked: "Only the Layer 1 gate rules fire: illegal contraband, malicious URLs, slurs." },
  ];
  const HEAT_BY_NAME = Object.fromEntries(HEAT_LEVELS.map((c) => [c.name, c]));
  // Which bar position a saved level maps to. The named levels predate the bar and are still
  // valid server-side, so they land on their nearest numeric equivalent rather than being
  // treated as "no level" and silently reset to 5 on the next save.
  const NAMED_AT = { strict: 3, medium: 5, lenient: 8 };

  // The surfaces a member can publish from, named by READING THE NAV rather than by keeping a
  // second copy of its labels here. The nav carries data-surface on every tab, so the chips
  // say exactly what the tabs say and keep saying it after a tab is renamed. A hard-coded
  // list had already drifted: it offered "Posts", "Advertisements" and "Product Listings",
  // which are content types the pipeline judges but are not tabs and are not called that
  // anywhere in the product.
  //
  // The three with no tab still have to appear - they are real surfaces with real settings,
  // and leaving them out would hide a page's moderation level rather than show it. They are
  // shown in a group of their own with a line saying what they are, because a badge reading
  // "NO TAB" next to `product_listing` makes an admin explain the dashboard's internals
  // before they can explain the setting. contracts/moderation_result.md calls them "existing
  // surfaces": BONC publishes listings, ads and feed posts elsewhere, and this reference
  // dashboard only implements six of the tabs.
  //
  // Their names are BONC's own, from the `nouns` block in config/feedback_messages.yaml -
  // the words authors are already shown ("Your listing needs a few changes"). That file is
  // the source of truth; these three are copied because they have no nav entry to read.
  const SURFACE_ORDER = ["business_profile", "video", "article", "request", "proposal",
                         "business_proposal", "post", "advertisement", "product_listing"];
  const OFF_TAB_NAMES = { post: "Post", advertisement: "Ad", product_listing: "Listing" };

  function surfaceList() {
    const named = {};
    document.querySelectorAll("#tabs .tab[data-surface]").forEach((el) => {
      const ct = el.dataset.surface;
      if (ct) named[ct] = (el.textContent || "").trim();
    });
    const seen = new Set();
    const out = [];
    // Nav order first, so the chips read in the order the tabs do.
    document.querySelectorAll("#tabs .tab[data-surface]").forEach((el) => {
      const ct = el.dataset.surface;
      if (ct && !seen.has(ct)) { seen.add(ct); out.push({ ct, tab: named[ct] }); }
    });
    for (const ct of SURFACE_ORDER) {
      if (!seen.has(ct)) {
        seen.add(ct);
        out.push({ ct, tab: OFF_TAB_NAMES[ct] || ct, noTab: true });
      }
    }
    return out;
  }
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  class SessionExpired extends Error {}

  async function call(method, url, body) {
    const isForm = body instanceof FormData;
    const res = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: {
        ...(isForm ? {} : { "Content-Type": "application/json" }),
        "X-Requested-With": "bonc-admin",
      },
      ...(body ? { body: isForm ? body : JSON.stringify(body) } : {}),
    });
    let data = {};
    try { data = await res.json(); } catch { /* not JSON */ }
    if (res.ok) return data;
    if (res.status === 401 && !url.includes("/auth/login") && !url.includes("/password/")) {
      throw new SessionExpired("Your session has ended. Please log in again.");
    }
    // The error carries the server's code and body, not just a sentence. Some failures are
    // decisions for the admin rather than things that went wrong - a policy conflict needs
    // the clashing rules so the caller can ask which one stands - and a bare message string
    // throws that away, leaving the UI able only to print the text and give up.
    const err = new Error(data.message || (data.error && data.error.message)
                          || `Something went wrong (${res.status}).`);
    err.code = data.error || (data.error && data.error.code) || `http_${res.status}`;
    err.status = res.status;
    err.detail = data;
    throw err;
  }

  function mount(root) {
    let email = "";            // kept only while the forgot-password steps are on screen
    let resetToken = "";
    let cooldownTimer = null;
    let currentAdmin = "";

    const $ = (id) => root.querySelector("#" + id);
    // `ok` is true (done), false (failed) or "busy" (still going). The third state is here
    // because without it every in-progress line was painted in the failure colour: a retrain
    // announced itself with "Extracting embeddings & fine-tuning linear probe…" in red and
    // sat there for the length of the job, which reads as an error message that never
    // resolves. Telling somebody their retrain has failed while it is running is a good way
    // to have them stop it and conclude the feature is broken.
    const say = (text, ok = false) => {
      const el = $("adm-msg");
      if (!el) return;
      el.textContent = text || "";
      const tone = ok === "busy" ? "busy" : (ok ? "ok" : "err");
      el.className = "adm-msg " + (text ? tone : "");
    };
    const busy = (btn, on, label) => {
      if (!btn) return;
      btn.disabled = on;
      btn.innerHTML = on ? `<span class="spinner"></span> ${esc(label)}` : esc(label);
    };

    function frame(title, sub, body) {
      clearInterval(cooldownTimer);
      root.innerHTML = `<h1>${esc(title)}</h1><p class="subtitle">${esc(sub)}</p>
        <section class="card adm-card" style="max-width:100%;"><div id="adm-msg" class="adm-msg" role="status" aria-live="polite"></div>${body}</section>`;
    }

    function passwordField(id, label, autocomplete) {
      return `<div class="field"><label for="${id}">${label}</label>
        <div class="adm-pw"><input class="input" id="${id}" type="password" autocomplete="${autocomplete}">
        <button type="button" class="btn btn-outline adm-eye" data-eye="${id}" aria-label="Show password">Show</button></div></div>`;
    }
    function wireEyes() {
      root.querySelectorAll("[data-eye]").forEach((b) => b.addEventListener("click", () => {
        const input = $(b.dataset.eye);
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        b.textContent = show ? "Hide" : "Show";
        b.setAttribute("aria-label", show ? "Hide password" : "Show password");
      }));
    }

    // ---- login ------------------------------------------------------------------------
    function loginView(notice = "", ok = false) {
      frame("Admin login", "Only authorised administrators can change moderation settings.", `
        <div style="max-width: 480px;">
          <div class="field"><label for="adm-email">Email address</label>
            <input class="input" id="adm-email" type="email" autocomplete="username" value="${esc(email)}"></div>
          ${passwordField("adm-pass", "Password", "current-password")}
          <div class="adm-row"><button class="btn btn-primary" id="adm-login">Log in</button>
            <button class="btn-link" id="adm-forgot" type="button">Forgot password?</button></div>
        </div>`);
      wireEyes();
      say(notice, ok);
      const submit = async () => {
        const e = $("adm-email").value.trim(), p = $("adm-pass").value;
        if (!e) return say("Enter your email address.");
        if (!/^\S+@\S+\.\S+$/.test(e)) return say("That doesn't look like an email address.");
        if (!p) return say("Enter your password.");
        const btn = $("adm-login");
        busy(btn, true, "Logging in…");
        try {
          const r = await call("POST", "/v1/admin/auth/login", { email: e, password: p });
          email = "";
          signedIn(r.email, false);
        } catch (err) {
          say(err.message);
          busy(btn, false, "Log in");
        }
      };
      $("adm-login").addEventListener("click", submit);
      root.querySelectorAll("input").forEach((i) => i.addEventListener("keydown", (ev) => { if (ev.key === "Enter") submit(); }));
      $("adm-forgot").addEventListener("click", () => { email = $("adm-email").value.trim(); forgotView(); });
    }

    // ---- forgot password --------------------------------------------------------------
    function forgotView() {
      frame("Forgot password", "We will email a 6-digit code to the administrator's address.", `
        <div style="max-width: 480px;">
          <div class="field"><label for="adm-email">Registered email address</label>
            <input class="input" id="adm-email" type="email" autocomplete="username" value="${esc(email)}"></div>
          <div class="adm-row"><button class="btn btn-primary" id="adm-send">Send code</button>
            <button class="btn-link" id="adm-back" type="button">Back to login</button></div>
        </div>`);
      $("adm-back").addEventListener("click", () => loginView());
      $("adm-send").addEventListener("click", async () => {
        const e = $("adm-email").value.trim();
        if (!/^\S+@\S+\.\S+$/.test(e)) return say("Enter a valid email address.");
        const btn = $("adm-send");
        busy(btn, true, "Sending…");
        try {
          const r = await call("POST", "/v1/admin/auth/password/forgot", { email: e });
          email = e;
          codeView(r.message);
        } catch (err) {
          say(err.message);
          busy(btn, false, "Send code");
        }
      });
    }

    function codeView(notice) {
      frame("Enter the code", "Check your email. The code works for 10 minutes and only once.", `
        <div style="max-width: 480px;">
          <div class="field"><label for="adm-code">6-digit code</label>
            <input class="input" id="adm-code" inputmode="numeric" maxlength="6" autocomplete="one-time-code"></div>
          <div class="adm-row"><button class="btn btn-primary" id="adm-verify">Verify code</button>
            <button class="btn btn-outline" id="adm-resend" type="button" disabled>Resend code</button>
            <button class="btn-link" id="adm-back" type="button">Back to login</button></div>
        </div>`);
      say(notice, true);
      let left = 60;
      const resend = $("adm-resend");
      const tick = () => {
        resend.textContent = left > 0 ? `Resend code (${left}s)` : "Resend code";
        resend.disabled = left > 0;
        if (left-- <= 0) clearInterval(cooldownTimer);
      };
      tick();
      cooldownTimer = setInterval(tick, 1000);
      $("adm-back").addEventListener("click", () => loginView());
      resend.addEventListener("click", async () => {
        resend.disabled = true;
        try {
          const r = await call("POST", "/v1/admin/auth/password/forgot", { email });
          say(r.message, true);
          left = 60; tick(); clearInterval(cooldownTimer); cooldownTimer = setInterval(tick, 1000);
        } catch (err) { say(err.message); }
      });
      const submit = async () => {
        const code = $("adm-code").value.trim();
        if (!/^\d{6}$/.test(code)) return say("Enter the 6 digits from the email.");
        const btn = $("adm-verify");
        busy(btn, true, "Checking…");
        try {
          const r = await call("POST", "/v1/admin/auth/password/verify", { email, code });
          resetToken = r.reset_token;
          newPasswordView();
        } catch (err) {
          say(err.message);
          busy(btn, false, "Verify code");
        }
      };
      $("adm-verify").addEventListener("click", submit);
      $("adm-code").addEventListener("keydown", (ev) => { if (ev.key === "Enter") submit(); });
      $("adm-code").focus();
    }

    function newPasswordView() {
      frame("Choose a new password", "At least 10 characters, with letters and numbers.", `
        <div style="max-width: 480px;">
          ${passwordField("adm-new", "New password", "new-password")}
          ${passwordField("adm-new2", "Confirm new password", "new-password")}
          <div class="adm-row"><button class="btn btn-primary" id="adm-reset">Save new password</button></div>
        </div>`);
      wireEyes();
      $("adm-reset").addEventListener("click", async () => {
        const a = $("adm-new").value, b = $("adm-new2").value;
        if (a.length < 10) return say("Use at least 10 characters.");
        if (a !== b) return say("The two passwords don't match.");
        const btn = $("adm-reset");
        busy(btn, true, "Saving…");
        try {
          await call("POST", "/v1/admin/auth/password/reset", { email, reset_token: resetToken, new_password: a });
          resetToken = "";
          loginView("Password changed. You can log in with the new password.", true);
        } catch (err) {
          say(err.message);
          busy(btn, false, "Save new password");
        }
      });
    }

    // ---- signed in dashboard -----------------------------------------------------------
    function signedIn(who, openPopup) {
      currentAdmin = who;
      frame("Admin Dashboard", "Manage AI content moderation, visual model policies, and fine-tuning.", `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; flex-wrap:wrap; gap:8px;">
          <p class="adm-who" style="margin:0;">Signed in as <b>${esc(who)}</b></p>
          <button class="btn btn-outline" id="adm-logout" type="button">Log out</button>
        </div>

        <div class="adm-dash-grid">
          <!-- Card 1: Moderation Settings -->
          <div class="adm-dash-card" id="card-settings">
            <div class="adm-dash-icon">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>
                <line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>
                <line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>
                <line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>
              </svg>
            </div>
            <h3>Content Moderation Settings</h3>
            <p>Configure global AI moderation switches and strictness levels across articles, businesses, and proposals.</p>
            <button class="btn btn-primary" id="adm-open" type="button">Moderation settings</button>
          </div>

          <!-- Card 2: Images Option (System DB & Fine-Tuning) -->
          <div class="adm-dash-card" id="card-images">
            <div class="adm-dash-icon" style="background:#fef3c7; color:#b45309;">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                <circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>
              </svg>
            </div>
            <h3>Image Policy & Fine-Tuning</h3>
            <p>Access stored dataset images by category, select/deselect images, adjust Allow/Block rules, and fine-tune the vision model for business needs (e.g., Baby & Child products).</p>
            <button class="btn btn-primary" id="adm-images-btn" type="button" style="background:#d97706; border-color:#d97706;">Manage Images & Model</button>
          </div>

          <!-- Card 3: Visual policy labels -->
          <div class="adm-dash-card" id="card-policy">
            <div class="adm-dash-icon" style="background:#ede9fe; color:#6d28d9;">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              </svg>
            </div>
            <h3>Image Policy Rules</h3>
            <p>Decide what pictures are refused, in words. Each rule is a few phrasings the model matches against - edit one, add your own, or turn a rule off without retraining anything.</p>
            <button class="btn btn-primary" id="adm-policy-btn" type="button" style="background:#6d28d9; border-color:#6d28d9;">Manage Policy Rules</button>
          </div>
        </div>`);

      $("adm-open").addEventListener("click", () => settingsModal());
      $("adm-images-btn").addEventListener("click", () => imagesOptionView());
      $("adm-policy-btn").addEventListener("click", () => policyView());
      $("adm-logout").addEventListener("click", async () => {
        try { await call("POST", "/v1/admin/auth/logout"); } catch { /* ignore */ }
        loginView("You have been logged out.", true);
      });
      if (openPopup) settingsModal();
    }

    // ===================================================================================
    // IMAGE POLICY RULES VIEW
    //
    // The visual check scores a picture against WRITTEN PHRASINGS, not trained classes, so a
    // rule here is the whole of the policy for that subject - no dataset, no retraining. That
    // is why this screen exists separately from the images one: selecting images and
    // retraining moves the PROBE, and a rule below refuses on its own regardless of what the
    // probe thinks. Wedding photographs are the case that showed it - they score 0.96-0.998
    // against the `wedding` phrasings, so no amount of deselecting and retraining changed the
    // outcome. Turning the rule off here does.
    // ===================================================================================
    function policyView() {
      let labels = {};
      let live = [];

      function render(msg = "", ok = false) {
        const names = Object.keys(labels).sort();
        const unsafe = names.filter((n) => labels[n] && labels[n].unsafe);
        const safe = names.filter((n) => !(labels[n] && labels[n].unsafe));
        frame("Image Policy Rules",
          "What the visual check refuses, written as phrasings. Changes apply in about a second - no retraining.", `
          <div class="img-top-bar">
            <button class="btn btn-outline" id="pol-back" type="button">← Back to Dashboard</button>
            <div style="display:flex; align-items:center; gap:8px;">
              <span class="img-badge-policy" id="pol-sync"></span>
              <button class="btn btn-outline" id="pol-reset" type="button"
                      title="Restore the policy exactly as it shipped">↺ Restore default</button>
            </div>
          </div>
          <div class="adm-msg ${msg ? (ok ? "ok" : "err") : ""}" id="pol-msg" role="status" aria-live="polite">${esc(msg)}</div>
          <p class="hint" style="margin:10px 0 4px;">
            <b>Refusing</b> rules block a picture that matches them. <b>Allowed</b> rules are what
            the refusing ones are judged against - every policy needs at least one.
          </p>
          <div class="pol-list">
            ${unsafe.map((n) => ruleRow(n, true)).join("") || `<p class="hint">No refusing rules.</p>`}
          </div>
          <h3 style="margin:18px 0 6px; font-size:15px;">Allowed subjects</h3>
          <div class="pol-list">
            ${safe.map((n) => ruleRow(n, false)).join("") || `<p class="hint">None.</p>`}
          </div>
          <div style="margin-top:18px;">
            <button class="btn btn-primary" id="pol-add" type="button">+ New rule</button>
          </div>`);

        const sync = $("pol-sync");
        const inSync = JSON.stringify(names) === JSON.stringify([...live].sort());
        sync.textContent = inSync ? `Live: ${live.length} rules in effect`
                                  : `⚠ File and model differ - save any rule to resync`;
        sync.className = inSync ? "img-badge-policy" : "img-badge-policy child-mode";

        $("pol-back").addEventListener("click", () => signedIn(currentAdmin, false));
        $("pol-add").addEventListener("click", () => ruleModal(null));
        $("pol-reset").addEventListener("click", () => resetPolicy());
        back_root().querySelectorAll("[data-edit]").forEach((b) =>
          b.addEventListener("click", () => ruleModal(b.dataset.edit)));
        back_root().querySelectorAll("[data-del]").forEach((b) =>
          b.addEventListener("click", () => removeRule(b.dataset.del)));
        back_root().querySelectorAll("[data-toggle]").forEach((b) =>
          b.addEventListener("click", () => toggleRule(b.dataset.toggle)));
      }

      /** The mount root, so the handlers above find only this view's buttons. */
      function back_root() { return root; }

      function ruleRow(name, isUnsafe) {
        const e = labels[name] || {};
        const n = (e.prompts || []).length;
        const bar = e.reject_min === undefined ? "default" : `refuses at ${e.reject_min}`;
        return `
          <div class="pol-row">
            <div class="pol-row-main">
              <b>${esc(name)}</b>
              <span class="pol-tag ${isUnsafe ? "block" : "allow"}">${isUnsafe ? "REFUSING" : "ALLOWED"}</span>
              <span class="hint">${n} phrasing${n === 1 ? "" : "s"} · ${esc(bar)}${e.category ? " · " + esc(e.category) : ""}</span>
              <div class="pol-prompts">${(e.prompts || []).slice(0, 3).map((p) => `“${esc(p)}”`).join(" · ")}${n > 3 ? ` <span class="hint">+${n - 3} more</span>` : ""}</div>
            </div>
            <div class="pol-row-acts">
              <button class="btn btn-outline" type="button" data-toggle="${esc(name)}">${isUnsafe ? "Stop refusing" : "Start refusing"}</button>
              <button class="btn btn-outline" type="button" data-edit="${esc(name)}">Edit</button>
              <button class="btn btn-outline adm-danger" type="button" data-del="${esc(name)}">Delete</button>
            </div>
          </div>`;
      }

      async function load(msg = "", ok = false) {
        try {
          const res = await call("GET", "/v1/admin/policy/labels");
          labels = res.labels || {};
          live = res.live_labels || [];
          render(msg, ok);
        } catch (err) {
          frame("Image Policy Rules", "", `<div class="adm-msg err">${esc(err.message)}</div>
            <button class="btn btn-outline" id="pol-back" type="button">← Back</button>`);
          $("pol-back").addEventListener("click", () => signedIn(currentAdmin, false));
        }
      }

      async function save(name, entry, what) {
        const msg = $("pol-msg");
        if (msg) { msg.className = "adm-msg"; msg.textContent = `Applying ${what}…`; }
        const apply = async (resolve) =>
          call("PUT", `/v1/admin/policy/labels/${encodeURIComponent(name)}`,
               resolve ? { ...entry, resolve } : entry);
        try {
          await apply();
          await load(`${what} applied. The model is using it now.`, true);
        } catch (err) {
          // A CONFLICT IS A QUESTION, NOT A FAILURE. The server answers 409 with the clashing
          // rules precisely so the admin can be asked which verdict stands, and this path -
          // the Stop refusing / Start refusing buttons on the rule list - printed the
          // sentence and stopped. The sentence ends "Choose which one should stand", so the
          // admin was told to make a choice they were never offered, with no way forward and
          // nothing changed. Same dialog the New/Edit rule path already opens.
          if (err.code === "policy_conflict" && err.detail && (err.detail.conflicts || []).length) {
            if (msg) { msg.className = "adm-msg"; msg.textContent = ""; }
            conflictModal(err.detail, async (verdict) => {
              try {
                await apply(verdict);
                await load(`${what} applied. The model is using it now.`, true);
              } catch (e2) { await load(e2.message, false); }
            });
            return;
          }
          await load(err.message, false);
        }
      }

      /** Turning a rule off keeps its phrasings, so it can be turned back on unchanged. */
      function toggleRule(name) {
        const e = labels[name];
        if (!e) return;
        save(name, { unsafe: !e.unsafe, prompts: e.prompts || [],
                     category: e.category || name,
                     reject_min: e.reject_min === undefined ? "" : e.reject_min },
             `“${name}” ${e.unsafe ? "stopped refusing" : "set to refuse"}`);
      }

      async function removeRule(name) {
        if (!window.confirm(`Delete the rule “${name}”? Its phrasings are lost; turning it off `
                            + `instead keeps them.`)) return;
        try {
          await call("DELETE", `/v1/admin/policy/labels/${encodeURIComponent(name)}`);
          await load(`“${name}” deleted.`, true);
        } catch (err) {
          await load(err.message, false);
        }
      }

      async function resetPolicy() {
        if (!window.confirm("Restore the policy exactly as it shipped? Every rule you have "
                            + "added or changed here is discarded.")) return;
        try {
          await call("POST", "/v1/admin/policy/reset");
          await load("Policy restored to the default.", true);
        } catch (err) {
          await load(err.message, false);
        }
      }

      /** Ask which verdict stands when the same wording is in two rules that disagree.
       *
       *  Deliberately a question with no default and no "apply anyway". Refusing and allowing
       *  the same thing are both coherent policies; the system cannot know which was meant,
       *  and guessing is what produced the bug - an allowing rainbow rule and a refusing one,
       *  with write order quietly deciding. Resolving one way REMOVES the wording from the
       *  rules holding the other verdict, so the consequence is spelled out before the click.
       */
      function conflictModal(err, onResolve) {
        const rows = (err.conflicts || []);
        const back = document.createElement("div");
        back.className = "backdrop";
        back.innerHTML = `
          <div class="modal img-modal-wide" role="alertdialog" aria-modal="true" aria-labelledby="cf-title">
            <div class="modal-head"><h2 id="cf-title">These rules contradict each other</h2>
              <button class="btn-link" id="cf-x" aria-label="Close" type="button">✕</button></div>
            <div class="modal-body">
              <p class="hint" style="margin-top:0;">${esc(err.message || "")}</p>
              <table class="pol-try-table"><tbody>
                ${rows.map((c) => `<tr>
                  <td>“${esc(c.phrasing || c.filename || "")}”</td>
                  <td><span class="pol-tag ${c.other_verdict === "allow" ? "allow" : "block"}">
                    ${esc(c.other_verdict || c.written_verdict || "")} in ${esc(c.other_label || c.written_rule || "")}</span></td>
                  <td><span class="pol-tag ${(c.this_verdict || c.you_chose) === "allow" ? "allow" : "block"}">
                    you chose ${esc(c.this_verdict || c.you_chose || "")}</span></td>
                </tr>`).join("")}
              </tbody></table>
              <p class="hint">Whichever you keep, the wording is removed from the rules holding
                the other verdict. A rule left with no wordings at all is deleted.</p>
            </div>
            <div class="modal-foot">
              <button class="btn btn-outline" id="cf-cancel" type="button">Leave both as they are</button>
              <button class="btn btn-outline adm-danger" id="cf-refuse" type="button">Refuse these</button>
              <button class="btn btn-primary" id="cf-allow" type="button" style="background:#15803d; border-color:#15803d;">Allow these</button>
            </div>
          </div>`;
        document.body.appendChild(back);
        const close = () => back.remove();
        back.querySelector("#cf-x").addEventListener("click", close);
        back.querySelector("#cf-cancel").addEventListener("click", close);
        back.querySelector("#cf-refuse").addEventListener("click", () => { close(); onResolve("refuse"); });
        back.querySelector("#cf-allow").addEventListener("click", () => { close(); onResolve("allow"); });
      }

      function ruleModal(name) {
        const e = (name && labels[name]) || { unsafe: true, prompts: [], category: "" };
        const back = document.createElement("div");
        back.className = "backdrop";
        back.innerHTML = `
          <div class="modal img-modal-wide" role="dialog" aria-modal="true" aria-labelledby="pol-modal-title">
            <div class="modal-head"><h2 id="pol-modal-title">${name ? "Edit rule" : "New rule"}</h2>
              <button class="btn-link" id="pol-x" aria-label="Close" type="button">✕</button></div>
            <div class="modal-body">
              <div id="pol-modal-msg" class="adm-msg" role="status"></div>
              <div class="field">
                <label for="pol-name">Rule name <span class="req">*</span></label>
                <input class="input" id="pol-name" value="${esc(name || "")}" ${name ? "readonly" : ""}
                       placeholder="e.g. fireworks" autocomplete="off">
                <p class="hint">Lower case, letters, digits and underscores. This name appears in
                  moderation results.</p>
              </div>
              <div class="field">
                <label for="pol-prompts">Phrasings <span class="req">*</span></label>
                <textarea class="input" id="pol-prompts" rows="7"
                  placeholder="a photo of fireworks exploding&#10;a photo of firecrackers for sale">${esc((e.prompts || []).join("\n"))}</textarea>
                <p class="hint">One per line. Write them the way a caption would read - "a photo of
                  …" scores better than a bare noun. Several wordings help: one may miss a picture
                  another catches.</p>
              </div>
              <div class="field">
                <label for="pol-unsafe">What this rule does</label>
                <select class="select" id="pol-unsafe">
                  <option value="1" ${e.unsafe ? "selected" : ""}>REFUSE pictures that match</option>
                  <option value="0" ${e.unsafe ? "" : "selected"}>ALLOW - an ordinary subject to judge the others against</option>
                </select>
              </div>
              <div class="field">
                <label for="pol-cat">Reported category</label>
                <input class="input" id="pol-cat" value="${esc(e.category || "")}"
                       placeholder="e.g. weapons - defaults to the rule name">
              </div>
              <div class="field">
                <label for="pol-min">Confidence needed to refuse</label>
                <input class="input" id="pol-min" value="${e.reject_min === undefined ? "" : e.reject_min}"
                       placeholder="leave blank to use the project default">
                <p class="hint">0 to 1. Scores are not comparable between rules, so set this from
                  that rule's own pictures rather than copying another's.</p>
              </div>

              <div class="pol-try">
                <div class="pol-try-head">
                  <b>Try these words first</b>
                  <button class="btn btn-outline" id="pol-try-btn" type="button">Show what they match</button>
                </div>
                <p class="hint">Nothing is saved. Your stored pictures are ranked by how closely
                  they match the phrasings above, so you can see what a rule would pick out
                  before it is in force.</p>
                <div id="pol-try-out"></div>
              </div>
            </div>
            <div class="modal-foot">
              <button class="btn btn-outline" id="pol-cancel" type="button">Cancel</button>
              <button class="btn btn-primary" id="pol-save" type="button">Apply rule</button>
            </div>
          </div>`;
        document.body.appendChild(back);
        const q = (id) => back.querySelector("#" + id);
        const close = () => back.remove();
        q("pol-x").addEventListener("click", close);
        q("pol-cancel").addEventListener("click", close);
        back.addEventListener("keydown", (ev) => { if (ev.key === "Escape") close(); });

        q("pol-try-btn").addEventListener("click", async () => {
          const prompts = q("pol-prompts").value.split("\n").map((s) => s.trim()).filter(Boolean);
          const out = q("pol-try-out");
          if (!prompts.length) {
            out.innerHTML = `<p class="hint">Write a phrasing above first.</p>`;
            return;
          }
          out.innerHTML = `<p class="adm-loading"><span class="spinner dark"></span> Matching against your stored pictures…</p>`;
          try {
            const res = await call("POST", "/v1/admin/policy/preview", { prompts, limit: 10 });
            const rows = res.top || [];
            // Grouped by the category each matched picture is filed under. A wording that is
            // working puts one category at the top repeatedly; a vague one returns a spread of
            // unrelated categories at almost the same score, which is what "no match" looks
            // like. That pattern is the useful signal here - not the numbers themselves.
            const byCat = {};
            rows.forEach((r) => { byCat[r.category || "?"] = (byCat[r.category || "?"] || 0) + 1; });
            const spread = Object.entries(byCat).sort((a, b) => b[1] - a[1]);
            const verdict = spread.length && spread[0][1] >= Math.ceil(rows.length * 0.6)
              ? `<span class="pol-tag allow">Focused — mostly <b>${esc(spread[0][0])}</b></span>`
              : `<span class="pol-tag block">Scattered — these words do not single anything out</span>`;
            out.innerHTML = `
              <div class="pol-try-verdict">${verdict}
                <span class="hint">ranked ${res.scored.toLocaleString()} stored pictures</span></div>
              <table class="pol-try-table"><tbody>
                ${rows.map((r, i) => `<tr>
                  <td class="rank">${i + 1}</td>
                  <td>${esc(r.filename || "")}</td>
                  <td><span class="pol-tag ${r.action === "allow" ? "allow" : "block"}">${esc(r.category || "")}</span></td>
                </tr>`).join("")}
              </tbody></table>
              <p class="hint">Order is what matters here, not the scores: the live check
                calculates differently and reads much higher. Use this to confirm the right
                pictures come to the top.</p>`;
          } catch (err) {
            out.innerHTML = `<div class="adm-msg err">${esc(err.message)}</div>`;
          }
        });
        q("pol-save").addEventListener("click", async () => {
          const nm = q("pol-name").value.trim().toLowerCase();
          const prompts = q("pol-prompts").value.split("\n").map((s) => s.trim()).filter(Boolean);
          const m = q("pol-modal-msg");
          if (!nm) { m.className = "adm-msg err"; m.textContent = "Give the rule a name."; return; }
          if (!prompts.length) {
            m.className = "adm-msg err"; m.textContent = "Add at least one phrasing."; return;
          }
          const foot = back.querySelector(".modal-foot");
          foot.innerHTML = `<button class="btn btn-primary" disabled><span class="spinner"></span> Applying…</button>`;
          const entry = { unsafe: q("pol-unsafe").value === "1", prompts,
                          category: q("pol-cat").value.trim() || nm,
                          reject_min: q("pol-min").value.trim() };
          const apply = async (resolve) => {
            await call("PUT", `/v1/admin/policy/labels/${encodeURIComponent(nm)}`,
                       resolve ? { ...entry, resolve } : entry);
            close();
            await load(`“${nm}” applied. The model is using it now.`, true);
          };
          try {
            await apply();
          } catch (err) {
            if (err.code === "policy_conflict" && err.detail) {
              conflictModal(err.detail, async (verdict) => {
                try { await apply(verdict); }
                catch (e2) { await load(e2.message, false); }
              });
              return;
            }
            foot.innerHTML = `<button class="btn btn-outline" id="pol-cancel2" type="button">Cancel</button>
              <button class="btn btn-primary" id="pol-save2" type="button">Apply rule</button>`;
            m.className = "adm-msg err"; m.textContent = err.message;
            back.querySelector("#pol-cancel2").addEventListener("click", close);
            back.querySelector("#pol-save2").addEventListener("click", () => q("pol-save").click());
          }
        });
      }

      load();
    }

    // ===================================================================================
    // IMAGES & MODEL FINE-TUNING VIEW
    // ===================================================================================
    function imagesOptionView() {
      let activeCat = "all";
      let activeAction = "all";
      let activeSelected = "all";
      let searchQuery = "";
      let page = 1;
      const limit = 24;

      let categories = [];
      let imagesData = { items: [], pagination: {}, stats: {} };
      let pendingChanges = new Map(); // id -> { action, is_selected }
      let blurSensitiveMode = true; // Initial blur for nudity and sexual images
      let revealedImageIds = new Set(); // IDs of sensitive images revealed by admin
      let blurredImageIds = new Set(); // IDs of non-sensitive images blurred by admin

      function isSensitiveImage(img) {
        if (!img) return false;
        if (img.is_sensitive !== undefined && img.is_sensitive !== null) {
          return Boolean(img.is_sensitive);
        }
        const cat = (img.category || "").toLowerCase();
        const desc = (img.description || "").toLowerCase();
        const fn = (img.filename || "").toLowerCase();
        const p = (img.file_path || "").toLowerCase();
        const kws = ["sexual", "nudity", "nude", "porn", "pornograph", "nsfw", "erotic"];
        if (kws.some((k) => cat.includes(k) || desc.includes(k) || fn.includes(k))) return true;
        return p.includes("/nudity/") || p.includes("/sexual/") || p.includes("/porn/") || p.includes("_quarantine_explicit");
      }

      function isImageBlurred(img) {
        if (!img) return false;
        const isSens = isSensitiveImage(img);
        if (isSens) {
          if (!blurSensitiveMode) return false;
          return !revealedImageIds.has(img.id);
        } else {
          return blurredImageIds.has(img.id);
        }
      }

      function renderShell() {
        frame("Image Policy & Model Fine-Tuning", "Manage stored policy images, add categories, and fine-tune the image model for business requirements.", `
          <div class="img-mgmt-shell">
            <!-- Top bar -->
            <div class="img-top-bar">
              <button class="btn btn-outline" id="img-back-btn" type="button">← Back to Dashboard</button>
              <div style="display:flex; align-items:center; gap:8px;">
                <span id="img-active-policy-badge" class="img-badge-policy">Policy: Loading…</span>
                <button class="btn btn-outline" id="img-hist-btn" type="button" title="View training history">🕒 Training History</button>
              </div>
            </div>

            <!-- Stats Bar -->
            <div class="img-stats-bar" id="img-stats-container">
              <div class="img-stat-box"><span class="img-stat-val" id="stat-total">0</span><span class="img-stat-lbl">Total Stored</span></div>
              <div class="img-stat-box"><span class="img-stat-val" id="stat-selected" style="color:#0284c7;">0</span><span class="img-stat-lbl">Selected to Train</span></div>
              <div class="img-stat-box"><span class="img-stat-val" id="stat-allowed" style="color:#16a34a;">0</span><span class="img-stat-lbl">Allowed (Safe)</span></div>
              <div class="img-stat-box"><span class="img-stat-val" id="stat-blocked" style="color:#dc2626;">0</span><span class="img-stat-lbl">Blocked (Unsafe)</span></div>
            </div>

            <!-- Business Presets Bar -->
            <div class="img-preset-bar">
              <div class="img-preset-left">
                <span style="font-weight:700; font-size:13px; color:#334155;">Business Preset:</span>
                <select class="img-preset-sel" id="img-preset-sel">
                  <option value="standard">Standard BONC Platform (Minors & Child Images Blocked)</option>
                  <option value="child_products">Child & Baby Care Products (Allow Baby Images - e.g. Mamy Poko Pants)</option>
                </select>
                <button class="btn btn-outline" id="img-apply-preset-btn" type="button">Apply Preset & Retrain</button>
              </div>
              <div style="font-size:12px; color:var(--muted);">Allows fine-tuning model for businesses selling diapers, baby wear, etc.</div>
            </div>

            <!-- Category Pills Bar -->
            <div>
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <span style="font-weight:700; font-size:13px; color:#334155;">Categories:</span>
                <span id="cat-bulk-act-wrap" style="display:none; font-size:12px;">
                  Set all <b id="cur-cat-name"></b> to:
                  <button class="btn-link" id="cat-bulk-allow" type="button" style="color:#16a34a; font-weight:700;">ALLOW ALL</button> |
                  <button class="btn-link" id="cat-bulk-block" type="button" style="color:#dc2626; font-weight:700;">BLOCK ALL</button>
                </span>
              </div>
              <div class="cat-pills-bar" id="cat-pills-container">
                <button class="cat-pill active" type="button" data-cat="all">All <span class="cnt" id="cat-cnt-all">0</span></button>
                <button class="cat-pill add-cat-btn" id="btn-add-cat-modal" type="button">+ New Category</button>
              </div>
            </div>

            <!-- Main Toolbar -->
            <div class="img-toolbar">
              <div class="img-filters">
                <input class="img-search-input" id="img-search" type="search" placeholder="Search images…" value="${esc(searchQuery)}">
                <select class="input" id="flt-action" style="padding:6px 10px; font-size:13px; width:auto;">
                  <option value="all">All Actions</option>
                  <option value="allow" ${activeAction === "allow" ? "selected" : ""}>Allowed Only</option>
                  <option value="block" ${activeAction === "block" ? "selected" : ""}>Blocked Only</option>
                </select>
                <select class="input" id="flt-selected" style="padding:6px 10px; font-size:13px; width:auto;">
                  <option value="all">All Selection</option>
                  <option value="1" ${activeSelected === "1" ? "selected" : ""}>Selected Only</option>
                  <option value="0" ${activeSelected === "0" ? "selected" : ""}>Deselected Only</option>
                </select>
                <button class="btn btn-outline" id="btn-select-all" type="button" style="padding:6px 10px; font-size:12px;">Select All</button>
                <button class="btn btn-outline" id="btn-deselect-all" type="button" style="padding:6px 10px; font-size:12px;">Deselect All</button>
                <button class="btn btn-outline" id="btn-toggle-blur-all" type="button" style="padding:6px 10px; font-size:12px;" title="Nudity & sexual images are blurred by default. Click to reveal all.">🛡️ NSFW Blur: ON</button>
              </div>
              <div class="img-actions-right">
                <button class="btn btn-outline" id="btn-upload-modal" type="button" style="border-color:#0284c7; color:#0284c7;">+ Upload Image</button>
                <button class="btn btn-primary" id="btn-submit-changes" type="button" style="background:#2563eb;">Submit Changes</button>
                <button class="btn btn-primary" id="btn-train-model" type="button" style="background:#16a34a; border-color:#16a34a;">⚡ Train Model</button>
              </div>
            </div>

            <!-- WHAT THE CURRENT FILTER IS TEACHING. A grid of thumbnails cannot show it and
                 the stat boxes above are global, so an admin selecting "all the gym images"
                 had no way to see that 49 of the 64 are ALLOW examples - gym adverts and class
                 photos the policy protects on purpose - and that selecting them all teaches
                 both directions at once, mostly allow. Sits between the toolbar and the grid
                 because it describes what the buttons above will act on. -->
            <div id="img-filter-teaching" class="img-filter-teaching" style="display:none;"></div>

            <!-- Image Grid Container -->
            <div id="img-grid-container"><p class="adm-loading"><span class="spinner dark"></span> Loading stored policy images…</p></div>

            <!-- Pagination Container -->
            <div class="img-pagination" id="img-pagination"></div>
          </div>`);

        $("img-back-btn").addEventListener("click", () => signedIn(currentAdmin, false));
        $("btn-add-cat-modal").addEventListener("click", () => addCategoryModal());
        $("btn-upload-modal").addEventListener("click", () => uploadImageModal());
        $("img-hist-btn").addEventListener("click", () => trainingHistoryModal());
        $("img-apply-preset-btn").addEventListener("click", () => applyPresetAction());
        $("btn-submit-changes").addEventListener("click", () => submitBatchChanges());
        $("btn-train-model").addEventListener("click", () => triggerTraining());

        $("img-search").addEventListener("input", (e) => {
          searchQuery = e.target.value;
          page = 1;
          loadImages();
        });
        $("flt-action").addEventListener("change", (e) => {
          activeAction = e.target.value;
          page = 1;
          loadImages();
        });
        $("flt-selected").addEventListener("change", (e) => {
          activeSelected = e.target.value;
          page = 1;
          loadImages();
        });
        $("btn-select-all").addEventListener("click", () => bulkSelection(true));
        $("btn-deselect-all").addEventListener("click", () => bulkSelection(false));
        $("btn-toggle-blur-all").addEventListener("click", () => {
          blurSensitiveMode = !blurSensitiveMode;
          if (!blurSensitiveMode) {
            (imagesData.items || []).forEach((img) => {
              if (isSensitiveImage(img)) revealedImageIds.add(img.id);
            });
          } else {
            revealedImageIds.clear();
          }
          const b = $("btn-toggle-blur-all");
          if (b) {
            b.textContent = blurSensitiveMode ? "🛡️ NSFW Blur: ON" : "👁️ NSFW Blur: OFF";
            b.title = blurSensitiveMode
              ? "Nudity & sexual images are blurred by default. Click to reveal all."
              : "Nudity & sexual images are revealed. Click to blur all.";
          }
          renderGrid(imagesData.items);
        });

        $("cat-bulk-allow").addEventListener("click", () => bulkCategoryAction("allow"));
        $("cat-bulk-block").addEventListener("click", () => bulkCategoryAction("block"));
      }

      async function loadCategories() {
        try {
          const res = await call("GET", "/v1/admin/images/categories");
          categories = res.categories || [];
          renderCategoryPills(res.active_mode);
        } catch (err) {
          say("Failed to load categories: " + err.message);
        }
      }

      function renderCategoryPills(activeMode) {
        const container = $("cat-pills-container");
        if (!container) return;

        // Update active policy badge
        const badge = $("img-active-policy-badge");
        if (badge) {
          if (activeMode === "child_products") {
            badge.textContent = "Policy: Child & Baby Products Mode (Baby Images Allowed)";
            badge.className = "img-badge-policy child-mode";
            $("img-preset-sel").value = "child_products";
          } else {
            badge.textContent = "Policy: Standard BONC Platform (Minors Blocked)";
            badge.className = "img-badge-policy";
            $("img-preset-sel").value = "standard";
          }
        }

        const totalAll = categories.reduce((sum, c) => sum + (c.total_images || 0), 0);

        // Build HTML. The "All" count keeps its id here, and is NOT written separately before
        // this line: the id exists only in the shell markup, and the innerHTML below replaces
        // the shell's children. So the first render found #cat-cnt-all and every later one
        // got null - and since this function re-renders itself from the pill click handler,
        // `null.textContent = ...` threw before the re-render and before loadImages(). The
        // handlers were attached and firing the whole time; the work just died on the first
        // statement, which looks exactly like a button that does nothing.
        let html = `<button class="cat-pill ${activeCat === "all" ? "active" : ""}" type="button" data-cat="all">All <span class="cnt" id="cat-cnt-all">${totalAll}</span></button>`;
        categories.forEach((cat) => {
          html += `<button class="cat-pill ${activeCat === cat.name ? "active" : ""}" type="button" data-cat="${esc(cat.name)}" title="${esc(cat.description || cat.display_name)}">
            ${esc(cat.display_name)} <span class="cnt">${cat.total_images || 0}</span></button>`;
        });
        html += `<button class="cat-pill add-cat-btn" id="btn-add-cat-modal" type="button">+ New Category</button>`;
        container.innerHTML = html;

        // Wire click handlers
        container.querySelectorAll(".cat-pill[data-cat]").forEach((pill) => {
          pill.addEventListener("click", () => {
            activeCat = pill.dataset.cat;
            page = 1;
            renderCategoryPills(activeMode);
            updateCategoryHeader();
            loadImages();
          });
        });
        $("btn-add-cat-modal").addEventListener("click", () => addCategoryModal());
        updateCategoryHeader();
      }

      function updateCategoryHeader() {
        const wrap = $("cat-bulk-act-wrap");
        if (!wrap) return;
        if (activeCat === "all") {
          wrap.style.display = "none";
        } else {
          wrap.style.display = "inline-block";
          const catObj = categories.find((c) => c.name === activeCat);
          $("cur-cat-name").textContent = catObj ? catObj.display_name : activeCat;
        }
      }

      async function loadImages() {
        const grid = $("img-grid-container");
        grid.innerHTML = `<p class="adm-loading"><span class="spinner dark"></span> Loading images…</p>`;

        const q = new URLSearchParams();
        if (activeCat !== "all") q.set("category", activeCat);
        if (activeAction !== "all") q.set("action", activeAction);
        if (activeSelected !== "all") q.set("selected", activeSelected);
        if (searchQuery.trim()) q.set("search", searchQuery.trim());
        q.set("page", page);
        q.set("limit", limit);

        try {
          imagesData = await call("GET", `/v1/admin/images?${q.toString()}`);
          updateStats(imagesData.stats);
          updateFilterTeaching(imagesData.filtered);
          renderGrid(imagesData.items);
          renderPagination(imagesData.pagination);
        } catch (err) {
          grid.innerHTML = `<div class="adm-msg err">${esc(err.message)}</div>`;
        }
      }

      function updateStats(st) {
        if (!st) return;
        $("stat-total").textContent = st.total || 0;
        $("stat-selected").textContent = st.selected || 0;
        $("stat-allowed").textContent = st.allowed || 0;
        $("stat-blocked").textContent = st.blocked || 0;
      }

      /** Categories the admin has deselected entirely, so the model ignores them. An
       *  exemplar in one of these is not being applied, and a card saying "Live rule" would
       *  be stating the opposite of what the check does. */
      const isWithdrawn = (cat) =>
        (imagesData.withdrawn_categories || []).includes(String(cat || "").toLowerCase());

      // The filter's own composition, which is the one the bulk buttons act on.
      function updateFilterTeaching(f) {
        const el = $("img-filter-teaching");
        if (!el) return;
        const filtering = activeCat !== "all" || activeAction !== "all" ||
                          activeSelected !== "all" || !!searchQuery.trim();
        if (!f || !filtering) { el.style.display = "none"; return; }
        const a = f.teaching_allow || 0, b = f.teaching_block || 0;
        const bits = [`<b>${f.total || 0}</b> match this filter`,
                      `<b>${f.selected || 0}</b> selected to train`];
        if (a || b) {
          bits.push(`teaching <b style="color:#16a34a;">${a} ALLOW</b> / ` +
                    `<b style="color:#dc2626;">${b} BLOCK</b>`);
        }
        // Only worth shouting about when it is genuinely ambiguous. Named explicitly because
        // "select all of these and retrain" is the one action where teaching both ways at
        // once silently cancels out, and the admin reads the result as the retrain failing.
        const warn = a && b
          ? `<div class="img-teach-warn">These pictures teach the model in <b>both</b>
             directions. The ${a} marked ALLOW push toward publishing this kind of picture and
             the ${b} marked BLOCK push toward refusing it, so retraining on the whole set may
             not move the verdict the way you expect. Filter by <b>Allowed Only</b> or
             <b>Blocked Only</b> to select just one side.</div>`
          : "";
        el.innerHTML = `<div class="img-teach-line">${bits.join(" &middot; ")}</div>${warn}`;
        el.style.display = "";
      }

      function renderGrid(items) {
        const grid = $("img-grid-container");
        if (!items || !items.length) {
          grid.innerHTML = `<div style="text-align:center; padding:40px; background:#f8fafc; border:1px dashed var(--line); border-radius:10px;">
            <p style="font-size:16px; font-weight:700; color:#475569; margin:0 0 6px;">No images found</p>
            <p style="font-size:13px; color:var(--muted); margin:0 0 16px;">No stored images match the current filters or category.</p>
            <button class="btn btn-outline" id="empty-upload-btn" type="button">+ Upload Image to this Category</button>
          </div>`;
          const btn = $("empty-upload-btn");
          if (btn) btn.addEventListener("click", () => uploadImageModal(activeCat !== "all" ? activeCat : "safe"));
          return;
        }

        let html = `<div class="img-grid">`;
        items.forEach((img) => {
          // Check pending local edits
          const pending = pendingChanges.get(img.id);
          const isSelected = pending && pending.is_selected !== undefined ? pending.is_selected : img.is_selected;
          const action = pending && pending.action !== undefined ? pending.action : img.action;
          const isAllow = action === "allow";
          const isSensitive = isSensitiveImage(img);
          const isBlurred = isImageBlurred(img);

          html += `
            <div class="img-card ${isAllow ? "allow-state" : "block-state"} ${isSelected ? "" : "deselected"}" data-id="${img.id}">
              <div class="img-thumb-wrap ${isSensitive ? "is-sensitive" : ""} ${isBlurred ? "is-blurred" : "is-revealed"}" data-id="${img.id}">
                <label class="img-check-label" title="Select or deselect this image for model training">
                  <input class="img-check-input" type="checkbox" data-id="${img.id}" ${isSelected ? "checked" : ""}>
                  <span>Train</span>
                </label>
                <img class="img-thumb" src="/v1/admin/images/${img.id}/file" alt="${esc(img.filename)}" loading="lazy" title="${isBlurred ? 'Image blurred. Click View Image to reveal.' : 'Click to preview full size'}" onerror="this.src=\'data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'100\' height=\'100\'><rect width=\'100\' height=\'100\' fill=\'%23eee\'/><text x=\'50%\' y=\'50%\' fill=\'%23aaa\' dominant-baseline=\'middle\' text-anchor=\'middle\'>No preview</text></svg>\'">
                <div class="img-sensitive-overlay">
                  <div class="img-sensitive-badge ${isSensitive ? "" : "neutral"}">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
                    <span>${isSensitive ? "Nudity / Sexual" : "Hidden"}</span>
                  </div>
                  <button class="btn-reveal-img" type="button" data-id="${img.id}" title="Click to view image">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                    View Image
                  </button>
                </div>
                <button class="btn-hide-img" type="button" data-id="${img.id}" title="Blur and hide this image">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
                  Blur
                </button>
              </div>
              <div class="img-card-body">
                <div class="img-card-meta">
                  <div style="display:flex; align-items:center; gap:4px; flex-wrap:wrap;">
                    <span class="img-cat-tag">${esc(img.category)}</span>
                    ${isSensitive ? `<span class="img-sensitive-tag" title="Flagged as nudity or sexual content">18+ Sensitive</span>` : ""}
                  </div>
                  <span class="img-badge ${isAllow ? "allow" : "block"}">${isAllow ? "ALLOWED" : "BLOCKED"}</span>
                </div>
                <div class="img-filename" title="${esc(img.filename)}">${esc(img.filename)}</div>
              </div>
              <div class="img-card-foot">
                <button class="btn-toggle-act" type="button" data-id="${img.id}" data-action="${isAllow ? "block" : "allow"}">
                  ${isAllow ? "Switch to BLOCK" : "Switch to ALLOW"}
                </button>
              </div>
              <!-- TEACH BY EXAMPLE. Switching the label above decides what the PROBE is
                   trained on next time somebody retrains. This decides what happens to the
                   next upload, now: the live check compares every picture against the ones
                   marked here. The two are separate on purpose and the wording says which is
                   which, because "block" meaning two different things one line apart is how
                   an admin ends up deselecting wedding photos and wondering why weddings are
                   still refused. -->
              <div class="img-card-teach ${img.is_exemplar ? "on" : ""} ${img.is_exemplar && isWithdrawn(img.category) ? "paused" : ""}">
                <span class="teach-lbl">${img.is_exemplar
                    ? (isWithdrawn(img.category)
                        ? `Paused: <b>${esc(img.category)}</b> is withdrawn from the model, so this mark is not being applied`
                        : `Live rule: <b>${isAllow ? "allowing" : "refusing"}</b> lookalikes`)
                    : "Use as a live example:"}</span>
                <span class="teach-acts">
                  ${img.is_exemplar
                    ? `<button class="btn-teach clear" type="button" data-teach="clear" data-id="${img.id}">Stop</button>`
                    : `<button class="btn-teach block" type="button" data-teach="block" data-id="${img.id}"
                          title="Refuse pictures that look like this, from the next upload">Refuse like this</button>
                       <button class="btn-teach allow" type="button" data-teach="allow" data-id="${img.id}"
                          title="Allow pictures that look like this, overriding other rules">Allow like this</button>`}
                </span>
              </div>
            </div>`;
        });
        html += `</div>`;
        grid.innerHTML = html;

        // Teaching by example applies IMMEDIATELY rather than joining pendingChanges, and the
        // difference is deliberate. The other controls on a card describe what the next
        // retrain should learn, so batching them is right. This one changes what the live
        // check does to the very next upload, and an admin who clicks "Refuse like this" and
        // then navigates away expecting it to be in force should be right.
        grid.querySelectorAll(".btn-teach").forEach((btn) => {
          btn.addEventListener("click", async (e) => {
            const el = e.target.closest(".btn-teach");
            const id = parseInt(el.dataset.id, 10);
            const mode = el.dataset.teach;
            const row = el.closest(".img-card-teach");
            row.innerHTML = `<span class="teach-lbl"><span class="spinner dark"></span> Applying…</span>`;
            // SHOW THE REACH FIRST. An exemplar generalises by appearance and appearance does
            // not respect the categories a person has in mind: one rainbow marked "refuse
            // like this" also reached a cloud at 0.828, and safe, romantic and minor pictures
            // above 0.68. The admin saw a cloud refused with nothing in the policy naming
            // clouds, and deselecting it and retraining the probe could not help, because the
            // probe was not the one objecting. So the blast radius is measured and shown
            // before the mark is applied, grouped by the categories it reaches - the surprise
            // is never "it caught more", it is "it caught THOSE".
            if (mode !== "clear") {
              try {
                const pv = await call("PUT", "/v1/admin/policy/exemplars",
                                      { ids: [id], mode, preview: true });
                const other = pv.other_categories || {};
                const names = Object.keys(other);
                if (names.length) {
                  const list = names.sort((a, b) => other[b] - other[a])
                    .map((c) => `${other[c]} ${esc(c)}`).join(", ");
                  const ok = window.confirm(
                    `This will ${mode === "block" ? "refuse" : "allow"} ${pv.would_match} other `
                    + `stored pictures, including ${list}.\n\n`
                    + `Pictures that merely look alike are caught too, not only ones in this `
                    + `category. Continue?`);
                  if (!ok) { loadImages(); return; }
                }
              } catch { /* preview is advisory: a failure here must not block the action */ }
            }
            const apply = async (resolve) => {
              const res = await call("PUT", "/v1/admin/policy/exemplars",
                                     resolve ? { ids: [id], mode, resolve } : { ids: [id], mode });
              say(mode === "clear"
                ? `Stopped using that picture as a live example. ${res.exemplars} still in force.`
                : `Done — pictures that look like that one will now be ${mode === "block" ? "refused" : "allowed"}. `
                  + `${res.refusing} refusing, ${res.allowing} allowing.`, true);
              loadImages();
            };
            try {
              await apply();
            } catch (err) {
              // The written rules already decide these pictures the other way. Exemplars are
              // checked first, so going ahead would override the admin's own rule with
              // nothing in the policy file to show for it - worth a question, not a warning.
              if (err.code === "exemplar_conflict" && err.detail) {
                conflictModal(err.detail, async (verdict) => {
                  try { await apply(verdict); }
                  catch (e2) { say(e2.message); loadImages(); }
                });
                return;
              }
              say(err.message);
              loadImages();
            }
          });
        });

        // Wire card events
        grid.querySelectorAll(".img-check-input").forEach((chk) => {
          chk.addEventListener("change", (e) => {
            const id = parseInt(e.target.dataset.id, 10);
            const card = e.target.closest(".img-card");
            const checked = e.target.checked;
            card.classList.toggle("deselected", !checked);

            const cur = pendingChanges.get(id) || {};
            cur.id = id;
            cur.is_selected = checked ? 1 : 0;
            pendingChanges.set(id, cur);
          });
        });

        grid.querySelectorAll(".btn-toggle-act").forEach((btn) => {
          btn.addEventListener("click", (e) => {
            const id = parseInt(e.target.dataset.id, 10);
            const nextAction = e.target.dataset.action;
            const cur = pendingChanges.get(id) || {};
            cur.id = id;
            cur.action = nextAction;
            pendingChanges.set(id, cur);

            // Re-render this card locally
            const card = e.target.closest(".img-card");
            const isAllow = nextAction === "allow";
            card.classList.toggle("allow-state", isAllow);
            card.classList.toggle("block-state", !isAllow);
            card.querySelector(".img-badge").className = `img-badge ${isAllow ? "allow" : "block"}`;
            card.querySelector(".img-badge").textContent = isAllow ? "ALLOWED" : "BLOCKED";
            btn.dataset.action = isAllow ? "block" : "allow";
            btn.textContent = isAllow ? "Switch to BLOCK" : "Switch to ALLOW";
          });
        });

        // Wire reveal buttons for all images
        grid.querySelectorAll(".btn-reveal-img").forEach((btn) => {
          btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const id = parseInt(btn.dataset.id, 10);
            const imgObj = (items || []).find((it) => it.id === id);
            if (!imgObj) return;
            if (isSensitiveImage(imgObj)) {
              revealedImageIds.add(id);
            } else {
              blurredImageIds.delete(id);
            }
            const wrap = btn.closest(".img-thumb-wrap");
            if (wrap) {
              wrap.classList.remove("is-blurred");
              wrap.classList.add("is-revealed");
            }
          });
        });

        // Wire blur/hide buttons for all images
        grid.querySelectorAll(".btn-hide-img").forEach((btn) => {
          btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const id = parseInt(btn.dataset.id, 10);
            const imgObj = (items || []).find((it) => it.id === id);
            if (!imgObj) return;
            if (isSensitiveImage(imgObj)) {
              revealedImageIds.delete(id);
            } else {
              blurredImageIds.add(id);
            }
            const wrap = btn.closest(".img-thumb-wrap");
            if (wrap) {
              wrap.classList.remove("is-revealed");
              wrap.classList.add("is-blurred");
            }
          });
        });

        // Wire thumbnail preview click (opens preview modal when revealed)
        grid.querySelectorAll(".img-thumb").forEach((thumb) => {
          thumb.addEventListener("click", (e) => {
            const wrap = thumb.closest(".img-thumb-wrap");
            if (!wrap) return;
            const id = parseInt(wrap.dataset.id, 10);
            const imgObj = (items || []).find((it) => it.id === id);
            if (!imgObj) return;
            if (wrap.classList.contains("is-revealed")) {
              imagePreviewModal(imgObj);
            }
          });
        });
      }

      function renderPagination(pg) {
        const container = $("img-pagination");
        if (!container || !pg || pg.total_pages <= 1) {
          if (container) container.innerHTML = "";
          return;
        }

        container.innerHTML = `
          <button class="btn btn-outline" id="pg-prev" ${pg.page <= 1 ? "disabled" : ""}>Previous</button>
          <span>Page <b>${pg.page}</b> of <b>${pg.total_pages}</b> (${pg.total} items)</span>
          <button class="btn btn-outline" id="pg-next" ${pg.page >= pg.total_pages ? "disabled" : ""}>Next</button>`;

        const prev = $("pg-prev"), next = $("pg-next");
        if (prev) prev.addEventListener("click", () => { if (page > 1) { page--; loadImages(); } });
        if (next) next.addEventListener("click", () => { if (page < pg.total_pages) { page++; loadImages(); } });
      }

      // ---- Actions: Submit, Train, Presets, Bulks -------------------------------------

      async function submitBatchChanges() {
        if (!pendingChanges.size) {
          return say("No unsaved changes. Select/deselect images or toggle Allow/Block first.", true);
        }
        const btn = $("btn-submit-changes");
        busy(btn, true, "Saving…");
        try {
          const updates = Array.from(pendingChanges.values());
          const res = await call("PUT", "/v1/admin/images/batch", { updates });
          pendingChanges.clear();
          say(res.message || "Saved changes successfully.", true);
          busy(btn, false, "Submit Changes");
          loadImages();
          loadCategories();
        } catch (err) {
          say("Failed to save: " + err.message);
          busy(btn, false, "Submit Changes");
        }
      }

      async function bulkSelection(selectOn) {
        const btn = $(selectOn ? "btn-select-all" : "btn-deselect-all");
        busy(btn, true, selectOn ? "Selecting…" : "Deselecting…");
        try {
          // THE SAME FILTER THE GRID IS SHOWING. Sending only the category made this button
          // act on pictures the admin could not see: there is no "gym" category - gym
          // pictures live under `physique` and `safe` - so searching "gym" shows 64 of them
          // and Deselect All used to deselect a whole category instead. The retrain after it
          // then measured a change nobody had made, which reads exactly like a broken retrain.
          const res = await call("PUT", "/v1/admin/images/batch", {
            select_all: selectOn,
            category: activeCat,
            action: activeAction,
            search: searchQuery.trim(),
          });
          say(res.message, true);
          busy(btn, false, selectOn ? "Select All" : "Deselect All");
          pendingChanges.clear();
          loadImages();
          loadCategories();
        } catch (err) {
          say("Bulk update failed: " + err.message);
          busy(btn, false, selectOn ? "Select All" : "Deselect All");
        }
      }

      async function bulkCategoryAction(action) {
        if (activeCat === "all") return;
        const btn = action === "allow" ? $("cat-bulk-allow") : $("cat-bulk-block");
        btn.textContent = "Updating…";
        try {
          const res = await call("PUT", `/v1/admin/images/categories/${encodeURIComponent(activeCat)}`, { action });
          say(res.message, true);
          btn.textContent = action === "allow" ? "ALLOW ALL" : "BLOCK ALL";
          pendingChanges.clear();
          loadImages();
          loadCategories();
        } catch (err) {
          say("Category policy update failed: " + err.message);
          btn.textContent = action === "allow" ? "ALLOW ALL" : "BLOCK ALL";
        }
      }

      async function applyPresetAction() {
        const sel = $("img-preset-sel");
        const preset = sel.value;
        const btn = $("img-apply-preset-btn");
        busy(btn, true, "Retraining model…");

        try {
          const res = await call("POST", "/v1/admin/images/preset", { preset });
          busy(btn, false, "Apply Preset & Retrain");
          pendingChanges.clear();
          trainingResultModal(res);
          loadCategories();
          loadImages();
        } catch (err) {
          say("Preset training failed: " + err.message);
          busy(btn, false, "Apply Preset & Retrain");
        }
      }

      async function triggerTraining() {
        const btn = $("btn-train-model");
        busy(btn, true, "Training Vision Model…");
        say("Extracting embeddings & fine-tuning linear probe across selected images…", "busy");

        try {
          // If there are pending changes, save them first
          if (pendingChanges.size) {
            await call("PUT", "/v1/admin/images/batch", { updates: Array.from(pendingChanges.values()) });
            pendingChanges.clear();
          }

          const res = await call("POST", "/v1/admin/images/train", { C: 1.0 });
          busy(btn, false, "⚡ Train Model");
          trainingResultModal(res);
          loadCategories();
          loadImages();
        } catch (err) {
          say("Model fine-tuning failed: " + err.message);
          busy(btn, false, "⚡ Train Model");
        }
      }

      // ---- Modals: Add Category, Upload Image, Training Results -----------------------

      function addCategoryModal() {
        const back = document.createElement("div");
        back.className = "backdrop";
        back.innerHTML = `
          <div class="modal img-modal-wide" role="dialog" aria-modal="true" aria-labelledby="cat-modal-title">
            <div class="modal-head"><h2 id="cat-modal-title">Add New Category</h2>
              <button class="btn-link" id="cat-x" aria-label="Close" type="button">✕</button></div>
            <div class="modal-body">
              <div id="cat-modal-msg" class="adm-msg" role="status"></div>
              <div class="field">
                <label for="new-cat-name">Category ID / Tag <span class="req">*</span></label>
                <input class="input" id="new-cat-name" placeholder="e.g. child_products, infant_wear" autocomplete="off">
                <p class="hint">Lower case, letters and underscores only.</p>
              </div>
              <div class="field">
                <label for="new-cat-disp">Display Label</label>
                <input class="input" id="new-cat-disp" placeholder="e.g. Baby & Child Products" autocomplete="off">
              </div>
              <div class="field">
                <label for="new-cat-act">Default Moderation Policy</label>
                <select class="select" id="new-cat-act">
                  <option value="allow">ALLOW (Images in this category are permitted)</option>
                  <option value="block">BLOCK (Images in this category are refused)</option>
                </select>
              </div>
              <div class="field">
                <label for="new-cat-desc">Description</label>
                <input class="input" id="new-cat-desc" placeholder="Brief note on what belongs in this category">
              </div>
            </div>
            <div class="modal-foot">
              <button class="btn btn-outline" id="cat-cancel" type="button">Cancel</button>
              <button class="btn btn-primary" id="cat-save" type="button">Create Category</button>
            </div>
          </div>`;

        document.body.appendChild(back);
        const q = (id) => back.querySelector("#" + id);
        const close = () => back.remove();
        q("cat-x").addEventListener("click", close);
        q("cat-cancel").addEventListener("click", close);

        q("cat-save").addEventListener("click", async () => {
          const name = q("new-cat-name").value.trim();
          const disp = q("new-cat-disp").value.trim();
          const act = q("new-cat-act").value;
          const desc = q("new-cat-desc").value.trim();

          if (!name) {
            q("cat-modal-msg").textContent = "Enter a category tag.";
            q("cat-modal-msg").className = "adm-msg err";
            return;
          }

          const btn = q("cat-save");
          busy(btn, true, "Creating…");
          try {
            await call("POST", "/v1/admin/images/categories", {
              name, display_name: disp, default_action: act, description: desc
            });
            close();
            activeCat = name.toLowerCase().replace(/ /g, "_");
            loadCategories();
            loadImages();
            say(`Created category '${name}'. You can now store and upload images into it.`, true);
          } catch (err) {
            q("cat-modal-msg").textContent = err.message;
            q("cat-modal-msg").className = "adm-msg err";
            busy(btn, false, "Create Category");
          }
        });
      }

      function uploadImageModal(prefCategory = "safe") {
        const back = document.createElement("div");
        back.className = "backdrop";

        const catOpts = categories.map((c) =>
          `<option value="${esc(c.name)}" ${c.name === prefCategory ? "selected" : ""}>${esc(c.display_name)} (${esc(c.name)})</option>`
        ).join("");

        back.innerHTML = `
          <div class="modal img-modal-wide" role="dialog" aria-modal="true" aria-labelledby="up-modal-title">
            <div class="modal-head"><h2 id="up-modal-title">Upload Image to Category</h2>
              <button class="btn-link" id="up-x" aria-label="Close" type="button">✕</button></div>
            <div class="modal-body">
              <div id="up-modal-msg" class="adm-msg" role="status"></div>
              <div class="field">
                <label for="up-file">Select Image File <span class="req">*</span></label>
                <input class="input" id="up-file" type="file" accept="image/*">
              </div>
              <div id="up-preview-wrap" style="display:none; margin: 10px 0;">
                <span style="font-size:12px; font-weight:700; color:#334155; margin-bottom:4px; display:block;">Image Preview:</span>
                <div class="img-thumb-wrap" id="up-thumb-box" style="height:170px; border-radius:8px; border:1px solid var(--line);">
                  <img id="up-preview-img" class="img-thumb" src="" alt="Upload preview">
                  <div class="img-sensitive-overlay" id="up-sensitive-overlay">
                    <div class="img-sensitive-badge" id="up-badge">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
                      <span id="up-badge-txt">Nudity / Sexual</span>
                    </div>
                    <button class="btn-reveal-img" id="up-reveal-btn" type="button">
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                      View Image
                    </button>
                  </div>
                  <button class="btn-hide-img" id="up-hide-btn" type="button" title="Blur image">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
                    Blur
                  </button>
                </div>
              </div>
              <div class="field">
                <label for="up-cat">Category <span class="req">*</span></label>
                <select class="select" id="up-cat">${catOpts}</select>
              </div>
              <div class="field">
                <label for="up-act">Moderation Action</label>
                <select class="select" id="up-act">
                  <option value="allow">ALLOW (Permit on platform / train as safe)</option>
                  <option value="block">BLOCK (Refuse on platform / train as violation)</option>
                </select>
              </div>
              <div class="field">
                <label for="up-desc">What is in this picture? <span class="req">*</span></label>
                <input class="input" id="up-desc" placeholder="a photo of fireworks for sale">
                <p class="hint">Write it as a caption — “a photo of …” reads the way the model
                  was trained and scores better than a bare noun. This sentence becomes a rule
                  in its own right, and it is the part that reaches pictures that look nothing
                  like the one you are uploading.</p>
              </div>

              <div class="pol-try">
                <div class="pol-try-head">
                  <b>Teach the checker from this upload</b>
                  <label class="adm-switch" style="transform:scale(.85)">
                    <input type="checkbox" role="switch" id="up-teach" checked>
                    <span class="adm-slider"></span><span class="adm-state">ON</span></label>
                </div>
                <ul class="teach-plan">
                  <li><b>This picture</b> — lookalikes are handled from the next upload on.</li>
                  <li><b>Your sentence</b> — added to the <span id="up-rule-name">rule</span>
                      rule, so it also reaches pictures that merely <i>are</i> this thing.</li>
                  <li><b>The next retrain</b> — this image joins the training set.</li>
                </ul>
                <p class="hint">Measured here: one picture alone catches 83% of similar ones, a
                  sentence alone 79% — together 93%, because they miss different pictures.
                  Turn this off to file the image without changing any behaviour.</p>
                <div style="margin-top:8px;">
                  <button class="btn btn-outline" id="up-try-btn" type="button">Show what the sentence matches</button>
                  <div id="up-try-out"></div>
                </div>
              </div>
            </div>
            <div class="modal-foot">
              <button class="btn btn-outline" id="up-cancel" type="button">Cancel</button>
              <button class="btn btn-primary" id="up-submit" type="button">Upload & Teach</button>
            </div>
          </div>`;

        document.body.appendChild(back);
        const q = (id) => back.querySelector("#" + id);
        const close = () => back.remove();
        q("up-x").addEventListener("click", close);
        q("up-cancel").addEventListener("click", close);

        // Name the rule the sentence will join, so the admin can see it is adding to
        // `weapon` rather than inventing something, and watch it change with the category.
        const showRule = () => { q("up-rule-name").textContent = q("up-cat").value || "rule"; };
        q("up-cat").addEventListener("change", showRule);
        showRule();

        let upFileSensitive = false;
        let upFileBlurred = false;

        function setUploadBoxState(blurred) {
          upFileBlurred = blurred;
          const box = q("up-thumb-box");
          if (!box) return;
          if (blurred) {
            box.classList.add("is-blurred");
            box.classList.remove("is-revealed");
          } else {
            box.classList.remove("is-blurred");
            box.classList.add("is-revealed");
          }
        }

        function updateUploadPreviewSensitivity(forceDefault = false) {
          const file = q("up-file").files[0];
          if (!file) return;
          const cat = (q("up-cat").value || "").toLowerCase();
          const desc = (q("up-desc").value || "").toLowerCase();
          const fn = (file.name || "").toLowerCase();
          const kws = ["sexual", "nudity", "nude", "porn", "pornograph", "nsfw", "erotic"];
          const prevSens = upFileSensitive;
          upFileSensitive = kws.some((k) => cat.includes(k) || desc.includes(k) || fn.includes(k));

          const badge = q("up-badge");
          const badgeTxt = q("up-badge-txt");

          if (upFileSensitive) {
            badge.className = "img-sensitive-badge";
            badgeTxt.textContent = "Nudity / Sexual";
            if (forceDefault || !prevSens) setUploadBoxState(true);
          } else {
            badge.className = "img-sensitive-badge neutral";
            badgeTxt.textContent = "Hidden / Blurred";
            if (forceDefault || prevSens) setUploadBoxState(false);
          }
        }

        q("up-file").addEventListener("change", (e) => {
          const file = e.target.files[0];
          if (!file) {
            q("up-preview-wrap").style.display = "none";
            return;
          }
          const reader = new FileReader();
          reader.onload = (re) => {
            q("up-preview-img").src = re.target.result;
            q("up-preview-wrap").style.display = "block";
            updateUploadPreviewSensitivity(true);
          };
          reader.readAsDataURL(file);
        });

        q("up-cat").addEventListener("change", () => updateUploadPreviewSensitivity(false));
        q("up-desc").addEventListener("input", () => updateUploadPreviewSensitivity(false));

        q("up-reveal-btn").addEventListener("click", () => setUploadBoxState(false));
        q("up-hide-btn").addEventListener("click", () => setUploadBoxState(true));
        q("up-teach").addEventListener("change", (e) => {
          e.target.closest(".adm-switch").querySelector(".adm-state").textContent =
            e.target.checked ? "ON" : "OFF";
        });
        // Same preview as the policy editor: see what the sentence picks out before it is a
        // rule. The picture being uploaded is not in the store yet, so this ranks the words
        // against what is already there - which is the half that generalises anyway.
        q("up-try-btn").addEventListener("click", async () => {
          const out = q("up-try-out");
          const phrase = q("up-desc").value.trim();
          if (!phrase) { out.innerHTML = `<p class="hint">Write the description first.</p>`; return; }
          out.innerHTML = `<p class="adm-loading"><span class="spinner dark"></span> Matching…</p>`;
          try {
            const res = await call("POST", "/v1/admin/policy/preview", { prompts: [phrase], limit: 8 });
            const rows = res.top || [];
            const byCat = {};
            rows.forEach((r) => { byCat[r.category || "?"] = (byCat[r.category || "?"] || 0) + 1; });
            const spread = Object.entries(byCat).sort((a, b) => b[1] - a[1]);
            const focused = spread.length && spread[0][1] >= Math.ceil(rows.length * 0.6);
            out.innerHTML = `
              <div class="pol-try-verdict">
                <span class="pol-tag ${focused ? "allow" : "block"}">${focused
                  ? `Focused — mostly ${esc(spread[0][0])}`
                  : "Scattered — this sentence does not single anything out"}</span>
                <span class="hint">ranked ${res.scored.toLocaleString()} stored pictures</span></div>
              <table class="pol-try-table"><tbody>${rows.map((r, i) => `<tr>
                <td class="rank">${i + 1}</td><td>${esc(r.filename || "")}</td>
                <td><span class="pol-tag ${r.action === "allow" ? "allow" : "block"}">${esc(r.category || "")}</span></td>
              </tr>`).join("")}</tbody></table>`;
          } catch (err) {
            out.innerHTML = `<div class="adm-msg err">${esc(err.message)}</div>`;
          }
        });

        q("up-submit").addEventListener("click", async () => {
          const fileInput = q("up-file");
          if (!fileInput.files.length) {
            q("up-modal-msg").textContent = "Select an image file to upload.";
            q("up-modal-msg").className = "adm-msg err";
            return;
          }

          const teach = q("up-teach").checked;
          const desc = q("up-desc").value.trim();
          if (teach && !desc) {
            q("up-modal-msg").textContent =
              "Describe the picture, or switch teaching off to file it without changing anything.";
            q("up-modal-msg").className = "adm-msg err";
            return;
          }

          const fd = new FormData();
          fd.append("file", fileInput.files[0]);
          fd.append("category", q("up-cat").value);
          fd.append("action", q("up-act").value);
          fd.append("description", desc);
          if (teach) { fd.append("teach", "1"); fd.append("phrasing", desc); }

          const btn = q("up-submit");
          busy(btn, true, teach ? "Uploading and teaching…" : "Uploading…");
          try {
            const res = await call("POST", "/v1/admin/images/upload", fd);
            close();
            // Report each channel separately. They can succeed independently - a file with no
            // readable embedding still contributes its sentence - and "uploaded" alone would
            // not say which parts of the policy actually moved.
            const t = res.taught;
            if (t && t.conflict) {
              // The image is stored; only the teaching is waiting on an answer. Resolving
              // applies it against the id that just came back, so the file is not uploaded
              // twice to answer one question.
              const p = t.pending || {};
              conflictModal({ message: res.message, conflicts: t.conflicts || [] },
                async (verdict) => {
                  try {
                    await call("PUT", "/v1/admin/policy/exemplars",
                               { ids: [p.image_id], mode: p.mode, resolve: verdict });
                    const ph = await call("POST",
                      `/v1/admin/policy/labels/${encodeURIComponent(p.label)}/phrasings`,
                      { phrasing: p.phrasing, unsafe: p.unsafe, category: p.category,
                        resolve: verdict });
                    say(verdict === "refuse"
                      ? `Kept as REFUSING. “${esc(p.phrasing)}” now belongs to the ${esc(ph.label || p.label)} rule only.`
                      : `Kept as ALLOWED. The refusing copy of “${esc(p.phrasing)}” has been removed.`,
                      true);
                  } catch (e2) {
                    say(e2.message);
                  }
                  loadCategories(); loadImages();
                });
              loadCategories(); loadImages();
            } else if (!t) {
              say(res.message || "Image uploaded.", true);
            } else {
              const bits = [];
              const ex = t.exemplar || {};
              bits.push(ex.skipped ? `no lookalike rule (${ex.skipped})`
                                   : `lookalikes handled (${ex.refusing || 0} refusing, ${ex.allowing || 0} allowing)`);
              const ph = t.phrasing || {};
              bits.push(ph.added
                ? `“${esc(desc)}” added to the ${esc(ph.label)} rule${ph.created_rule ? " (new rule)" : ""}`
                : `sentence not added — ${esc(ph.reason || ph.error || "unknown")}`);
              bits.push("included in the next retrain");
              say(`Uploaded and taught: ${bits.join("; ")}.`, true);
            }
            loadCategories();
            loadImages();
          } catch (err) {
            q("up-modal-msg").textContent = err.message;
            q("up-modal-msg").className = "adm-msg err";
            busy(btn, false, "Upload & Teach");
          }
        });
      }

      function trainingResultModal(data) {
        const back = document.createElement("div");
        back.className = "backdrop";

        const classesList = (data.classes || []).map((c) => `<span class="cat-pill" style="padding:2px 8px; font-size:11px;">${esc(c)}</span>`).join(" ");

        // WHAT THE RETRAIN ACTUALLY DID, per category. `Accuracy` above is the probe's fit to
        // the data it was just handed; it averages every category together and describes one
        // of the three channels that decide. An admin who selected 72 gym pictures, retrained,
        // read 85.4% and then watched a gym photograph publish was not misreading the screen -
        // the screen was not answering their question. These rows are each category put back
        // through the real decision path, worst first, because the only rows worth opening a
        // modal for are the ones that disagree with what the admin marked.
        // WHICH CHANNEL REFUSED, and whether the retrain actually moved the verdict. The
        // probe is one of three voters and is routinely outvoted: deselecting the gym
        // pictures and retraining takes the probe from 45 refusals to 15, and the written
        // `physique` rule picks up all 30 of them, so the verdict stays at 71 of 72 and the
        // admin sees a control that does nothing. Showing the split is what turns "the
        // retrain is broken" into "the retrain worked and a rule is overruling it".
        const causeCell = (r) => {
          const by = r.by_cause || {};
          const parts = Object.keys(by).sort((a, b) => by[b] - by[a])
            .map((k) => `${by[k]} ${esc(k)}`);
          let out = parts.length ? parts.join(", ") : "—";
          if (typeof r.probe_refused_before === "number" &&
              r.probe_refused !== r.probe_refused_before) {
            const d = r.probe_refused - r.probe_refused_before;
            out += `<div class="train-cat-delta">probe ${d > 0 ? "+" : ""}${d} vs last retrain</div>`;
          }
          if (r.masked_note) {
            out += `<div class="train-cat-masked">${esc(r.masked_note)}</div>`;
          }
          return out;
        };

        const rep = data.category_report || {};
        const rows = Object.keys(rep).sort((a, b) => (rep[a].refused_pct) - (rep[b].refused_pct));
        let reportHtml = "";
        if (rows.length) {
          reportHtml = `
            <div style="margin-top:14px;">
              <span style="font-size:12px; font-weight:700; color:#334155; display:block; margin-bottom:6px;">
                What the live check now does to these pictures
              </span>
              ${(data.withdrawn || []).length ? `
                <div class="train-withdrawn">
                  <b>Withdrawn from the model:</b>
                  ${esc((data.withdrawn || []).join(", "))}.
                  Every picture in ${(data.withdrawn || []).length > 1 ? "those categories" : "that category"}
                  is deselected, so the image model has no opinion about
                  ${(data.withdrawn || []).length > 1 ? "them" : "it"} any more - neither the
                  trained probe nor any "refuse things like this" mark in
                  ${(data.withdrawn || []).length > 1 ? "them" : "it"}. Your written policy
                  rules still apply, and anything no rule refuses will publish.
                </div>` : ""}
              <table class="train-cat-table">
                <thead><tr><th>Category</th><th>Pictures</th><th>Teaching</th><th>Refused now</th><th>Decided by</th><th>Disagrees</th></tr></thead>
                <tbody>` +
            rows.map((c) => {
              const r = rep[c];
              // Red when the admin marked these BLOCK and the system mostly publishes them,
              // or marked them ALLOW and it mostly refuses them. Either way the number the
              // admin came to check is the refusal rate, so that is what gets coloured.
              // With nothing selected there is no teaching intent to judge the rate against,
              // so fall back to what the pictures are MARKED as.
              const mostlyBlock = (r.teaching_allow || r.teaching_block)
                ? r.teaching_block >= r.teaching_allow : true;
              const good = mostlyBlock ? r.refused_pct >= 90 : r.refused_pct <= 10;
              const col = good ? "#16a34a" : (r.refused_pct >= 60 || !mostlyBlock ? "#b45309" : "#dc2626");
              // "not in training" is a real state and the one the admin lands in after
              // deselecting a category. Saying it beats an empty cell, which reads as a bug.
              const teach = (r.teaching_allow || r.teaching_block)
                ? [r.teaching_allow ? `${r.teaching_allow} allow` : "",
                   r.teaching_block ? `${r.teaching_block} block` : ""].filter(Boolean).join(" + ")
                : "not in training";
              return `<tr>
                  <td><b>${esc(c)}</b>${r.withdrawn
                      ? ` <span class="train-cat-flag withdrawn" title="Every picture deselected - the model has no opinion about this category">withdrawn</span>` : ""}${r.teaches_both_ways
                      ? ` <span class="train-cat-flag" title="This category teaches the model in both directions at once">both ways</span>` : ""}</td>
                  <td>${r.n}</td>
                  <td>${esc(teach)}</td>
                  <td style="color:${col}; font-weight:700;">${r.refused_pct}%</td>
                  <td>${causeCell(r)}</td>
                  <td>${r.contradicting
                      ? `<button class="btn-link train-cat-why" type="button" data-cat="${esc(c)}"
                           style="color:#b45309; font-weight:700;">${r.contradicting} &rsaquo;</button>`
                      : "—"}</td>
                </tr>`;
            }).join("") + `
                </tbody>
              </table>
              <p class="adm-sub" style="margin-top:6px;">${esc(data.report_note || "")}</p>
            </div>`;
        }

        back.innerHTML = `
          <div class="modal img-modal-wide" role="dialog" aria-modal="true" aria-labelledby="tr-modal-title">
            <div class="modal-head"><h2 id="tr-modal-title" style="color:#16a34a;">⚡ Model Fine-Tuning Complete</h2>
              <button class="btn-link" id="tr-x" aria-label="Close" type="button">✕</button></div>
            <div class="modal-body">
              <p style="margin:0 0 12px; font-size:14px; color:#334155;">
                The vision model linear probe has been successfully retrained and hot-reloaded into the running service.
              </p>
              <div class="train-result-box">
                <div class="train-metric-row"><span>Accuracy:</span> <b>${data.accuracy}%</b></div>
                <div class="train-metric-row"><span>Training Samples:</span> <b>${data.samples} images</b></div>
                <div class="train-metric-row"><span>Allowed / Blocked:</span> <b>${data.allowed_count} Allowed / ${data.blocked_count} Blocked</b></div>
                <div class="train-metric-row"><span>Active Minor Policy:</span> <b>${esc(data.minor_policy || "Updated")}</b></div>
                <div class="train-metric-row"><span>Duration:</span> <b>${data.duration} seconds</b></div>
                <div style="margin-top:6px;">
                  <span style="font-size:12px; font-weight:700; color:#166534; display:block; margin-bottom:4px;">Trained Output Classes:</span>
                  <div style="display:flex; flex-wrap:wrap; gap:4px;">${classesList}</div>
                </div>
              </div>
              ${reportHtml}
              <p class="adm-sub" style="color:#16a34a; font-weight:600;">✓ Changes are active immediately on /v1/moderate/media without server restart.</p>
            </div>
            <div class="modal-foot">
              <button class="btn btn-primary" id="tr-close" type="button">Done</button>
            </div>
          </div>`;

        document.body.appendChild(back);
        const close = () => back.remove();
        back.querySelector("#tr-x").addEventListener("click", close);
        back.querySelector("#tr-close").addEventListener("click", close);

        // NAME THE PICTURES, not just the count. "2 disagree" tells an admin something is
        // off; `images (13).jpg, marked BLOCK, published, best label safe 0.641` tells them
        // which picture to go and look at and which channel to argue with. Chasing one of
        // these by hand - scoring an image in a shell to find out why it published - is the
        // work this replaces.
        back.querySelectorAll(".train-cat-why").forEach((btn) => {
          btn.addEventListener("click", () => {
            const cat = btn.dataset.cat;
            const ex = (rep[cat] || {}).examples || [];
            const host = btn.closest("td");
            if (!ex.length) { return; }
            if (host.querySelector(".train-cat-ex")) {
              host.querySelector(".train-cat-ex").remove();
              return;
            }
            const box = document.createElement("div");
            box.className = "train-cat-ex";
            box.innerHTML = ex.map((e) => `
                <div class="train-cat-ex-row">
                  <b>${esc(e.filename || ("#" + e.id))}</b><br>
                  marked <b>${esc((e.marked || "").toUpperCase())}</b> &rarr;
                  <b style="color:${e.verdict === "refused" ? "#dc2626" : "#16a34a"};">${esc(e.verdict)}</b>
                  ${e.refused_by ? ` by <b>${esc(e.refused_by)}</b>` : ""}<br>
                  best label <b>${esc(e.top_label || "?")}</b> ${e.top_score}
                  &middot; probe ${e.probe_unsafe}
                </div>`).join("");
            host.appendChild(box);
          });
        });
      }

      async function trainingHistoryModal() {
        const back = document.createElement("div");
        back.className = "backdrop";
        back.innerHTML = `
          <div class="modal img-modal-wide" role="dialog" aria-modal="true" aria-labelledby="hist-modal-title">
            <div class="modal-head"><h2 id="hist-modal-title">Model Training History</h2>
              <button class="btn-link" id="hist-x" aria-label="Close" type="button">✕</button></div>
            <div class="modal-body" id="hist-body">
              <p class="adm-loading"><span class="spinner dark"></span> Loading history…</p>
            </div>
            <div class="modal-foot"><button class="btn btn-outline" id="hist-close" type="button">Close</button></div>
          </div>`;

        document.body.appendChild(back);
        const close = () => back.remove();
        back.querySelector("#hist-x").addEventListener("click", close);
        back.querySelector("#hist-close").addEventListener("click", close);

        try {
          const res = await call("GET", "/v1/admin/images/history");
          const hist = res.history || [];
          if (!hist.length) {
            back.querySelector("#hist-body").innerHTML = `<p style="color:var(--muted);">No training runs recorded yet.</p>`;
            return;
          }

          let html = `<div style="display:flex; flex-direction:column; gap:10px;">`;
          hist.forEach((h) => {
            const dateStr = new Date(h.created_at * 1000).toLocaleString();
            html += `
              <div style="background:#f8fafc; border:1px solid var(--line); border-radius:8px; padding:10px 12px; font-size:13px;">
                <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                  <b>${esc(h.model_name)}</b>
                  <span style="color:var(--muted); font-size:11px;">${esc(dateStr)}</span>
                </div>
                <div style="display:flex; gap:16px; color:#475569; font-size:12px;">
                  <span>Accuracy: <b style="color:#16a34a;">${h.accuracy ? Math.round(h.accuracy * 100) : "--"}%</b></span>
                  <span>Samples: <b>${h.total_samples}</b></span>
                  <span>Allowed: <b>${h.allowed_count}</b></span>
                  <span>Blocked: <b>${h.blocked_count}</b></span>
                </div>
              </div>`;
          });
          html += `</div>`;
          back.querySelector("#hist-body").innerHTML = html;
        } catch (err) {
          back.querySelector("#hist-body").innerHTML = `<div class="adm-msg err">${esc(err.message)}</div>`;
        }
      }

      function imagePreviewModal(img) {
        const isSens = isSensitiveImage(img);
        const pending = pendingChanges.get(img.id);
        const action = pending && pending.action !== undefined ? pending.action : img.action;
        const isAllow = action === "allow";
        let modalBlurred = isImageBlurred(img);

        const back = document.createElement("div");
        back.className = "backdrop";
        back.style.zIndex = "100";
        back.innerHTML = `
          <div class="modal img-preview-modal-dialog" role="dialog" aria-modal="true">
            <div class="modal-head">
              <div style="display:flex; align-items:center; gap:8px;">
                <h2 style="font-size:16px; margin:0; word-break:break-all;">${esc(img.filename)}</h2>
                ${isSens ? `<span class="img-sensitive-tag">18+ Nudity / Sexual</span>` : ""}
              </div>
              <button class="btn-link" id="prev-close-x" type="button" aria-label="Close">✕</button>
            </div>
            <div class="img-preview-modal-body" id="prev-body">
              <img class="img-preview-modal-img ${modalBlurred ? "blurred" : ""}" id="prev-img" src="/v1/admin/images/${img.id}/file" alt="${esc(img.filename)}">
              <div class="img-preview-modal-overlay" id="prev-overlay" style="${modalBlurred ? "" : "display:none;"}">
                <div class="img-sensitive-badge ${isSens ? "" : "neutral"}" style="font-size:12px; padding:4px 10px;">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
                  ${isSens ? "Explicit / Sexual Content" : "Hidden / Blurred Image"}
                </div>
                <button class="btn-reveal-img" id="prev-reveal-btn" type="button" style="font-size:13px; padding:8px 16px;">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                  Reveal Image
                </button>
              </div>
            </div>
            <div class="modal-body" style="padding:12px 16px; font-size:13px; background:#f8fafc; border-top:1px solid var(--line);">
              <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:10px;">
                <div><span style="color:var(--muted); font-size:11px; display:block;">Category</span><b>${esc(img.category)}</b></div>
                <div><span style="color:var(--muted); font-size:11px; display:block;">Policy Action</span>
                  <span class="img-badge ${isAllow ? "allow" : "block"}">${isAllow ? "ALLOWED" : "BLOCKED"}</span>
                </div>
                <div><span style="color:var(--muted); font-size:11px; display:block;">Source</span><b>${esc(img.source || "dataset")}</b></div>
                <div><span style="color:var(--muted); font-size:11px; display:block;">Exemplar Status</span><b>${img.is_exemplar ? "Live Rule" : "No"}</b></div>
              </div>
              ${img.description ? `<div style="margin-top:8px; font-size:12px; color:#475569;"><span style="color:var(--muted);">Description: </span>${esc(img.description)}</div>` : ""}
            </div>
            <div class="modal-foot" style="display:flex; justify-content:space-between; align-items:center;">
              <div>
                <button class="btn btn-outline" id="prev-toggle-blur-btn" type="button" style="font-size:12px;">
                  ${modalBlurred ? "Show Image" : "Blur Image"}
                </button>
              </div>
              <div style="display:flex; gap:8px;">
                <button class="btn btn-outline" id="prev-close-btn" type="button">Close</button>
              </div>
            </div>
          </div>`;

        document.body.appendChild(back);
        const close = () => back.remove();
        back.querySelector("#prev-close-x").addEventListener("click", close);
        back.querySelector("#prev-close-btn").addEventListener("click", close);
        back.addEventListener("click", (e) => { if (e.target === back) close(); });

        const prevImg = back.querySelector("#prev-img");
        const prevOverlay = back.querySelector("#prev-overlay");
        const toggleBtn = back.querySelector("#prev-toggle-blur-btn");

        function setModalBlur(blurred) {
          modalBlurred = blurred;
          if (blurred) {
            prevImg.classList.add("blurred");
            prevOverlay.style.display = "flex";
            if (toggleBtn) toggleBtn.textContent = "Show Image";
            if (isSens) {
              revealedImageIds.delete(img.id);
            } else {
              blurredImageIds.add(img.id);
            }
            const cardWrap = document.querySelector(`.img-thumb-wrap[data-id="${img.id}"]`);
            if (cardWrap) {
              cardWrap.classList.remove("is-revealed");
              cardWrap.classList.add("is-blurred");
            }
          } else {
            prevImg.classList.remove("blurred");
            prevOverlay.style.display = "none";
            if (toggleBtn) toggleBtn.textContent = "Blur Image";
            if (isSens) {
              revealedImageIds.add(img.id);
            } else {
              blurredImageIds.delete(img.id);
            }
            const cardWrap = document.querySelector(`.img-thumb-wrap[data-id="${img.id}"]`);
            if (cardWrap) {
              cardWrap.classList.remove("is-blurred");
              cardWrap.classList.add("is-revealed");
            }
          }
        }

        const revealBtn = back.querySelector("#prev-reveal-btn");
        if (revealBtn) revealBtn.addEventListener("click", () => setModalBlur(false));
        if (toggleBtn) toggleBtn.addEventListener("click", () => setModalBlur(!modalBlurred));
      }

      // Initialize
      renderShell();
      loadCategories();
      loadImages();
    }

    // ---- settings popup ----------------------------------------------------------------
    function settingsModal() {
      const back = document.createElement("div");
      back.className = "backdrop";
      back.innerHTML = `<div class="modal adm-modal" role="dialog" aria-modal="true" aria-labelledby="adm-title">
        <div class="modal-head"><h2 id="adm-title">Moderation settings</h2>
          <button class="btn-link" id="mm-x" aria-label="Close" type="button">✕</button></div>
        <div class="modal-body" id="mm-body"><p class="adm-loading"><span class="spinner dark"></span> Loading saved settings…</p></div>
        <div class="modal-foot" id="mm-foot"></div></div>`;
      document.body.appendChild(back);
      const q = (id) => back.querySelector("#" + id);
      const close = () => back.remove();
      let saved = null;
      let draft = null;
      // WHICH PAGE THE BAR IS AIMED AT. "all" is the setting every page follows; a content
      // type is that one page on its own. The server has taken a level per content type from
      // the start - `_settings_view` has always returned `surfaces` - and the dashboard drove
      // only the all-at-once endpoint, so the per-page settings existed and could be reached
      // by nothing but a hand-edited YAML file.
      let scope = "all";
      // Taken once when the modal opens: the nav does not change while it is on screen, and
      // re-reading the DOM on every repaint would be work for nothing.
      const SURFACES = surfaceList();
      // Pending per-page changes, applied on Save. Keyed by content type; absent means the
      // page is left exactly as it is, which is not the same as setting it to the default.
      let overrides = {};

      const sessionEnded = (err) => { close(); loginView(err.message); };
      back.addEventListener("keydown", (ev) => { if (ev.key === "Escape") close(); });
      q("mm-x").addEventListener("click", close);

      const label = (s) => `${s.enabled ? "ON" : "OFF"} · ${s.level ? levelName(s.level) : (s.mixed ? "mixed" : "project default")}`;

      /** What one page is set to right now: a pending change if there is one, else what the
       *  server last reported, else the everything-setting it inherits. */
      function surfaceState(ct) {
        if (overrides[ct]) return { ...overrides[ct], pending: true };
        const srv = (saved.surfaces || {})[ct];
        if (srv && (srv.level || srv.enabled === false)) {
          return { enabled: srv.enabled !== false, level: srv.level || null, pending: false };
        }
        return { enabled: srv ? srv.enabled !== false : true, level: null, pending: false,
                 inherited: true };
      }

      /** The scope the bar is currently editing, as {enabled, level}. */
      function scopeState() {
        if (scope === "all") return { enabled: draft.enabled, level: draft.level };
        const st = surfaceState(scope);
        return { enabled: st.enabled, level: st.level || draft.level };
      }

      function setScopeState(patch) {
        if (scope === "all") { Object.assign(draft, patch); return; }
        const now = scopeState();
        overrides[scope] = { enabled: now.enabled, level: now.level, ...patch };
      }

      const heatNumber = (lvl) => (HEAT_BY_NAME[lvl] || {}).level || NAMED_AT[lvl] || 5;

      /** One scope chip. Shared so the tabbed and the off-tab groups cannot drift apart. */
      function chip(sf) {
        const st = surfaceState(sf.ct);
        const own = !st.inherited;
        const val = !st.enabled ? "OFF" : own ? `L${heatNumber(st.level)}` : "default";
        const title = sf.noTab
          ? `${sf.ct} — published elsewhere on BONC, no tab in this dashboard`
          : `${sf.tab} tab`;
        return `<button type="button" class="scope-chip ${scope === sf.ct ? "active" : ""} ${own ? "own" : ""} ${st.pending ? "pending" : ""}"
                  data-scope="${esc(sf.ct)}" title="${esc(title)}">
          <span class="scope-name">${esc(sf.tab)}</span>
          <span class="scope-val">${esc(val)}</span>
        </button>`;
      }

      function draw(msg = "", ok = false, confirming = false) {
        const lv = saved.levels;
        q("mm-body").innerHTML = `
          <div class="adm-msg ${msg ? (ok ? "ok" : "err") : ""}" role="status" aria-live="polite">${esc(msg)}</div>
          <p class="adm-current">Currently saved: <b>${esc(label(saved))}</b></p>
          ${saved.mixed ? `<p class="adm-current">Pages differ right now - the chips below show which.
            Changing <b>Every page</b> makes them all match again.</p>` : ""}
          <!-- WHICH PAGE THIS CHANGES. Everything below - the toggle, the bar, the policy
               card - applies to whatever is picked here. A page with its own setting says so
               on its chip, so the ones that differ from the default are visible without
               opening each in turn. -->
          <div class="scope-picker" ${confirming ? 'style="opacity:.5;pointer-events:none"' : ""}>
            <div class="scope-label">Applies to</div>
            <div class="scope-chips" id="mm-scopes">
              <button type="button" class="scope-chip ${scope === "all" ? "active" : ""}" data-scope="all">
                <span class="scope-name">Every page</span>
                <!-- saved.enabled is all() over the pages, so switching ONE page off made
                     this read OFF - "everything is off" - while every other chip still
                     showed its own level. When the pages disagree the honest summary is that
                     they disagree, not whichever value the aggregate collapsed to. -->
                <span class="scope-val">${esc(saved.mixed ? "mixed"
                  : (draft.enabled ? `L${heatNumber(draft.level)}` : "OFF"))}</span>
              </button>
              ${SURFACES.filter((sf) => !sf.noTab).map(chip).join("")}
            </div>
            <!-- The surfaces with no tab, grouped and explained rather than badged. A chip
                 reading "product_listing NO TAB" sitting in the same row as "Articles" makes
                 an admin account for the dashboard's internals before they can say anything
                 about the setting. One sentence does that for them. -->
            ${SURFACES.some((sf) => sf.noTab) ? `
            <div class="scope-group">
              <div class="scope-group-label">Elsewhere on BONC</div>
              <p class="scope-group-note">Listings, ads and feed posts are checked by the same
                moderator, but they are published from other parts of BONC and have no tab
                here. Their levels are set the same way.</p>
              <div class="scope-chips" id="mm-scopes-offtab">
                ${SURFACES.filter((sf) => sf.noTab).map(chip).join("")}
              </div>
            </div>` : ""}
            ${scope === "all"
              ? `<p class="scope-note">Changing this sets <b>every</b> page at once and clears
                   any page-by-page settings below.</p>`
              : `<p class="scope-note">Changing this affects <b>${esc((SURFACES.find((x) => x.ct === scope) || {}).tab || scope)}</b>
                   only. <button type="button" class="btn-link" id="mm-follow">Match the default
                   (L${heatNumber(draft.level)})</button></p>`}
          </div>
          <div class="adm-switch-row">
            <div><b>AI moderation</b><div class="adm-sub">${scopeState().enabled
                ? `ON: ${scope === "all" ? "content is checked before it is published." : "this page is checked before publishing."}`
                : `OFF: ${scope === "all" ? "content on every page is published without AI checks." : "this page publishes with no AI check."}`}</div></div>
            <label class="adm-switch"><input type="checkbox" role="switch" id="mm-enabled" ${scopeState().enabled ? "checked" : ""} ${confirming ? "disabled" : ""}>
              <span class="adm-slider"></span><span class="adm-state">${scopeState().enabled ? "ON" : "OFF"}</span></label>
          </div>
          ${heatAvailable(lv) ? `
          <div class="heat-control-panel" ${confirming ? 'style="opacity:.5;pointer-events:none"' : ""}>
            <div class="heat-header">
              <div class="heat-title-group">
                <span style="font-size:18px;">🌡️</span>
                <h3>Moderation Heat Bar</h3>
              </div>
              <div class="heat-badges">
                <span class="heat-badge-lvl" id="heat-badge-lvl"></span>
                <span class="heat-badge-val" id="heat-badge-val"></span>
                <span class="heat-badge-status" id="heat-badge-status"></span>
              </div>
            </div>
            <p style="font-size:13px; color:var(--muted); margin:0 0 12px;">
              Drag the slider, or click a step. <strong>Cold</strong> refuses more;
              <strong>hot</strong> publishes more.
            </p>
            <div class="heat-track-container">
              <div class="heat-track-bar" id="heat-track-bar"></div>
              <input type="range" class="heat-slider" id="heat-slider" min="1" max="10" step="1"
                     value="5" aria-label="Moderation heat bar, level 1 to 10">
            </div>
            <div class="heat-scale-bounds">
              <span>❄️ Strictest</span><span>⚖️ Balanced</span><span>🔥 Most permissive</span>
            </div>
            <div class="heat-steppers" id="heat-steppers"></div>
            <div class="heat-policy-card">
              <div class="heat-policy-head">
                <strong id="heat-policy-name"></strong>
                <span class="heat-policy-tone" id="heat-policy-tone"></span>
              </div>
              <div class="heat-policy-grid">
                <div class="heat-allowed-box"><b>✅ PUBLISHES</b>
                  <span id="heat-policy-allowed"></span></div>
                <div class="heat-blocked-box"><b>⛔ REFUSES (cumulative)</b>
                  <span id="heat-policy-blocked"></span></div>
              </div>
              <div class="heat-monotonic-note"><span>🔒</span>
                <span><strong>Cumulative:</strong> whatever is refused at Level 9 is also refused
                at Level 8, and so on down to Level 1.</span></div>
            </div>
          </div>` : `
          <fieldset class="adm-levels" ${confirming ? "disabled" : ""}><legend>Moderation level</legend>
            ${lv.map((n) => `<label class="adm-level"><input type="radio" name="mm-level" value="${esc(n)}" ${draft.level === n ? "checked" : ""}>
              <span><b>${esc(levelName(n))}</b><span class="adm-sub">${esc(LEVEL_TEXT[n] || "")}</span></span></label>`).join("")}
          </fieldset>`}
          ${scopeState().enabled ? "" : `<p class="adm-sub">The level is remembered for when moderation is turned back ON.</p>`}
          ${Object.keys(overrides).length ? `<p class="adm-warn">Unsaved page settings:
            ${esc(Object.keys(overrides).map((ct) => (SURFACES.find((x) => x.ct === ct) || {}).tab || ct).join(", "))}.
            They are applied when you press Save.</p>` : ""}
          ${confirming ? `<div class="adm-confirm" role="alertdialog"><b>Turn moderation OFF?</b>
            <p>Everything people submit, on every page, will be published with no AI check. Login and file-safety checks stay on.</p>
            <div class="adm-row"><button class="btn btn-outline" id="mm-keep" type="button">Keep it ON</button>
              <button class="btn btn-primary adm-danger" id="mm-yes" type="button">Yes, turn it OFF</button></div></div>` : ""}`;
        q("mm-foot").innerHTML = confirming ? "" : `
          <button class="btn btn-outline" id="mm-cancel" type="button">Cancel</button>
          <button class="btn btn-outline" id="mm-logout" type="button">Log out</button>
          <button class="btn btn-primary" id="mm-save" type="button">Save settings</button>`;
        bind(confirming);
      }

      /** The bar needs all ten levels present server-side; otherwise the radio list stands. */
      function heatAvailable(levels) {
        return HEAT_LEVELS.every((c) => (levels || []).includes(c.name));
      }

      /** Paint the bar at `lvl` and record it on the draft. The draft carries the API's key
       *  (`level_7`), not the number, so saving is unchanged from the radio version. */
      function applyHeat(lvl) {
        const n = Math.max(1, Math.min(10, parseInt(lvl, 10) || 5));
        const cfg = HEAT_LEVELS[n - 1];
        setScopeState({ level: cfg.name });
        q("heat-slider").value = n;
        q("heat-badge-lvl").textContent = `Level ${cfg.level} / 10`;
        q("heat-badge-val").textContent = cfg.val.toFixed(2);
        q("heat-badge-status").textContent = `${cfg.icon} ${cfg.label}`;
        q("heat-badge-status").style.backgroundColor = cfg.color;
        q("heat-policy-name").textContent = `Level ${cfg.level} — ${cfg.label} (${cfg.val.toFixed(2)})`;
        q("heat-policy-tone").textContent = cfg.tone;
        q("heat-policy-allowed").textContent = cfg.allowed;
        q("heat-policy-blocked").textContent = cfg.blocked;
        back.querySelectorAll(".heat-step-btn").forEach((btn) => {
          const on = parseInt(btn.dataset.level, 10) === n;
          btn.classList.toggle("active", on);
          btn.style.borderColor = on ? cfg.color : "";
          btn.style.boxShadow = on ? `0 0 0 2px ${cfg.color}33` : "";
        });
      }

      function wireHeat() {
        q("heat-steppers").innerHTML = HEAT_LEVELS.map((cfg) => `
          <button type="button" class="heat-step-btn" data-level="${cfg.level}">
            <span class="step-lvl">L${cfg.level}</span>
            <span class="step-val">${cfg.val.toFixed(2)}</span>
          </button>`).join("");
        q("heat-slider").addEventListener("input", (e) => applyHeat(e.target.value));
        q("heat-steppers").addEventListener("click", (e) => {
          const btn = e.target.closest(".heat-step-btn");
          if (btn) applyHeat(btn.dataset.level);
        });
        // Open where the saved setting actually is. A named level (strict/medium/lenient) is
        // still valid server-side, so it maps to its nearest step instead of being read as
        // "nothing set" and quietly reset to Balanced the first time anyone saves.
        applyHeat(heatNumber(scopeState().level));
      }

      function bind(confirming) {
        if (confirming) {
          q("mm-keep").addEventListener("click", () => { draft.enabled = true; draw(); });
          q("mm-yes").addEventListener("click", () => doSave());
          return;
        }
        q("mm-enabled").addEventListener("change", (ev) => {
          setScopeState({ enabled: ev.target.checked }); draw();
        });
        ["mm-scopes", "mm-scopes-offtab"].forEach((id) => {
          if (!q(id)) return;
          q(id).addEventListener("click", (ev) => {
            const btn = ev.target.closest(".scope-chip");
            if (!btn) return;
            scope = btn.dataset.scope;
            draw();
          });
        });
        if (q("mm-follow")) {
          // COPIES the default rather than going back to inheriting it. set_feature() takes
          // level=None to mean "leave it alone", so there is no way through this API to clear
          // a page's own bar once it has one - and a button labelled "follow the default"
          // that actually pins today's default would be a lie the first time the default
          // moved. So it says what it does. Clearing needs a server change in levels.py,
          // which belongs to the moderation-levels work.
          q("mm-follow").addEventListener("click", () => {
            setScopeState({ enabled: draft.enabled, level: draft.level });
            draw();
          });
        }
        if (q("heat-slider")) {
          wireHeat();
        } else {
          back.querySelectorAll("input[name=mm-level]").forEach((r) =>
            r.addEventListener("change", () => { draft.level = r.value; }));
        }
        q("mm-cancel").addEventListener("click", close);
        q("mm-logout").addEventListener("click", async () => {
          try { await call("POST", "/v1/admin/auth/logout"); } catch { /* ignore */ }
          close(); loginView("You have been logged out.", true);
        });
        q("mm-save").addEventListener("click", () => {
          if (!draft.level) return draw("Choose a moderation level.");
          // The confirmation is for switching the whole site off. Turning one page off is a
          // smaller act with a smaller blast radius and does not need the alarm.
          if (!draft.enabled && saved.enabled) return draw("", false, true);
          doSave();
        });
      }

      async function doSave() {
        const foot = q("mm-foot");
        foot.innerHTML = `<button class="btn btn-primary" disabled><span class="spinner"></span> Saving…</button>`;
        // THE EVERYTHING-SETTING GOES FIRST, because it writes to every page and would
        // otherwise overwrite the per-page settings sent before it. Sending them the other
        // way round silently discarded whichever page the admin had just set.
        const globalChanged = draft.enabled !== saved.enabled || draft.level !== saved.level;
        const pages = Object.entries(overrides);
        try {
          let next = saved;
          if (globalChanged || !pages.length) {
            next = await call("PUT", "/v1/admin/moderation-settings",
                              { enabled: draft.enabled, level: draft.level });
          }
          for (const [ct, st] of pages) {
            const body = { enabled: st.enabled };
            if (st.level) body.level = st.level;
            await call("PUT", `/v1/admin/moderation-levels/${encodeURIComponent(ct)}`, body);
          }
          // Re-read rather than patching what we think we sent: the server decides what a
          // page ends up at, and the chips have to show that and not our guess at it.
          saved = await call("GET", "/v1/admin/moderation-settings");
          draft = { enabled: saved.enabled, level: saved.level || draft.level };
          const named = pages.map(([ct]) =>
            (SURFACES.find((x) => x.ct === ct) || {}).tab || ct);
          overrides = {};
          draw(pages.length
            ? `Saved. ${esc(named.join(", "))} ${pages.length > 1 ? "have" : "has"} its own setting; every other page is ${label(saved)}.`
            : `Saved. Moderation is now ${label(saved)} on every page.`, true);
        } catch (err) {
          if (err instanceof SessionExpired) return sessionEnded(err);
          // Part of a multi-page save may already have landed, so the saved state is re-read
          // instead of assumed. Saying "nothing was changed" when something was is worse
          // than saying nothing.
          try { saved = await call("GET", "/v1/admin/moderation-settings"); } catch (_e) { /* keep what we had */ }
          draft = { enabled: saved.enabled, level: saved.level || draft.level };
          overrides = {};
          draw(`${err.message} The settings shown are what the server has now.`);
        }
      }

      (async () => {
        try {
          saved = await call("GET", "/v1/admin/moderation-settings");
          draft = { enabled: saved.enabled, level: saved.level || (saved.levels.includes("medium") ? "medium" : saved.levels[0]) };
          draw();
          q("mm-save").focus();
        } catch (err) {
          if (err instanceof SessionExpired) return sessionEnded(err);
          q("mm-body").innerHTML = `<div class="adm-msg err">${esc(err.message)}</div>`;
          q("mm-foot").innerHTML = `<button class="btn btn-outline" id="mm-cancel">Close</button>`;
          q("mm-cancel").addEventListener("click", close);
        }
      })();
    }

    // ---- start: are we already logged in? ----------------------------------------------
    root.innerHTML = `<p class="adm-loading"><span class="spinner dark"></span> Checking your login…</p>`;
    call("GET", "/v1/admin/auth/me").then((r) => signedIn(r.email, false)).catch(() => loginView());
  }

  window.BONCAdmin = { mount };
})();
