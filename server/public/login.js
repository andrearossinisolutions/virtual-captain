"use strict";
(function () {
  var msg = VC.$("msg");
  VC.redirectIfSignedIn();
  VC.onSubmit(VC.$("form"), function () {
    return VC.api("/api/auth/login", {
      email: VC.$("email").value, password: VC.$("password").value
    }).then(function (r) {
      if (r.ok) { VC.afterLogin(); } else { VC.message(msg, VC.errorText(r), "error"); }
    });
  });
})();
