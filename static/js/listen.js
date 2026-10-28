// "Tap a card on the reader". Any element with data-listen becomes a small control:
// Start listening -> ask the server to capture the next tap -> poll once a second ->
// show the card that was detected (and drop its ID into the input named by data-target).
// All text is set with textContent, never as HTML.
(function () {
  "use strict";

  document.querySelectorAll("[data-listen]").forEach(function (box) {
    var urls = { start: box.dataset.start, cancel: box.dataset.cancel, status: box.dataset.status };
    var purpose = box.dataset.purpose || "register";
    var target = box.dataset.target ? document.querySelector(box.dataset.target) : null;
    var startBtn = box.querySelector(".listen-start");
    var cancelBtn = box.querySelector(".listen-cancel");
    var message = box.querySelector(".listen-message");
    var timer = null;

    function post(url, extra) {
      var body = new URLSearchParams(extra || {});
      body.set("csrf_token", box.dataset.csrf);
      return fetch(url, { method: "POST", body: body, credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
    }

    function stop() {
      clearInterval(timer);
      timer = null;
      box.classList.remove("is-listening");
      startBtn.hidden = false;
      cancelBtn.hidden = true;
    }

    function poll() {
      fetch(urls.status, { cache: "no-store", credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (s) {
          if (s.uid) {
            stop();
            box.classList.add("has-card");
            message.textContent = "Card " + s.uid + " detected. " + (s.note || "");
            if (target) { target.value = s.uid; }
          } else if (s.waiting) {
            message.textContent = "Waiting for a card… " + s.seconds_left + "s";
          } else {
            stop();
            message.textContent = "Timed out. Press Start listening to try again.";
          }
        })
        .catch(function () { message.textContent = "Lost contact with the server…"; });
    }

    startBtn.addEventListener("click", function () {
      post(urls.start, { purpose: purpose }).then(function () {
        box.classList.remove("has-card");
        box.classList.add("is-listening");
        startBtn.hidden = true;
        cancelBtn.hidden = false;
        message.textContent = "Waiting for a card…";
        timer = setInterval(poll, 1000);
        poll();
      }).catch(function () { message.textContent = "Could not start. Reload the page and try again."; });
    });

    cancelBtn.addEventListener("click", function () {
      post(urls.cancel).then(function () { stop(); message.textContent = "Cancelled."; });
    });
  });
})();
