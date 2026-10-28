// The kiosk screen. Every second it asks the server "any new taps?", shows each result
// for a few seconds, then returns to "Tap your card". Text is always set with
// textContent, never as HTML, so a name can't inject anything into the page.
(function () {
  "use strict";

  var screen = document.getElementById("screen");
  var title = document.getElementById("title");
  var status = document.getElementById("status");
  var detail = document.getElementById("detail");
  var clock = document.getElementById("clock");
  var offline = document.getElementById("offline");

  var feedUrl = screen.dataset.events;
  var holdMs = parseInt(screen.dataset.hold, 10) || 4000;
  var POLL_MS = 800;

  var lastId = null;      // null until the first answer: old taps are never replayed
  var queue = [];
  var busy = false;
  var failures = 0;

  var IDLE = { cls: "screen-idle", title: "Tap your\ncard.", status: "", detail: "to check in or out" };

  function show(look) {
    screen.className = "screen " + look.cls;
    title.textContent = look.title;
    status.textContent = look.status || "";
    status.hidden = !look.status;
    detail.textContent = look.detail || "";
  }

  function describe(e) {
    var name = e.name || "there";
    switch (e.kind) {
      case "check_in":
        return { cls: "screen-in", title: "Welcome, " + name + ".", status: "Checked in " + e.time, detail: "Have a good day." };
      case "check_out":
        return { cls: "screen-out", title: "Goodbye, " + name + ".", status: "Checked out " + e.time,
                 detail: e.hours ? e.hours + " worked today." : "See you tomorrow." };
      case "already":
        return { cls: "screen-again", title: "Hi again" + (e.name ? ", " + e.name : "") + ".",
                 status: e.checked_out_at ? "Already checked out " + e.checked_out_at
                       : e.checked_in_at ? "Already checked in " + e.checked_in_at : "Already scanned",
                 detail: "No need to tap twice." };
      default:
        return { cls: "screen-denied", title: "Card not recognised.", status: "Access denied", detail: "Please see your administrator." };
    }
  }

  function next() {
    if (busy || !queue.length) return;
    if (queue.length > 3) queue = queue.slice(-1);        // a burst of taps: show the newest
    busy = true;
    show(describe(queue.shift()));
    setTimeout(function () { show(IDLE); busy = false; next(); }, holdMs);
  }

  function poll() {
    var url = feedUrl + (lastId === null ? "" : "?after=" + lastId);
    fetch(url, { cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        lastId = data.last_id;
        queue = queue.concat(data.events);
        failures = 0;
        offline.hidden = true;
        next();
      })
      .catch(function () { if (++failures >= 3) offline.hidden = false; })
      .then(function () { setTimeout(poll, POLL_MS); });
  }

  function tick() {
    var now = new Date();
    clock.textContent = now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }) +
      " · " + now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  }

  // Keep a phone or tablet screen awake while the kiosk is open (where the browser allows it).
  function keepAwake() {
    if (!("wakeLock" in navigator)) return;
    navigator.wakeLock.request("screen").catch(function () {});
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) keepAwake(); });

  tick(); setInterval(tick, 1000);
  keepAwake();
  poll();
})();
