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

    var searchableSelectSequence = 0;

    function initNativeSelect(select) {
      if (!select || select.multiple) return;
      select.classList.add("jims-select");
    }

    function initSearchableSelect(select) {
      if (!select || select.dataset.searchableReady === "true" || select.multiple || select.disabled) return;
      select.dataset.searchableReady = "true";

      var selected = select.options[select.selectedIndex] || null;
      var fieldLabel = select.dataset.searchLabel || "Options";
      searchableSelectSequence += 1;
      var controlId = select.id || "jims-searchable-select-" + searchableSelectSequence;
      var wrapper = document.createElement("div");
      wrapper.className = "jims-combobox";
      var input = document.createElement("input");
      input.type = "text";
      input.className = "jims-combobox-input";
      input.id = controlId + "-search";
      input.autocomplete = "off";
      input.spellcheck = false;
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-expanded", "false");
      input.setAttribute("aria-required", String(select.required));
      input.placeholder = select.dataset.searchPlaceholder || "Search options...";

      function optionLabel(option) {
        var label = option ? option.text.trim() : "";
        if (option && !option.value && (!label || /^[-\u2013\u2014\s]+$/.test(label))) {
          return select.dataset.emptyLabel || "Select " + fieldLabel.toLocaleLowerCase();
        }
        return label;
      }

      function selectedInputValue(option) {
        return option && option.value ? optionLabel(option) : "";
      }

      input.value = selectedInputValue(selected);
      var visibleLabel = select.id ? document.querySelector('label[for="' + CSS.escape(select.id) + '"]') : null;
      if (visibleLabel) {
        if (!visibleLabel.id) visibleLabel.id = input.id + "-label";
        input.setAttribute("aria-labelledby", visibleLabel.id);
      } else {
        input.setAttribute("aria-label", fieldLabel);
      }
      if (select.getAttribute("aria-describedby")) input.setAttribute("aria-describedby", select.getAttribute("aria-describedby"));
      if (select.getAttribute("aria-invalid")) input.setAttribute("aria-invalid", "true");

      var menu = document.createElement("div");
      menu.className = "jims-combobox-menu jims-combobox-menu-portal";
      menu.hidden = true;
      menu.setAttribute("role", "listbox");
      menu.setAttribute("aria-label", fieldLabel + " options");
      menu.id = controlId + "-options";
      input.setAttribute("aria-controls", menu.id);
      var status = document.createElement("div");
      status.className = "jims-combobox-status";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      status.setAttribute("aria-atomic", "true");
      var activeIndex = -1;
      var visibleOptions = [];
      var positionFrame = null;

      function positionMenu() {
        positionFrame = null;
        if (menu.hidden) return;
        if (!document.contains(input)) {
          closeMenu();
          menu.remove();
          return;
        }
        var rect = input.getBoundingClientRect();
        var viewportGap = 12;
        var popupGap = 6;
        var preferredHeight = 288;
        var below = window.innerHeight - rect.bottom - viewportGap - popupGap;
        var above = rect.top - viewportGap - popupGap;
        var openAbove = below < 176 && above > below;
        var available = Math.max(120, Math.min(preferredHeight, openAbove ? above : below));
        var width = Math.min(Math.max(rect.width, 240), window.innerWidth - (viewportGap * 2));
        var left = Math.max(viewportGap, Math.min(rect.left, window.innerWidth - width - viewportGap));

        menu.style.left = left + "px";
        menu.style.width = width + "px";
        menu.style.maxHeight = available + "px";
        var renderedHeight = Math.min(menu.scrollHeight, available);
        menu.style.top = openAbove
          ? Math.max(viewportGap, rect.top - renderedHeight - popupGap) + "px"
          : Math.min(window.innerHeight - viewportGap, rect.bottom + popupGap) + "px";
        menu.dataset.placement = openAbove ? "top" : "bottom";
      }

      function queuePosition() {
        if (!positionFrame) positionFrame = window.requestAnimationFrame(positionMenu);
      }

      function closeMenu() {
        menu.hidden = true;
        input.setAttribute("aria-expanded", "false");
        activeIndex = -1;
        input.removeAttribute("aria-activedescendant");
        wrapper.classList.remove("is-open");
      }

      function setActive(index) {
        if (!visibleOptions.length) return;
        activeIndex = Math.max(0, Math.min(index, visibleOptions.length - 1));
        visibleOptions.forEach(function (optionNode, optionIndex) {
          optionNode.classList.toggle("is-active", optionIndex === activeIndex);
        });
        var activeOption = visibleOptions[activeIndex];
        input.setAttribute("aria-activedescendant", activeOption.id);
        activeOption.scrollIntoView({ block: "nearest" });
      }

      function choose(option) {
        select.value = option.value;
        input.value = selectedInputValue(option);
        select.dispatchEvent(new Event("change", { bubbles: true }));
        closeMenu();
        input.focus();
      }

      function renderOptions(query) {
        var options = Array.prototype.slice.call(select.options);
        var normalized = (query || "").trim().toLocaleLowerCase();
        var matches = options.filter(function (option) {
          return !option.disabled && !option.hidden && (!normalized || optionLabel(option).toLocaleLowerCase().indexOf(normalized) !== -1);
        });
        menu.replaceChildren();
        visibleOptions = [];
        matches.slice(0, 50).forEach(function (option, index) {
          var optionNode = document.createElement("div");
          optionNode.className = "jims-combobox-option";
          optionNode.id = menu.id + "-" + index;
          optionNode.setAttribute("role", "option");
          optionNode.setAttribute("aria-selected", String(option.value === select.value));
          optionNode.textContent = optionLabel(option);
          optionNode.addEventListener("mousedown", function (event) { event.preventDefault(); });
          optionNode.addEventListener("click", function () { choose(option); });
          menu.appendChild(optionNode);
          visibleOptions.push(optionNode);
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
        status.textContent = !matches.length
          ? "No matching " + fieldLabel.toLocaleLowerCase() + " options"
          : matches.length + " " + fieldLabel.toLocaleLowerCase() + " option" + (matches.length === 1 ? "" : "s") + " available";
        menu.hidden = false;
        input.setAttribute("aria-expanded", "true");
        wrapper.classList.add("is-open");
        positionMenu();
        var selectedIndex = visibleOptions.findIndex(function (_optionNode, index) {
          return matches[index] && matches[index].value === select.value;
        });
        if (visibleOptions.length) setActive(selectedIndex >= 0 ? selectedIndex : 0);
      }

      select.parentNode.insertBefore(wrapper, select);
      wrapper.appendChild(select);
      wrapper.appendChild(input);
      wrapper.appendChild(status);
      document.body.appendChild(menu);
      select.classList.add("jims-searchable-native");
      select.tabIndex = -1;
      select.setAttribute("aria-hidden", "true");

      input.addEventListener("focus", function () {
        var current = select.options[select.selectedIndex];
        input.value = selectedInputValue(current);
        renderOptions("");
      });
      input.addEventListener("input", function () {
        if (select.value) {
          select.value = "";
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
        renderOptions(input.value);
      });
      input.addEventListener("keydown", function (event) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          if (menu.hidden) renderOptions(input.value);
          else setActive(activeIndex + 1);
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          if (menu.hidden) renderOptions(input.value);
          else setActive(activeIndex < 0 ? visibleOptions.length - 1 : activeIndex - 1);
        } else if (event.key === "Enter" && !menu.hidden && visibleOptions[activeIndex]) {
          event.preventDefault();
          visibleOptions[activeIndex].click();
        } else if (event.key === "Escape") {
          if (!menu.hidden) {
            event.preventDefault();
            closeMenu();
            var current = select.options[select.selectedIndex];
            input.value = selectedInputValue(current);
          }
        } else if (event.key === "Tab") {
          closeMenu();
        }
      });
      select.addEventListener("focus", function () { input.focus(); });
      select.addEventListener("change", function () {
        var current = select.options[select.selectedIndex];
        input.value = selectedInputValue(current);
        input.setAttribute("aria-invalid", "false");
      });
      select.addEventListener("invalid", function () {
        input.setAttribute("aria-invalid", "true");
        input.focus();
      });
      input.addEventListener("blur", function () {
        window.setTimeout(function () {
          if (document.activeElement !== input && !menu.contains(document.activeElement)) {
            closeMenu();
            var current = select.options[select.selectedIndex];
            input.value = selectedInputValue(current);
          }
        }, 0);
      });
      document.addEventListener("click", function (event) {
        if (!wrapper.contains(event.target) && !menu.contains(event.target)) {
          closeMenu();
          var current = select.options[select.selectedIndex];
          input.value = selectedInputValue(current);
        }
      });
      window.addEventListener("resize", queuePosition);
      window.addEventListener("scroll", queuePosition, true);
      if (window.visualViewport) {
        window.visualViewport.addEventListener("resize", queuePosition);
        window.visualViewport.addEventListener("scroll", queuePosition);
      }

      new MutationObserver(function () {
        var current = select.options[select.selectedIndex];
        if (document.activeElement !== input) input.value = selectedInputValue(current);
        if (!menu.hidden) renderOptions(input.value);
      }).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "hidden", "label"] });
    }

    function initChoiceGroup(group) {
      if (!group || group.dataset.choiceReady === "true") return;
      group.dataset.choiceReady = "true";
      var inputs = Array.prototype.slice.call(group.querySelectorAll('input[type="checkbox"]'));
      var count = group.querySelector("[data-choice-count]");
      function syncChoiceCount() {
        var selectedCount = inputs.filter(function (input) { return input.checked; }).length;
        if (count) count.textContent = selectedCount + " selected";
      }
      inputs.forEach(function (input) { input.addEventListener("change", syncChoiceCount); });
      syncChoiceCount();
    }

    function initChoiceGroups(root) {
      if (root.matches && root.matches("[data-choice-group]")) initChoiceGroup(root);
      if (root.querySelectorAll) root.querySelectorAll("[data-choice-group]").forEach(initChoiceGroup);
    }

    function initCategoryUnitRules(form) {
      if (!form || form.dataset.categoryUnitsReady === "true") return;
      form.dataset.categoryUnitsReady = "true";
      var allowedInputs = Array.prototype.slice.call(form.querySelectorAll('input[name="allowed_units"]'));
      var defaultSelect = form.querySelector('select[name="default_unit"]');
      if (!allowedInputs.length || !defaultSelect) return;

      function syncUnitRules(userInitiated) {
        var allowedValues = new Set(allowedInputs.filter(function (input) {
          return input.checked && !input.disabled;
        }).map(function (input) { return input.value; }));
        Array.prototype.slice.call(defaultSelect.options).forEach(function (option) {
          if (option.value) option.disabled = !allowedValues.has(option.value);
        });

        var changed = false;
        if (defaultSelect.value && !allowedValues.has(defaultSelect.value)) {
          defaultSelect.value = "";
          changed = true;
        }
        if (userInitiated && !defaultSelect.value && allowedValues.size === 1) {
          defaultSelect.value = Array.from(allowedValues)[0];
          changed = true;
        }
        if (changed) defaultSelect.dispatchEvent(new Event("change", { bubbles: true }));
      }

      allowedInputs.forEach(function (input) {
        input.addEventListener("change", function () { syncUnitRules(true); });
      });
      syncUnitRules(false);
    }

    function initSearchableSelects(root) {
      if (root.matches && root.matches("select")) initNativeSelect(root);
      if (root.querySelectorAll) root.querySelectorAll("select").forEach(initNativeSelect);
      if (root.matches && root.matches("select[data-searchable-select]")) initSearchableSelect(root);
      if (root.querySelectorAll) root.querySelectorAll("select[data-searchable-select]").forEach(initSearchableSelect);
    }

    initSearchableSelects(document);
    initChoiceGroups(document);
    document.querySelectorAll("[data-category-unit-form]").forEach(initCategoryUnitRules);
    new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(function (node) {
          if (node.nodeType === 1) {
            initSearchableSelects(node);
            initChoiceGroups(node);
            if (node.matches && node.matches("[data-category-unit-form]")) initCategoryUnitRules(node);
            if (node.querySelectorAll) node.querySelectorAll("[data-category-unit-form]").forEach(initCategoryUnitRules);
          }
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
    var pendingSubmitter = null;
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
      pendingSubmitter = null;
      if (confirmReturnFocus && document.contains(confirmReturnFocus)) confirmReturnFocus.focus();
      confirmReturnFocus = null;
    }

    function requestConfirmation(event, form, source) {
        if (form.dataset.confirmed === "true" || !confirmLayer || !confirmTitle || !confirmMessage || !confirmAccept || !confirmCancel) return;
        event.preventDefault();
        ensureCsrfToken(form);
        pendingForm = form;
        pendingSubmitter = source === form ? null : source;
        confirmReturnFocus = document.activeElement;
        confirmTitle.textContent = source.dataset.confirmTitle || "Confirm action";
        confirmMessage.textContent = source.dataset.confirm || "Are you sure you want to continue?";
        confirmAccept.textContent = source.dataset.confirmAction || "Confirm";
        confirmAccept.className = "jims-btn " + (source.dataset.confirmTone === "danger" ? "jims-btn-danger" : "jims-btn-primary");
        confirmLayer.hidden = false;
        document.body.classList.add("overflow-hidden");
        confirmCancel.focus();
    }

    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        requestConfirmation(event, form, form);
      });
    });

    document.addEventListener("submit", function (event) {
      var submitter = event.submitter;
      if (!submitter || !submitter.matches("[data-confirm]")) return;
      requestConfirmation(event, event.target, submitter);
    });

    if (confirmAccept) {
      confirmAccept.addEventListener("click", function () {
        if (!pendingForm) return;
        ensureCsrfToken(pendingForm);
        pendingForm.dataset.confirmed = "true";
        pendingForm.requestSubmit(pendingSubmitter || undefined);
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
