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
    throw new Error(data.message || (data.error && data.error.message) || `Something went wrong (${res.status}).`);
  }

  function mount(root) {
    let email = "";            // kept only while the forgot-password steps are on screen
    let resetToken = "";
    let cooldownTimer = null;
    let currentAdmin = "";

    const $ = (id) => root.querySelector("#" + id);
    const say = (text, ok = false) => {
      const el = $("adm-msg");
      if (!el) return;
      el.textContent = text || "";
      el.className = "adm-msg " + (text ? (ok ? "ok" : "err") : "");
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
        try {
          await call("PUT", `/v1/admin/policy/labels/${encodeURIComponent(name)}`, entry);
          await load(`${what} applied. The model is using it now.`, true);
        } catch (err) {
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
          try {
            await call("PUT", `/v1/admin/policy/labels/${encodeURIComponent(nm)}`, entry);
            close();
            await load(`“${nm}” applied. The model is using it now.`, true);
          } catch (err) {
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
              </div>

              <div class="img-actions-right">
                <button class="btn btn-outline" id="btn-upload-modal" type="button" style="border-color:#0284c7; color:#0284c7;">+ Upload Image</button>
                <button class="btn btn-primary" id="btn-submit-changes" type="button" style="background:#2563eb;">Submit Changes</button>
                <button class="btn btn-primary" id="btn-train-model" type="button" style="background:#16a34a; border-color:#16a34a;">⚡ Train Model</button>
              </div>
            </div>

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

          html += `
            <div class="img-card ${isAllow ? "allow-state" : "block-state"} ${isSelected ? "" : "deselected"}" data-id="${img.id}">
              <div class="img-thumb-wrap">
                <label class="img-check-label" title="Select or deselect this image for model training">
                  <input class="img-check-input" type="checkbox" data-id="${img.id}" ${isSelected ? "checked" : ""}>
                  <span>Train</span>
                </label>
                <img class="img-thumb" src="/v1/admin/images/${img.id}/file" alt="${esc(img.filename)}" loading="lazy" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'100\\' height=\\'100\\'><rect width=\\'100\\' height=\\'100\\' fill=\\'%23eee\\'/><text x=\\'50%\\' y=\\'50%\\' fill=\\'%23aaa\\' dominant-baseline=\\'middle\\' text-anchor=\\'middle\\'>No preview</text></svg>'">
              </div>
              <div class="img-card-body">
                <div class="img-card-meta">
                  <span class="img-cat-tag">${esc(img.category)}</span>
                  <span class="img-badge ${isAllow ? "allow" : "block"}">${isAllow ? "ALLOWED" : "BLOCKED"}</span>
                </div>
                <div class="img-filename" title="${esc(img.filename)}">${esc(img.filename)}</div>
              </div>
              <div class="img-card-foot">
                <button class="btn-toggle-act" type="button" data-id="${img.id}" data-action="${isAllow ? "block" : "allow"}">
                  ${isAllow ? "Switch to BLOCK" : "Switch to ALLOW"}
                </button>
              </div>
            </div>`;
        });
        html += `</div>`;
        grid.innerHTML = html;

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
          const res = await call("PUT", "/v1/admin/images/batch", {
            select_all: selectOn,
            category: activeCat,
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
        say("Extracting embeddings & fine-tuning linear probe across selected images…");

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
                <label for="up-desc">Description (optional)</label>
                <input class="input" id="up-desc" placeholder="e.g. Mamy Poko Pants baby diaper packaging photo">
              </div>
            </div>
            <div class="modal-foot">
              <button class="btn btn-outline" id="up-cancel" type="button">Cancel</button>
              <button class="btn btn-primary" id="up-submit" type="button">Upload & Embed</button>
            </div>
          </div>`;

        document.body.appendChild(back);
        const q = (id) => back.querySelector("#" + id);
        const close = () => back.remove();
        q("up-x").addEventListener("click", close);
        q("up-cancel").addEventListener("click", close);

        q("up-submit").addEventListener("click", async () => {
          const fileInput = q("up-file");
          if (!fileInput.files.length) {
            q("up-modal-msg").textContent = "Select an image file to upload.";
            q("up-modal-msg").className = "adm-msg err";
            return;
          }

          const fd = new FormData();
          fd.append("file", fileInput.files[0]);
          fd.append("category", q("up-cat").value);
          fd.append("action", q("up-act").value);
          fd.append("description", q("up-desc").value.trim());

          const btn = q("up-submit");
          busy(btn, true, "Uploading & generating embeddings…");
          try {
            const res = await call("POST", "/v1/admin/images/upload", fd);
            close();
            say(res.message || "Image uploaded and stored successfully.", true);
            loadCategories();
            loadImages();
          } catch (err) {
            q("up-modal-msg").textContent = err.message;
            q("up-modal-msg").className = "adm-msg err";
            busy(btn, false, "Upload & Embed");
          }
        });
      }

      function trainingResultModal(data) {
        const back = document.createElement("div");
        back.className = "backdrop";

        const classesList = (data.classes || []).map((c) => `<span class="cat-pill" style="padding:2px 8px; font-size:11px;">${esc(c)}</span>`).join(" ");

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

      const sessionEnded = (err) => { close(); loginView(err.message); };
      back.addEventListener("keydown", (ev) => { if (ev.key === "Escape") close(); });
      q("mm-x").addEventListener("click", close);

      const label = (s) => `${s.enabled ? "ON" : "OFF"} · ${s.level ? levelName(s.level) : (s.mixed ? "mixed" : "project default")}`;

      function draw(msg = "", ok = false, confirming = false) {
        const lv = saved.levels;
        q("mm-body").innerHTML = `
          <div class="adm-msg ${msg ? (ok ? "ok" : "err") : ""}" role="status" aria-live="polite">${esc(msg)}</div>
          <p class="adm-current">Currently saved: <b>${esc(label(saved))}</b></p>
          ${saved.mixed ? `<p class="adm-warn">Some pages have different settings right now. Saving will make them all match.</p>` : ""}
          <div class="adm-switch-row">
            <div><b>AI moderation</b><div class="adm-sub">${draft.enabled ? "ON: content is checked before it is published." : "OFF: content on every page is published without AI checks."}</div></div>
            <label class="adm-switch"><input type="checkbox" role="switch" id="mm-enabled" ${draft.enabled ? "checked" : ""} ${confirming ? "disabled" : ""}>
              <span class="adm-slider"></span><span class="adm-state">${draft.enabled ? "ON" : "OFF"}</span></label>
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
          ${draft.enabled ? "" : `<p class="adm-sub">The level is remembered for when moderation is turned back ON.</p>`}
          <p class="adm-sub">Applies to every content type: Articles, Add Business, Videos, Requests, Proposals, Business Proposals and the rest.</p>
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
        draft.level = cfg.name;
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
        const cur = HEAT_BY_NAME[draft.level];
        applyHeat(cur ? cur.level : (NAMED_AT[draft.level] || 5));
      }

      function bind(confirming) {
        if (confirming) {
          q("mm-keep").addEventListener("click", () => { draft.enabled = true; draw(); });
          q("mm-yes").addEventListener("click", () => doSave());
          return;
        }
        q("mm-enabled").addEventListener("change", (ev) => { draft.enabled = ev.target.checked; draw(); });
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
          if (!draft.enabled && saved.enabled) return draw("", false, true);
          doSave();
        });
      }

      async function doSave() {
        const foot = q("mm-foot");
        foot.innerHTML = `<button class="btn btn-primary" disabled><span class="spinner"></span> Saving…</button>`;
        try {
          const next = await call("PUT", "/v1/admin/moderation-settings", { enabled: draft.enabled, level: draft.level });
          saved = next;
          draft = { enabled: next.enabled, level: next.level };
          draw(`Saved. Moderation is now ${label(next)} on every page.`, true);
        } catch (err) {
          if (err instanceof SessionExpired) return sessionEnded(err);
          draft = { enabled: saved.enabled, level: saved.level };
          draw(`${err.message} Nothing was changed. Check your connection and try again.`);
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
