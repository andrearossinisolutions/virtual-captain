"use strict";
(function () {
  var msg = VC.$("msg");
  var token = VC.takeFragmentToken();  // kept in memory only
  if (!token) { VC.message(msg, "Open the link from your email again.", "error"); }
  VC.onSubmit(VC.$("form"), function () {
    var pw = VC.$("password").value;
    if (!token) { VC.message(msg, "Open the link from your email again.", "error"); return; }
    if (pw.length < 10) { VC.message(msg, "The password must have at least 10 characters.", "error"); return; }
    if (pw !== VC.$("confirm").value) { VC.message(msg, "The two passwords do not match.", "error"); return; }
    return VC.api("/api/auth/reset", { token: token, password: pw }).then(function (r) {
      if (r.ok) { VC.go("/login"); } else { VC.message(msg, VC.errorText(r), "error"); }
    });
  });
})();
