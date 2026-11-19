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

  function when(ts) {
    return ts ? new Date(ts * 1000).toLocaleString() : "never";
  }

  function renderDevices(list) {
    var box = VC.$("devices");
    while (box.firstChild) { box.removeChild(box.firstChild); }
    VC.$("revoke-all").hidden = list.length === 0;
    if (list.length === 0) {
      var none = document.createElement("p");
      none.textContent = "No program is linked.";
      box.appendChild(none);
      return;
    }
    list.forEach(function (d) {
      var row = document.createElement("div");
      var title = document.createElement("p");
      title.className = "value";
      title.textContent = d.name + (d.online ? " (online)" : " (offline)");
      var info = document.createElement("p");
      info.className = "hint";
      info.textContent = "Last used: " + when(d.last_used_at) + ". Link expires: " + when(d.expires_at) + ".";
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "secondary";
      btn.textContent = "Revoke";
      btn.addEventListener("click", function () { revoke("/api/device/mine/revoke", { id: d.id }, btn); });
      row.appendChild(title);
      row.appendChild(info);
      row.appendChild(btn);
      box.appendChild(row);
    });
  }

  function loadDevices() {
    return VC.api("/api/device/mine", null, "GET").then(function (r) {
      if (r.ok) { renderDevices(r.data.devices || []); return; }
      VC.message(VC.$("msg-devices"), VC.errorText(r), "error");
    }).catch(function () {
      VC.message(VC.$("msg-devices"), "Could not load the linked programs.", "error");
    });
  }

  function revoke(path, data, button) {
    var msg = VC.$("msg-devices");
    button.disabled = true;
    return VC.api(path, data).then(function (r) {
      button.disabled = false;
      if (r.status === 401 && r.data.error === "unauthenticated") { VC.go("/login"); return; }
      if (r.ok) { VC.message(msg, "Revoked. The program must be linked again.", "ok"); } else { VC.message(msg, VC.errorText(r), "error"); }
      return loadDevices();
    });
  }

  var confirmTimer = null;
  VC.$("revoke-all").addEventListener("click", function () {
    var btn = VC.$("revoke-all");
    if (confirmTimer === null) {
      btn.textContent = "Click again to confirm";
      confirmTimer = setTimeout(function () { confirmTimer = null; btn.textContent = "Revoke all"; }, 5000);
      return;
    }
    clearTimeout(confirmTimer);
    confirmTimer = null;
    btn.textContent = "Revoke all";
    revoke("/api/device/mine/revoke-all", {}, btn);
  });

  VC.requireSession().then(function (user) {
    current = user;
    VC.$("who").textContent = user.email;
    VC.$("simbrief").value = user.simbrief_id || "";
    return loadDevices();
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
