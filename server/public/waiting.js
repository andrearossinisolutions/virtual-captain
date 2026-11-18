"use strict";
/* Waiting page: polls /api/agent/status every 3 s. Connected = the PC program is online:
   the title turns to "Program connected" and "Start flight" is enabled. No flight data here
   (step U1). Dynamic text only with textContent. */
(function () {
  var POLL_MS = 3000;
  var TEXT = {
    offTitle: "Program not connected",
    offState: "Waiting for the Virtual Captain program",
    onTitle: "Program connected",
    onState: "The Virtual Captain program is connected",
    started: "Connected. The flight view arrives in the next step."
  };
  var connected = null;

  function show(on) {
    if (on === connected) { return; }
    connected = on;
    VC.$("title").textContent = on ? TEXT.onTitle : TEXT.offTitle;
    VC.$("state").textContent = on ? TEXT.onState : TEXT.offState;
    VC.$("dot").className = on ? "dot on" : "dot";
    VC.$("start").disabled = !on;
    if (!on) { VC.$("note").textContent = ""; VC.$("note").className = "msg"; }
  }

  function poll() {
    VC.api("/api/agent/status", null, "GET").then(function (r) {
      if (r.status === 401) { VC.go("/login"); return; }
      show(r.ok && r.data.connected === true);
      window.setTimeout(poll, POLL_MS);
    });
  }

  VC.requireSession().then(function (user) {
    VC.$("who").textContent = user.email;
    poll();
  });
  VC.logoutButton(VC.$("logout"));
  VC.$("start").addEventListener("click", function () {
    if (connected) { VC.message(VC.$("note"), TEXT.started, "ok"); }
  });
})();
