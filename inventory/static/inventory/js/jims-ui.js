(function () {
  "use strict";

  function onReady(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback);
    } else {
      callback();
    }
  }

  onReady(function () {
    // A scrollable data region must be reachable by keyboard users.  Templates
    // keep their semantic table markup; this only supplies a consistent focus
    // target and useful orientation for the shared table wrappers.
    var pageTitle = document.querySelector("main h1");
    var tableLabel = pageTitle ? pageTitle.textContent.trim() + " records" : "Data records";
    document.querySelectorAll(".jims-table-wrap, .js-table-wrap, .jims-scroll-region").forEach(function (region) {
      if (!region.hasAttribute("tabindex")) region.setAttribute("tabindex", "0");
      if (!region.hasAttribute("role")) region.setAttribute("role", "region");
      if (!region.hasAttribute("aria-label")) region.setAttribute("aria-label", "Scrollable " + tableLabel);
    });
    document.querySelectorAll("table thead th").forEach(function (header) {
      if (!header.hasAttribute("scope")) header.setAttribute("scope", "col");
    });

    var navButton = document.querySelector("[data-nav-toggle]");
    var navPanel = document.querySelector("[data-nav-panel]");
    if (navButton && navPanel) {
      navButton.addEventListener("click", function () {
        var expanded = navButton.getAttribute("aria-expanded") === "true";
        navButton.setAttribute("aria-expanded", String(!expanded));
        navPanel.classList.toggle("hidden", expanded);
      });
    }

    document.querySelectorAll("[data-menu-button]").forEach(function (button) {
      var panel = document.getElementById(button.getAttribute("aria-controls"));
      if (!panel) return;
      button.addEventListener("click", function (event) {
        event.stopPropagation();
        var opening = panel.hidden;
        document.querySelectorAll("[data-menu-panel]").forEach(function (other) { other.hidden = true; });
        panel.hidden = !opening;
        button.setAttribute("aria-expanded", String(opening));
      });
      panel.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          panel.hidden = true;
          button.setAttribute("aria-expanded", "false");
          button.focus();
        }
      });
    });

    document.addEventListener("click", function () {
      document.querySelectorAll("[data-menu-panel]").forEach(function (panel) { panel.hidden = true; });
      document.querySelectorAll("[data-menu-button]").forEach(function (button) {
        button.setAttribute("aria-expanded", "false");
      });
    });

    var confirmLayer = document.querySelector("[data-confirm-layer]");
    var confirmTitle = document.querySelector("[data-confirm-title]");
    var confirmMessage = document.querySelector("[data-confirm-message]");
    var confirmAccept = document.querySelector("[data-confirm-accept]");
    var confirmCancel = document.querySelector("[data-confirm-cancel]");
    var pendingForm = null;

    function getCsrfToken() {
      var prefix = "csrftoken=";
      var cookies = document.cookie ? document.cookie.split(";") : [];
      for (var index = 0; index < cookies.length; index += 1) {
        var cookie = cookies[index].trim();
        if (cookie.indexOf(prefix) === 0) return decodeURIComponent(cookie.slice(prefix.length));
      }
      return "";
    }

    function ensureCsrfToken(form) {
      var tokenInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
      if (tokenInput && tokenInput.value) return;

      var token = getCsrfToken();
      if (!token) return;
      if (!tokenInput) {
        tokenInput = document.createElement("input");
        tokenInput.type = "hidden";
        tokenInput.name = "csrfmiddlewaretoken";
        form.appendChild(tokenInput);
      }
      tokenInput.value = token;
    }

    function closeConfirm() {
      if (!confirmLayer) return;
      confirmLayer.hidden = true;
      document.body.classList.remove("overflow-hidden");
      pendingForm = null;
    }

    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (form.dataset.confirmed === "true" || !confirmLayer) return;
        event.preventDefault();
        ensureCsrfToken(form);
        pendingForm = form;
        confirmTitle.textContent = form.dataset.confirmTitle || "Confirm action";
        confirmMessage.textContent = form.dataset.confirm || "Are you sure you want to continue?";
        confirmAccept.textContent = form.dataset.confirmAction || "Confirm";
        confirmAccept.className = "jims-btn " + (form.dataset.confirmTone === "danger" ? "jims-btn-danger" : "jims-btn-primary");
        confirmLayer.hidden = false;
        document.body.classList.add("overflow-hidden");
        confirmCancel.focus();
      });
    });

    if (confirmAccept) {
      confirmAccept.addEventListener("click", function () {
        if (!pendingForm) return;
        ensureCsrfToken(pendingForm);
        pendingForm.dataset.confirmed = "true";
        pendingForm.requestSubmit();
      });
    }
    if (confirmCancel) confirmCancel.addEventListener("click", closeConfirm);
    if (confirmLayer) {
      confirmLayer.addEventListener("click", function (event) {
        if (event.target === confirmLayer) closeConfirm();
      });
      confirmLayer.addEventListener("keydown", function (event) {
        if (event.key === "Escape") closeConfirm();
      });
    }

    document.querySelectorAll("form[data-submit-lock]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (event.defaultPrevented) return;
        if (form.dataset.submitting === "true") return;
        form.dataset.submitting = "true";
        window.setTimeout(function () {
          form.querySelectorAll("button[type='submit'], input[type='submit']").forEach(function (button) {
            button.disabled = true;
            button.classList.add("jims-loading");
            if (button.dataset.loadingLabel) button.textContent = button.dataset.loadingLabel;
          });
        }, 0);
      });
    });

    document.querySelectorAll("[data-cart-tab]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        var target = tab.dataset.cartTab;
        document.querySelectorAll("[data-cart-tab]").forEach(function (item) {
          var active = item.dataset.cartTab === target;
          item.setAttribute("aria-selected", String(active));
          item.classList.toggle("jims-btn-primary", active);
          item.classList.toggle("jims-btn-secondary", !active);
        });
        document.querySelectorAll("[data-cart-panel]").forEach(function (panel) {
          panel.classList.toggle("is-active", panel.dataset.cartPanel === target);
        });
      });
    });

    document.querySelectorAll("form[data-unsaved-form]").forEach(function (form) {
      var dirty = false;
      form.addEventListener("input", function () { dirty = true; });
      form.addEventListener("submit", function () { dirty = false; });
      window.addEventListener("beforeunload", function (event) {
        if (!dirty) return;
        event.preventDefault();
        event.returnValue = "";
      });
    });
  });
})();
