"use strict";
(function () {
  var current = { email: "" };

  function bind(formId, msgId, build, done) {
    var msg = VC.$(msgId);
    VC.onSubmit(VC.$(formId), function () {
      var body = build(msg);
      if (!body) { return; }
      return VC.api(body.path, body.data).then(function (r) {
        if (r.status === 401 && r.data.error === "unauthenticated") { VC.go("/login"); return; }
        if (r.ok) { done(msg, r); } else { VC.message(msg, VC.errorText(r), "error"); }
      });
    });
  }

  VC.requireSession().then(function (user) {
    current = user;
    VC.$("who").textContent = user.email;
    VC.$("simbrief").value = user.simbrief_id || "";
  });
  VC.logoutButton(VC.$("logout"));

  bind("settings", "msg-settings", function () {
    return { path: "/api/auth/settings", data: { simbrief_id: VC.$("simbrief").value } };
  }, function (msg, r) {
    VC.$("simbrief").value = r.data.simbrief_id;
    VC.message(msg, "Saved.", "ok");
  });

  bind("password", "msg-password", function (msg) {
    if (VC.$("new_password").value.length < 10) {
      VC.message(msg, "The new password must have at least 10 characters.", "error");
      return null;
    }
    return { path: "/api/auth/password", data: {
      current_password: VC.$("current_password").value, new_password: VC.$("new_password").value } };
  }, function (msg) {
    VC.$("current_password").value = "";
    VC.$("new_password").value = "";
    VC.message(msg, "Password changed. Other devices were signed out.", "ok");
  });

  bind("email", "msg-email", function () {
    return { path: "/api/auth/email", data: {
      new_email: VC.$("new_email").value, password: VC.$("email_password").value } };
  }, function (msg) {
    VC.$("email_password").value = "";
    VC.message(msg, "If this address can be used, we sent it a confirmation link. Your email changes once you open it.", "ok");
  });

  bind("delete", "msg-delete", function (msg) {
    if (VC.$("confirm_text").value.trim() !== "DELETE") {
      VC.message(msg, "Type DELETE to confirm.", "error");
      return null;
    }
    return { path: "/api/auth/delete", data: { password: VC.$("delete_password").value } };
  }, function () {
    VC.go("/");
  });
})();
