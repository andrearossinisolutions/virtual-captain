"use strict";
(function () {
  var msg = VC.$("msg");
  VC.onSubmit(VC.$("form"), function () {
    return VC.api("/api/auth/forgot", { email: VC.$("email").value }).then(function (r) {
      if (r.ok) {
        VC.message(msg, "If an account exists for this address, we sent a reset link.", "ok");
      } else {
        VC.message(msg, VC.errorText(r), "error");
      }
    });
  });
})();
