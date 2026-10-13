// Role tabs: when the person picks Admin / HR manager / Employee, update the
// label, placeholder, subtitle and note. The text comes from data-attributes
// on each radio button (written by the server), so there is no copy to keep
// in two places. Without JavaScript the form still works; it just doesn't
// change the wording.
(function () {
  var form = document.getElementById("login-form");
  if (!form) return;

  var label = document.getElementById("username-label");
  var input = document.getElementById("username");
  var sub = document.getElementById("login-sub");
  var note = document.getElementById("role-note");

  function apply(radio) {
    label.textContent = radio.dataset.label;
    input.placeholder = radio.dataset.placeholder;
    sub.textContent = radio.dataset.sub;
    note.textContent = radio.dataset.note;
    note.hidden = !radio.dataset.note;
  }

  form.addEventListener("change", function (event) {
    if (event.target.name === "role") apply(event.target);
  });
})();
