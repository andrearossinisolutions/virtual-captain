"use strict";
/* Shared helpers. Dynamic text is only ever written with textContent. */
var VC = (function () {
  var csrf = "";

  var ERRORS = {
    invalid_request: "Please check the values you entered.",
    invalid_token: "This link is invalid or has expired.",
    invalid_credentials: "Email or password is incorrect.",
    too_many_attempts: "Too many attempts. Please try again later.",
    busy: "Server busy, try again in a few seconds.",
    unauthenticated: "Your session has ended. Please sign in again.",
    too_large: "Please check the values you entered.",
    confirm_mismatch: "The characters do not match the code shown on your PC.",
    invalid_code: "This code is not valid, has expired or was already used. Start again from the program.",
    not_found: "This program is no longer linked.",
    forbidden: "Please reload the page and try again."
  };

  function $(id) { return document.getElementById(id); }

  function message(node, text, kind) {
    if (!node) { return; }
    node.textContent = text;
    node.className = "msg" + (kind ? " " + kind : "");
  }

  function errorText(result) {
    var code = result && result.data && result.data.error;
    if (code && ERRORS[code]) { return ERRORS[code]; }
    return "Something went wrong. Please try again.";
  }

  /* JSON request, same origin only. Never throws: returns {ok, status, data}. */
  function api(path, body, method) {
    var headers = { "Accept": "application/json" };
    var init = { method: method || "POST", credentials: "same-origin", headers: headers, cache: "no-store" };
    if (init.method !== "GET") {
      headers["Content-Type"] = "application/json";
      if (csrf) { headers["X-CSRF-Token"] = csrf; }
      init.body = JSON.stringify(body || {});
    }
    return fetch(path, init).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (data && data.csrf_token) { csrf = data.csrf_token; }
        return { ok: resp.ok, status: resp.status, data: data || {} };
      });
    }).catch(function () {
      return { ok: false, status: 0, data: { error: "network" } };
    });
  }

  function me() {
    return api("/api/auth/me", null, "GET").then(function (r) { return r.ok ? r.data : null; });
  }

  function go(path) { window.location.replace(path); }

  /* Protected pages: no session means the login page. */
  function requireSession() {
    return me().then(function (user) {
      if (!user) { go("/login"); return new Promise(function () {}); }
      return user;
    });
  }

  /* Login and register: already signed in means the waiting page. */
  function redirectIfSignedIn() {
    return me().then(function (user) { if (user) { afterLogin(); } });
  }

  /* The token lives in the URL fragment: read it and remove it from the address bar at once.
     It is never put in a query string, a link or a log. */
  function takeFragmentToken() {
    var raw = window.location.hash.replace(/^#/, "");
    if (raw) { window.history.replaceState(null, "", window.location.pathname); }
    return /^[A-Za-z0-9_-]{16,200}$/.test(raw) ? raw : "";
  }

  /* Device code (ABCD-EFGH) in the fragment: read it, remove it from the address bar at once. */
  function takeFragmentCode() {
    var raw = window.location.hash.replace(/^#/, "");
    if (raw) { window.history.replaceState(null, "", window.location.pathname); }
    raw = raw.toUpperCase();
    return /^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(raw) ? raw : "";
  }

  /* Kept only for this tab, only while signing in, for 10 minutes; the return page is always
     the fixed /link. Cleared at sign-out and on registration. */
  var STASH = "vc_link_code", STASH_TTL = 10 * 60 * 1000;
  function stashLinkCode(code) {
    try { window.sessionStorage.setItem(STASH, JSON.stringify({ c: code, t: Date.now() })); } catch (e) { /* ignore */ }
  }
  function clearLinkCode() {
    try { window.sessionStorage.removeItem(STASH); } catch (e) { /* ignore */ }
  }
  function readStash() {
    try {
      var item = JSON.parse(window.sessionStorage.getItem(STASH) || "null");
      if (!item || typeof item.c !== "string" || typeof item.t !== "number" ||
          Date.now() - item.t > STASH_TTL || !/^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(item.c)) {
        clearLinkCode();
        return "";
      }
      return item.c;
    } catch (e) { return ""; }
  }
  function hasStashedLinkCode() { return readStash() !== ""; }
  function takeStashedLinkCode() { var code = readStash(); clearLinkCode(); return code; }

  function afterLogin() { go(hasStashedLinkCode() ? "/link" : "/waiting"); }

  function onSubmit(form, handler) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var buttons = form.querySelectorAll("button[type=submit]");
      buttons.forEach(function (b) { b.disabled = true; });
      Promise.resolve(handler()).then(function () {
        buttons.forEach(function (b) { b.disabled = false; });
      });
    });
  }

  function logoutButton(button) {
    button.addEventListener("click", function () {
      clearLinkCode();
      api("/api/auth/logout", {}).then(function () { go("/login"); });
    });
  }

  return { $: $, api: api, me: me, go: go, message: message, errorText: errorText,
           requireSession: requireSession, redirectIfSignedIn: redirectIfSignedIn,
           takeFragmentToken: takeFragmentToken,
           takeFragmentCode: takeFragmentCode, stashLinkCode: stashLinkCode,
           takeStashedLinkCode: takeStashedLinkCode, clearLinkCode: clearLinkCode, afterLogin: afterLogin, onSubmit: onSubmit, logoutButton: logoutButton };
})();
