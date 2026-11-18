"use strict";
(function () {
  var msg = VC.$("msg");
  VC.redirectIfSignedIn();
  VC.onSubmit(VC.$("form"), function () {
    return VC.api("/api/auth/register", {
      email: VC.$("email").value, simbrief_id: VC.$("simbrief").value
    }).then(function (r) {
      if (r.ok) {  // same text whether or not the address already exists
        VC.message(msg, "If this address can be used, we sent you a link. Open it to confirm your email and choose your password.", "ok");
      } else {
        VC.message(msg, VC.errorText(r), "error");
      }
    });
  });
})();
