(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    var workspace = document.querySelector("[data-team-workspace]");
    if (!workspace) return;
    var returnFocus = null;
    function openDrawer(id, trigger) {
      var drawer = document.querySelector('[data-team-drawer="' + id + '"]');
      if (!drawer) return;
      returnFocus = trigger || document.activeElement;
      drawer.hidden = false;
      drawer.setAttribute("aria-hidden", "false");
      document.body.classList.add("overflow-hidden");
      var panel = drawer.querySelector(".team-drawer__panel");
      if (panel) panel.focus();
    }
    function closeDrawer(drawer) {
      drawer.hidden = true;
      drawer.setAttribute("aria-hidden", "true");
      document.body.classList.remove("overflow-hidden");
      if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
      returnFocus = null;
    }
    document.querySelectorAll("[data-team-drawer-open]").forEach(function (button) {
      button.addEventListener("click", function () { openDrawer(button.dataset.teamDrawerOpen, button); });
    });
    document.querySelectorAll("[data-team-drawer]").forEach(function (drawer) {
      drawer.querySelectorAll("[data-team-drawer-close]").forEach(function (button) { button.addEventListener("click", function () { closeDrawer(drawer); }); });
      drawer.addEventListener("click", function (event) { if (event.target === drawer) closeDrawer(drawer); });
      drawer.addEventListener("keydown", function (event) { if (event.key === "Escape") closeDrawer(drawer); });
    });
    document.querySelectorAll("[data-team-access-form]").forEach(function (form) {
      var save = form.querySelector("[data-team-save]");
      if (!save) return;
      var initial = new FormData(form);
      function updateSave() {
        var current = new FormData(form);
        var changed = false;
        initial.forEach(function (value, key) { if (current.getAll(key).join("|") !== initial.getAll(key).join("|")) changed = true; });
        current.forEach(function (value, key) { if (!initial.has(key)) changed = true; });
        save.disabled = !changed;
      }
      form.addEventListener("change", updateSave);
      form.addEventListener("input", updateSave);
      form.querySelectorAll('input[name="base_role"]').forEach(function (radio) {
        radio.addEventListener("change", function () {
          var description = radio.parentElement.querySelector("small");
          form.dataset.confirm = "This member will receive the " + radio.parentElement.querySelector("strong").textContent + " role. " + (description ? description.textContent : "Their workspace access will change immediately.");
        });
      });
      updateSave();
    });
    var inviteDialog = document.querySelector("[data-team-invite-dialog]");
    function openInvite() { if (!inviteDialog) return; if (typeof inviteDialog.showModal === "function") inviteDialog.showModal(); else inviteDialog.setAttribute("open", "open"); }
    function closeInvite() { if (!inviteDialog) return; if (typeof inviteDialog.close === "function") inviteDialog.close(); else inviteDialog.removeAttribute("open"); }
    document.querySelectorAll("[data-team-invite-open]").forEach(function (button) { button.addEventListener("click", openInvite); });
    document.querySelectorAll("[data-team-invite-close]").forEach(function (button) { button.addEventListener("click", closeInvite); });
    if (workspace.dataset.teamInviteOpened === "true") openInvite();
  });
})();
