"use strict";
(function () {
  var msg = VC.$("msg");
  var token = VC.takeFragmentToken();  // kept in memory only
  var checking = VC.$("checking");

  function invalid(text) {
    checking.hidden = true;
    VC.$("again").hidden = false;
    VC.message(msg, text, "error");
  }

  if (!token) {  // reloaded page or incomplete link: the token is no longer in the address bar
    invalid("Open the link from your email again.");
  } else {
    VC.api("/api/auth/verify/preview", { token: token }).then(function (r) {
      if (!r.ok || !r.data.valid) {
        if (r.ok) { invalid("This link is invalid or has expired. If the link expired, register again with the same address."); }
        else { invalid("We could not check the link. Open the link from your email again in a moment."); }
        return;
      }
      checking.hidden = true;
      if (r.data.kind === "email_change") { VC.$("emailform").hidden = false; } else { VC.$("pwform").hidden = false; }
    });
  }

  VC.onSubmit(VC.$("pwform"), function () {
    var pw = VC.$("password").value;
    if (pw.length < 10) { VC.message(msg, "The password must have at least 10 characters.", "error"); return; }
    if (pw !== VC.$("confirm").value) { VC.message(msg, "The two passwords do not match.", "error"); return; }
    return VC.api("/api/auth/verify", { token: token, password: pw }).then(function (r) {
      if (r.ok) { VC.go("/waiting"); } else { VC.message(msg, VC.errorText(r), "error"); }
    });
  });

  VC.onSubmit(VC.$("emailform"), function () {
    return VC.api("/api/auth/verify", { token: token }).then(function (r) {
      if (r.ok) { VC.go("/login"); } else { VC.message(msg, VC.errorText(r), "error"); }
    });
  });
})();
