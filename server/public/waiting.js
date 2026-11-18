"use strict";
(function () {
  VC.requireSession().then(function (user) { VC.$("who").textContent = user.email; });
  VC.logoutButton(VC.$("logout"));
})();
