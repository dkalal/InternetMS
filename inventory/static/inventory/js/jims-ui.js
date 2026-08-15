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

    function initSearchableSelect(select) {
      if (!select || select.dataset.searchableReady === "true" || select.multiple || select.disabled) return;
      select.dataset.searchableReady = "true";

      var options = Array.prototype.slice.call(select.options);
      var selected = select.options[select.selectedIndex] || null;
      var wrapper = document.createElement("div");
      wrapper.className = "jims-combobox";
      var input = document.createElement("input");
      input.type = "text";
      input.className = "jims-combobox-input";
      input.autocomplete = "off";
      input.spellcheck = false;
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-expanded", "false");
      input.setAttribute("aria-label", select.dataset.searchLabel || "Search options");
      input.placeholder = select.dataset.searchPlaceholder || "Search options...";
      input.value = selected ? selected.text.trim() : "";

      var menu = document.createElement("div");
      menu.className = "jims-combobox-menu";
      menu.hidden = true;
      menu.setAttribute("role", "listbox");
      menu.id = (select.id || "searchable-select") + "-options";
      input.setAttribute("aria-controls", menu.id);
      var activeIndex = -1;
      var visibleButtons = [];

      function closeMenu() {
        menu.hidden = true;
        input.setAttribute("aria-expanded", "false");
        activeIndex = -1;
        input.removeAttribute("aria-activedescendant");
      }

      function setActive(index) {
        if (!visibleButtons.length) return;
        activeIndex = Math.max(0, Math.min(index, visibleButtons.length - 1));
        visibleButtons.forEach(function (button, buttonIndex) {
          var active = buttonIndex === activeIndex;
          button.classList.toggle("is-active", active);
        });
        var activeButton = visibleButtons[activeIndex];
        input.setAttribute("aria-activedescendant", activeButton.id);
        activeButton.scrollIntoView({ block: "nearest" });
      }

      function choose(option) {
        select.value = option.value;
        input.value = option.text.trim();
        select.dispatchEvent(new Event("change", { bubbles: true }));
        closeMenu();
        input.focus();
      }

      function renderOptions(query) {
        var normalized = (query || "").trim().toLocaleLowerCase();
        var matches = options.filter(function (option) {
          return !normalized || option.text.toLocaleLowerCase().indexOf(normalized) !== -1;
        });
        menu.replaceChildren();
        visibleButtons = [];
        matches.slice(0, 50).forEach(function (option, index) {
          var button = document.createElement("button");
          button.type = "button";
          button.className = "jims-combobox-option";
          button.id = menu.id + "-" + index;
          button.setAttribute("role", "option");
          button.setAttribute("aria-selected", String(option.value === select.value));
          button.textContent = option.text.trim();
          button.addEventListener("mousedown", function (event) { event.preventDefault(); });
          button.addEventListener("click", function () { choose(option); });
          menu.appendChild(button);
          visibleButtons.push(button);
        });
        if (!matches.length) {
          var empty = document.createElement("div");
          empty.className = "jims-combobox-empty";
          empty.textContent = "No matching options";
          menu.appendChild(empty);
        } else if (matches.length > 50) {
          var hint = document.createElement("div");
          hint.className = "jims-combobox-hint";
          hint.textContent = "Keep typing to narrow " + matches.length + " results";
          menu.appendChild(hint);
        }
        menu.hidden = false;
        input.setAttribute("aria-expanded", "true");
        var selectedIndex = visibleButtons.findIndex(function (_button, index) {
          return matches[index] && matches[index].value === select.value;
        });
        if (visibleButtons.length) setActive(selectedIndex >= 0 ? selectedIndex : 0);
      }

      select.parentNode.insertBefore(wrapper, select);
      wrapper.appendChild(select);
      wrapper.appendChild(input);
      wrapper.appendChild(menu);
      select.classList.add("jims-searchable-native");

      input.addEventListener("focus", function () {
        var current = select.options[select.selectedIndex];
        input.value = current ? current.text.trim() : "";
        renderOptions("");
      });
      input.addEventListener("input", function () { renderOptions(input.value); });
      input.addEventListener("keydown", function (event) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          if (menu.hidden) renderOptions(input.value);
          else setActive(activeIndex + 1);
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          if (menu.hidden) renderOptions(input.value);
          else setActive(activeIndex - 1);
        } else if (event.key === "Enter" && !menu.hidden && visibleButtons[activeIndex]) {
          event.preventDefault();
          visibleButtons[activeIndex].click();
        } else if (event.key === "Escape") {
          event.preventDefault();
          closeMenu();
        }
      });
      select.addEventListener("focus", function () { input.focus(); });
      select.addEventListener("change", function () {
        var current = select.options[select.selectedIndex];
        input.value = current ? current.text.trim() : "";
      });
      select.addEventListener("invalid", function () { input.focus(); });
      document.addEventListener("click", function (event) {
        if (!wrapper.contains(event.target)) {
          closeMenu();
          var current = select.options[select.selectedIndex];
          input.value = current ? current.text.trim() : "";
        }
      });
    }

    function initSearchableSelects(root) {
      if (root.matches && root.matches("select[data-searchable-select]")) initSearchableSelect(root);
      if (root.querySelectorAll) root.querySelectorAll("select[data-searchable-select]").forEach(initSearchableSelect);
    }

    initSearchableSelects(document);
    new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(function (node) {
          if (node.nodeType === 1) initSearchableSelects(node);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });

    var navButton = document.querySelector("[data-nav-toggle]");
    var navPanel = document.querySelector("[data-nav-panel]");
    var navBackdrop = document.querySelector("[data-nav-backdrop]");
    if (navButton && navPanel) {
      function setNavigationOpen(open) {
        navButton.setAttribute("aria-expanded", String(open));
        navPanel.classList.toggle("hidden", !open);
        if (navBackdrop) navBackdrop.hidden = !open;
        document.body.classList.toggle("overflow-hidden", open && window.innerWidth < 1024);
        if (open) {
          var firstLink = navPanel.querySelector("a");
          if (firstLink) firstLink.focus();
        } else {
          navButton.focus();
        }
      }

      navButton.addEventListener("click", function () {
        setNavigationOpen(navButton.getAttribute("aria-expanded") !== "true");
      });
      document.querySelectorAll("[data-nav-dismiss]").forEach(function (button) {
        button.addEventListener("click", function () { setNavigationOpen(false); });
      });
      if (navBackdrop) navBackdrop.addEventListener("click", function () { setNavigationOpen(false); });
      navPanel.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && window.innerWidth < 1024) setNavigationOpen(false);
      });
      navPanel.querySelectorAll("a").forEach(function (link) {
        link.addEventListener("click", function () {
          if (window.innerWidth < 1024) setNavigationOpen(false);
        });
      });
      window.addEventListener("resize", function () {
        if (window.innerWidth >= 1024) {
          navPanel.classList.remove("hidden");
          if (navBackdrop) navBackdrop.hidden = true;
          document.body.classList.remove("overflow-hidden");
          navButton.setAttribute("aria-expanded", "false");
        } else if (navButton.getAttribute("aria-expanded") !== "true") {
          navPanel.classList.add("hidden");
        }
      });
    }

    var sidebarCollapse = document.querySelector("[data-sidebar-collapse]");
    if (sidebarCollapse && navPanel) {
      var sidebarKey = "jims.sidebar.collapsed";
      function setSidebarCollapsed(collapsed) {
        document.body.classList.toggle("jims-sidebar-collapsed", collapsed);
        sidebarCollapse.setAttribute("aria-expanded", String(!collapsed));
        sidebarCollapse.title = collapsed ? "Expand navigation" : "Minimize navigation";
        var path = sidebarCollapse.querySelector("path");
        if (path) path.setAttribute("d", collapsed ? "m9 18 6-6-6-6" : "m15 18-6-6 6-6");
        navPanel.querySelectorAll(".jims-sidebar-link").forEach(function (link) {
          var label = link.textContent.trim();
          if (label) link.title = collapsed ? label : "";
        });
        try { window.localStorage.setItem(sidebarKey, String(collapsed)); } catch (error) {}
      }
      var collapsed = false;
      try { collapsed = window.localStorage.getItem(sidebarKey) === "true"; } catch (error) {}
      setSidebarCollapsed(collapsed);
      sidebarCollapse.addEventListener("click", function () { setSidebarCollapsed(!document.body.classList.contains("jims-sidebar-collapsed")); });
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
    var confirmTitle = confirmLayer && confirmLayer.querySelector("[data-confirm-dialog-title]");
    var confirmMessage = confirmLayer && confirmLayer.querySelector("[data-confirm-dialog-message]");
    var confirmAccept = confirmLayer && confirmLayer.querySelector("[data-confirm-dialog-accept]");
    var confirmCancel = confirmLayer && confirmLayer.querySelector("[data-confirm-dialog-cancel]");
    var pendingForm = null;
    var confirmReturnFocus = null;

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
      if (confirmReturnFocus && document.contains(confirmReturnFocus)) confirmReturnFocus.focus();
      confirmReturnFocus = null;
    }

    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (form.dataset.confirmed === "true" || !confirmLayer || !confirmTitle || !confirmMessage || !confirmAccept || !confirmCancel) return;
        event.preventDefault();
        ensureCsrfToken(form);
        pendingForm = form;
        confirmReturnFocus = document.activeElement;
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
        if (event.key !== "Tab") return;
        var focusable = Array.prototype.slice.call(
          confirmLayer.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')
        );
        if (!focusable.length) return;
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
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

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var billing = document.querySelector("[data-customer-billing]");
    if (billing) {
      var preferenceKey = "jims.customer-list.billing-open";
      try {
        billing.open = window.localStorage.getItem(preferenceKey) === "true";
      } catch (error) {
        billing.open = false;
      }
      billing.addEventListener("toggle", function () {
        try {
          window.localStorage.setItem(preferenceKey, String(billing.open));
        } catch (error) {
          // The disclosure remains usable when browser storage is unavailable.
        }
      });
    }

    document.querySelectorAll("[data-customer-actions]").forEach(function (menu) {
      menu.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          menu.open = false;
          menu.querySelector("summary").focus();
        }
      });
    });

    document.querySelectorAll("[data-customer-sites-toggle]").forEach(function (button) {
      var panel = document.getElementById(button.getAttribute("aria-controls"));
      if (!panel) return;
      button.addEventListener("click", function () {
        var expanded = button.getAttribute("aria-expanded") === "true";
        button.setAttribute("aria-expanded", String(!expanded));
        panel.hidden = expanded;
        var label = button.querySelector("[data-customer-sites-toggle-label]");
        if (label) label.textContent = expanded ? button.dataset.closedLabel : "Hide sites";
      });
    });

    document.addEventListener("click", function (event) {
      document.querySelectorAll("[data-customer-actions][open]").forEach(function (menu) {
        if (!menu.contains(event.target)) menu.open = false;
      });
    });
  });
})();
