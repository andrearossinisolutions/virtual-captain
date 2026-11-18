"use strict";
(function () {
  var msg = VC.$("msg"), form = VC.$("form"), box = VC.$("confirm"), input = VC.$("code");
  var ALPHABET = /[^A-HJKMNP-Z2-9]/g;  /* no 0, 1, I, L, O */
  var active = "";

  function format(raw) {
    var text = raw.toUpperCase().replace(ALPHABET, "").slice(0, 8);
    return text.length > 4 ? text.slice(0, 4) + "-" + text.slice(4) : text;
  }

  function show(code, info) {
    active = code;
    VC.$("d-code").textContent = code;
    VC.$("last4").value = "";  /* never pre-filled: the user must type it from the PC screen */
    VC.$("d-name").textContent = info.device_name || "";
    VC.$("d-ip").textContent = info.ip || "";
    VC.$("d-time").textContent = new Date(info.requested_at * 1000).toLocaleString();
    VC.$("d-ua").textContent = info.user_agent || "";
    form.hidden = true;
    box.hidden = false;
    VC.$("approve").disabled = true;
    VC.message(msg, "", "");
  }

  function reset() {
    active = "";
    box.hidden = true;
    form.hidden = false;
    input.value = "";
  }

  function lookup(code) {
    return VC.api("/api/device/lookup", { user_code: code }).then(function (r) {
      if (r.status === 401) { VC.stashLinkCode(code); VC.go("/login"); return; }
      if (r.ok) { show(code, r.data); return; }
      VC.message(msg, VC.errorText(r), "error");
    });
  }

  function confirmed() {
    var typed = VC.$("last4").value.toUpperCase().replace(ALPHABET, "");
    return active.length === 9 && typed.length === 4 && typed === active.slice(5);
  }

  function decide(path, okText) {
    var buttons = [VC.$("approve"), VC.$("deny")];
    buttons.forEach(function (b) { b.disabled = true; });
    return VC.api(path, { user_code: active, confirm: VC.$("last4").value }).then(function (r) {
      if (r.ok) {
        box.hidden = true;
        VC.message(msg, okText, "ok");
      } else if (r.data.error === "confirm_mismatch") {
        VC.message(msg, VC.errorText(r), "error");
      } else {
        reset();
        VC.message(msg, VC.errorText(r), "error");
      }
      VC.$("deny").disabled = false;
      VC.$("approve").disabled = !confirmed();
    });
  }

  input.addEventListener("input", function () { input.value = format(input.value); });
  VC.onSubmit(form, function () {
    var code = format(input.value);
    if (code.length !== 9) {
      VC.message(msg, VC.errorText({ data: { error: "invalid_code" } }), "error");
      return;
    }
    return lookup(code);
  });
  VC.$("last4").addEventListener("input", function () {
    VC.$("last4").value = VC.$("last4").value.toUpperCase().replace(ALPHABET, "");
    VC.$("approve").disabled = !confirmed();
  });
  VC.$("approve").addEventListener("click", function () {
    if (!confirmed()) { return; }
    decide("/api/device/approve", "Program linked. You can close this page and go back to the program.");
  });
  VC.$("deny").addEventListener("click", function () {
    decide("/api/device/deny", "Request denied. Nothing was linked.");
  });

  /* The code arrives in the URL fragment (removed at once) or was kept in memory of this tab
     while signing in. Without a session the page goes to /login and comes back here. */
  var code = VC.takeFragmentCode() || VC.takeStashedLinkCode();
  VC.me().then(function (user) {
    if (!user) {
      if (code) { VC.stashLinkCode(code); }
      VC.go("/login");
      return;
    }
    if (code) { input.value = code; lookup(code); }
  });
})();
