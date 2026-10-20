/* Admin: login, forgot password, and the moderation settings popup.
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
    // The ten cumulative levels from config/moderation_levels.yaml. Without these the radio
    // list renders a bare "Level_1" and no description, because the fieldset draws whatever
    // levels the server returns and looks the text up here.
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
  // "level_7" reads as "Level 7" in the UI; the key keeps its underscore for the API.
  const levelName = (s) => cap(String(s).replace(/_/g, " "));
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  class SessionExpired extends Error {}

  async function call(method, url, body) {
    const res = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "bonc-admin" },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    let data = {};
    try { data = await res.json(); } catch { /* not JSON */ }
    if (res.ok) return data;
    if (res.status === 401 && !url.includes("/auth/login") && !url.includes("/password/")) {
      throw new SessionExpired("Your session has ended. Please log in again.");
    }
    throw new Error(data.message || `Something went wrong (${res.status}).`);
  }

  function mount(root) {
    let email = "";            // kept only while the forgot-password steps are on screen
    let resetToken = "";
    let cooldownTimer = null;

    const $ = (id) => root.querySelector("#" + id);
    const say = (text, ok = false) => {
      const el = $("adm-msg");
      if (!el) return;
      el.textContent = text || "";
      el.className = "adm-msg " + (text ? (ok ? "ok" : "err") : "");
    };
    const busy = (btn, on, label) => {
      btn.disabled = on;
      btn.innerHTML = on ? `<span class="spinner"></span> ${esc(label)}` : esc(label);
    };

    function frame(title, sub, body) {
      clearInterval(cooldownTimer);
      root.innerHTML = `<h1>${esc(title)}</h1><p class="subtitle">${esc(sub)}</p>
        <section class="card adm-card"><div id="adm-msg" class="adm-msg" role="status" aria-live="polite"></div>${body}</section>`;
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
        <div class="field"><label for="adm-email">Email address</label>
          <input class="input" id="adm-email" type="email" autocomplete="username" value="${esc(email)}"></div>
        ${passwordField("adm-pass", "Password", "current-password")}
        <div class="adm-row"><button class="btn btn-primary" id="adm-login">Log in</button>
          <button class="btn-link" id="adm-forgot" type="button">Forgot password?</button></div>`);
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
          signedIn(r.email, true);
        } catch (err) {
          say(err.message);
          busy(btn, false, "Log in");
        }
      };
      $("adm-login").addEventListener("click", submit);
      root.querySelectorAll("input").forEach((i) => i.addEventListener("keydown", (ev) => { if (ev.key === "Enter") submit(); }));
      $("adm-forgot").addEventListener("click", () => { email = $("adm-email").value.trim(); forgotView(); });
    }

    // ---- forgot password: email -> code -> new password -------------------------------
    function forgotView() {
      frame("Forgot password", "We will email a 6-digit code to the administrator's address.", `
        <div class="field"><label for="adm-email">Registered email address</label>
          <input class="input" id="adm-email" type="email" autocomplete="username" value="${esc(email)}"></div>
        <div class="adm-row"><button class="btn btn-primary" id="adm-send">Send code</button>
          <button class="btn-link" id="adm-back" type="button">Back to login</button></div>`);
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
        <div class="field"><label for="adm-code">6-digit code</label>
          <input class="input" id="adm-code" inputmode="numeric" maxlength="6" autocomplete="one-time-code"></div>
        <div class="adm-row"><button class="btn btn-primary" id="adm-verify">Verify code</button>
          <button class="btn btn-outline" id="adm-resend" type="button" disabled>Resend code</button>
          <button class="btn-link" id="adm-back" type="button">Back to login</button></div>`);
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
        ${passwordField("adm-new", "New password", "new-password")}
        ${passwordField("adm-new2", "Confirm new password", "new-password")}
        <div class="adm-row"><button class="btn btn-primary" id="adm-reset">Save new password</button></div>`);
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

    // ---- signed in ---------------------------------------------------------------------
    function signedIn(who, openPopup) {
      frame("Admin", "Manage AI content moderation.", `
        <p class="adm-who">Signed in as <b>${esc(who)}</b></p>
        <div class="adm-row"><button class="btn btn-primary" id="adm-open">Moderation settings</button>
          <button class="btn btn-outline" id="adm-logout">Log out</button></div>`);
      $("adm-open").addEventListener("click", () => settingsModal());
      $("adm-logout").addEventListener("click", async () => {
        try { await call("POST", "/v1/admin/auth/logout"); } catch { /* already out */ }
        loginView("You have been logged out.", true);
      });
      if (openPopup) settingsModal();
    }

    // ---- the settings popup ------------------------------------------------------------
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
      let saved = null;          // what the server really has
      let draft = null;          // what the switches show now

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
          <fieldset class="adm-levels" ${confirming ? "disabled" : ""}><legend>Moderation level</legend>
            ${lv.map((n) => `<label class="adm-level"><input type="radio" name="mm-level" value="${esc(n)}" ${draft.level === n ? "checked" : ""}>
              <span><b>${esc(levelName(n))}</b><span class="adm-sub">${esc(LEVEL_TEXT[n] || "")}</span></span></label>`).join("")}
          </fieldset>
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

      function bind(confirming) {
        if (confirming) {
          q("mm-keep").addEventListener("click", () => { draft.enabled = true; draw(); });
          q("mm-yes").addEventListener("click", () => doSave());
          return;
        }
        q("mm-enabled").addEventListener("change", (ev) => { draft.enabled = ev.target.checked; draw(); });
        back.querySelectorAll("input[name=mm-level]").forEach((r) =>
          r.addEventListener("change", () => { draft.level = r.value; }));
        q("mm-cancel").addEventListener("click", close);
        q("mm-logout").addEventListener("click", async () => {
          try { await call("POST", "/v1/admin/auth/logout"); } catch { /* ignore */ }
          close(); loginView("You have been logged out.", true);
        });
        q("mm-save").addEventListener("click", () => {
          if (!draft.level) return draw("Choose a moderation level.");
          if (!draft.enabled && saved.enabled) return draw("", false, true);    // ask before OFF
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
          draft = { enabled: saved.enabled, level: saved.level };        // put the screen back as it was
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
