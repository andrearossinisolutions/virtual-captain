"use strict";
/* Shared helpers. Dynamic text is only ever written with textContent. */
var VC = (function () {
  var csrf = "";

  var ERRORS = {
    invalid_request: "Please check the values you entered.",
    invalid_token: "This link is invalid or has expired.",
    invalid_credentials: "Email or password is incorrect.",
    too_many_attempts: "Too many attempts. Please try again later.",
    unauthenticated: "Your session has ended. Please sign in again.",
    too_large: "Please check the values you entered."
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
    return me().then(function (user) { if (user) { go("/waiting"); } });
  }

  /* The token lives in the URL fragment: read it and remove it from the address bar at once.
     It is never put in a query string, a link or a log. */
  function takeFragmentToken() {
    var raw = window.location.hash.replace(/^#/, "");
    if (raw) { window.history.replaceState(null, "", window.location.pathname); }
    return /^[A-Za-z0-9_-]{16,200}$/.test(raw) ? raw : "";
  }

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
      api("/api/auth/logout", {}).then(function () { go("/login"); });
    });
  }

  return { $: $, api: api, me: me, go: go, message: message, errorText: errorText,
           requireSession: requireSession, redirectIfSignedIn: redirectIfSignedIn,
           takeFragmentToken: takeFragmentToken, onSubmit: onSubmit, logoutButton: logoutButton };
})();
