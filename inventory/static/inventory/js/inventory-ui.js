(function () {
  "use strict";

  function ready(callback) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", callback);
    else callback();
  }

  function number(value) {
    var parsed = parseFloat(value || "0");
    return isFinite(parsed) ? parsed : 0;
  }

  ready(function () {
    var quantityFormatter = new Intl.NumberFormat("en-TZ", { maximumFractionDigits: 6 });
    var tzsDisplayFormatter = new Intl.NumberFormat("en-TZ", { maximumFractionDigits: 2 });
    var productForm = document.querySelector("[data-product-form]");
    if (productForm) {
      var itemType = document.getElementById("id_item_type");
      var trackStock = document.getElementById("id_track_stock");
      var serialized = document.getElementById("id_is_serialized");
      var buying = document.getElementById("id_buying_price");
      var selling = document.getElementById("id_selling_price");
      var technician = document.getElementById("id_technician_price");
      var wholesaleToggle = document.getElementById("id_allow_wholesale");
      var wholesalePanel = document.querySelector("[data-wholesale-panel]");

      function money(value) {
        if (value === null || !isFinite(value)) return "—";
        return tzsDisplayFormatter.format(value) + " TZS";
      }

      function optionalNumber(input) {
        if (!input || input.value === "") return null;
        var value = parseFloat(input.value);
        return isFinite(value) ? value : null;
      }

      function syncProductFields() {
        var service = itemType && itemType.value === "service";
        if (service && trackStock && !trackStock.disabled) trackStock.checked = false;
        document.querySelectorAll("[data-physical-only]").forEach(function (element) {
          element.classList.toggle("hidden", service);
        });
        var stockEnabled = !service && trackStock && trackStock.checked;
        document.querySelectorAll("[data-stock-only]").forEach(function (element) {
          element.classList.toggle("hidden", !stockEnabled);
        });
        var serialEnabled = stockEnabled && serialized && serialized.checked;
        document.querySelectorAll("[data-serialized-only]").forEach(function (element) {
          element.classList.toggle("hidden", !serialEnabled);
        });
        document.querySelectorAll("[data-product-type-summary]").forEach(function (summary) {
          summary.textContent = service
            ? "Services are available for quotations and invoices but do not use stock, serial, expiry, or reorder controls."
            : "Physical items can use stock, serial, expiry, and reorder controls. Stock balances are created through receiving and authorized adjustments.";
        });
      }

      function syncPricing() {
        var buy = optionalNumber(buying);
        var sell = optionalNumber(selling);
        var effective = sell;
        var technicianValue = optionalNumber(technician);
        var effectiveTechnician = technicianValue !== null ? technicianValue : sell;
        var profit = buy !== null && effective !== null ? effective - buy : null;
        var margin = document.querySelector("[data-product-margin]");
        var marginRate = document.querySelector("[data-product-margin-rate]");
        var standardSelling = document.querySelector("[data-product-standard-selling]");
        var effectiveTechnicianNode = document.querySelector("[data-product-effective-technician]");
        if (margin) margin.textContent = money(profit);
        if (marginRate) marginRate.textContent = profit !== null && effective ? ((profit / effective) * 100).toFixed(2) + "%" : "—";
        if (standardSelling) standardSelling.textContent = money(effective);
        if (effectiveTechnicianNode) effectiveTechnicianNode.textContent = money(effectiveTechnician);
      }

      function syncWholesale() {
        if (wholesalePanel && wholesaleToggle) wholesalePanel.classList.toggle("hidden", !wholesaleToggle.checked);
      }

      [itemType, trackStock, serialized].forEach(function (input) {
        if (input) input.addEventListener("change", syncProductFields);
      });
      [buying, selling, technician].forEach(function (input) {
        if (input) input.addEventListener("input", syncPricing);
      });
      if (wholesaleToggle) wholesaleToggle.addEventListener("change", syncWholesale);
      syncProductFields();
      syncPricing();
      syncWholesale();
    }

    var cartLineForm = document.querySelector("[data-cart-line-form]");
    if (cartLineForm) {
      var serialPicker = cartLineForm.querySelector("[data-serial-picker]");
      var productSelect = document.getElementById("id_product");
      if (productSelect) {
        productSelect.addEventListener("change", function () {
          var selectedProduct = productSelect.value;
          if (!selectedProduct) return;
          var url = new URL(window.location.href);
          url.searchParams.set("product", selectedProduct);
          window.location.assign(url.toString());
        });
      }
      if (serialPicker) {
        var serialSearch = serialPicker.querySelector("[data-serial-search]");
        var serialOptions = Array.prototype.slice.call(serialPicker.querySelectorAll("[data-serial-option]"));
        var selectedOutput = serialPicker.querySelector("[data-serial-selected]");
        var requiredOutput = serialPicker.querySelector("[data-serial-required]");
        var quantityInput = document.getElementById("id_quantity");

        function syncSerialPicker() {
          var selected = serialOptions.filter(function (option) {
            var input = option.querySelector("input");
            return input && input.checked;
          }).length;
          if (selectedOutput) selectedOutput.textContent = String(selected);
          if (requiredOutput) requiredOutput.textContent = String(Math.max(1, Math.floor(number(quantityInput && quantityInput.value))));
        }

        function filterSerials() {
          var term = serialSearch ? serialSearch.value.trim().toLowerCase() : "";
          serialOptions.forEach(function (option) {
            option.hidden = Boolean(term && option.textContent.toLowerCase().indexOf(term) === -1);
          });
        }

        if (serialSearch) serialSearch.addEventListener("input", filterSerials);
        if (quantityInput) quantityInput.addEventListener("input", syncSerialPicker);
        serialOptions.forEach(function (option) {
          var input = option.querySelector("input");
          if (input) input.addEventListener("change", syncSerialPicker);
        });
        syncSerialPicker();
      }
    }

    var quickSupplierDialog = document.querySelector("[data-quick-supplier-dialog]");
    var openQuickSupplier = document.querySelector("[data-open-quick-supplier]");
    if (quickSupplierDialog && openQuickSupplier) {
      var supplierForm = quickSupplierDialog.closest("form");
      var supplierUrl = supplierForm.dataset.supplierQuickCreateUrl;
      var supplierSelect = document.getElementById("id_supplier");
      var supplierName = quickSupplierDialog.querySelector("[data-quick-supplier-name]");
      var supplierPhone = quickSupplierDialog.querySelector("[data-quick-supplier-phone]");
      var supplierNameError = quickSupplierDialog.querySelector("[data-quick-supplier-name-error]");
      var supplierPhoneError = quickSupplierDialog.querySelector("[data-quick-supplier-phone-error]");
      var supplierGeneralError = quickSupplierDialog.querySelector("[data-quick-supplier-error]");
      var saveSupplier = quickSupplierDialog.querySelector("[data-save-quick-supplier]");
      var closeSupplierButtons = quickSupplierDialog.querySelectorAll("[data-close-quick-supplier], [data-cancel-quick-supplier]");

      function setSupplierError(node, message) {
        node.textContent = message || "";
        node.classList.toggle("hidden", !message);
      }

      function resetSupplierErrors() {
        setSupplierError(supplierNameError, "");
        setSupplierError(supplierPhoneError, "");
        setSupplierError(supplierGeneralError, "");
      }

      function closeSupplierDialog() {
        quickSupplierDialog.classList.add("hidden");
        quickSupplierDialog.classList.remove("flex");
        document.body.classList.remove("overflow-hidden");
        openQuickSupplier.focus();
      }

      openQuickSupplier.addEventListener("click", function () {
        resetSupplierErrors();
        supplierName.value = "";
        supplierPhone.value = "";
        quickSupplierDialog.classList.remove("hidden");
        quickSupplierDialog.classList.add("flex");
        document.body.classList.add("overflow-hidden");
        supplierName.focus();
      });
      closeSupplierButtons.forEach(function (button) { button.addEventListener("click", closeSupplierDialog); });
      quickSupplierDialog.addEventListener("click", function (event) { if (event.target === quickSupplierDialog) closeSupplierDialog(); });
      quickSupplierDialog.addEventListener("keydown", function (event) {
        if (event.key === "Escape") { closeSupplierDialog(); return; }
        if (event.key !== "Tab") return;
        var focusable = Array.prototype.filter.call(
          quickSupplierDialog.querySelectorAll("button:not([disabled]), input:not([disabled])"),
          function (element) { return element.offsetParent !== null; }
        );
        if (!focusable.length) return;
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      });
      saveSupplier.addEventListener("click", function () {
        resetSupplierErrors();
        saveSupplier.disabled = true;
        saveSupplier.textContent = "Saving…";
        var csrf = supplierForm.querySelector("input[name='csrfmiddlewaretoken']");
        var body = new URLSearchParams({ company_name: supplierName.value.trim(), phone: supplierPhone.value.trim() });
        fetch(supplierUrl, {
          method: "POST",
          headers: { "X-CSRFToken": csrf.value, "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
          body: body.toString()
        }).then(function (response) {
          return response.json().then(function (payload) { return { ok: response.ok, payload: payload }; });
        }).then(function (result) {
          if (!result.ok) {
            var errors = result.payload.errors || {};
            setSupplierError(supplierNameError, errors.company_name && errors.company_name[0] ? errors.company_name[0].message : "");
            setSupplierError(supplierPhoneError, errors.phone && errors.phone[0] ? errors.phone[0].message : "");
            if (!errors.company_name && !errors.phone) setSupplierError(supplierGeneralError, "Supplier could not be saved. Try again.");
            return;
          }
          var supplier = result.payload.supplier;
          supplierSelect.appendChild(new Option(supplier.text, String(supplier.id), true, true));
          supplierSelect.value = String(supplier.id);
          supplierSelect.dispatchEvent(new Event("change", { bubbles: true }));
          closeSupplierDialog();
        }).catch(function () {
          setSupplierError(supplierGeneralError, "Supplier could not be saved. Check the connection and try again.");
        }).finally(function () {
          saveSupplier.disabled = false;
          saveSupplier.textContent = "Save supplier";
        });
      });
      [supplierName, supplierPhone].forEach(function (input) {
        input.addEventListener("keydown", function (event) {
          if (event.key === "Enter") {
            event.preventDefault();
            if (!saveSupplier.disabled) saveSupplier.click();
          }
        });
      });
    }

    var formset = document.querySelector("[data-purchase-formset]");
    if (formset) {
      var totalForms = document.getElementById("id_lines-TOTAL_FORMS");
      var template = document.getElementById("purchase-line-template");
      var addButton = document.querySelector("[data-add-purchase-line]");
      var purchaseForm = formset.closest("form");
      var productSearchUrl = purchaseForm ? purchaseForm.dataset.productSearchUrl : "";
      var metaNode = document.getElementById("purchase-product-meta");
      var productMeta = metaNode ? JSON.parse(metaNode.textContent) : {};

      function activeLines() {
        return Array.prototype.filter.call(formset.querySelectorAll("[data-purchase-line]"), function (line) {
          var deletion = line.querySelector("input[name$='-DELETE']");
          return !line.classList.contains("hidden") && !(deletion && deletion.checked);
        });
      }

      function syncDuplicateWarnings() {
        var counts = {};
        activeLines().forEach(function (line) {
          var select = line.querySelector("select[name$='-product']");
          if (select && select.value) counts[select.value] = (counts[select.value] || 0) + 1;
        });
        activeLines().forEach(function (line) {
          var select = line.querySelector("select[name$='-product']");
          var hint = line.querySelector("[data-product-hint]");
          if (select && select.value && counts[select.value] > 1 && hint) {
            hint.textContent = "This product appears more than once. Keep separate rows only for different batch or expiry details.";
            hint.classList.add("text-amber-700");
          } else if (hint) {
            hint.classList.remove("text-amber-700");
            var meta = select && select.value ? productMeta[select.value] : null;
            hint.textContent = meta
              ? meta.sku + (meta.serialized ? " · Serial numbers required" : "") + (meta.expiry ? " · Expiry tracked" : "")
              : "Search by name, SKU, brand or model.";
          }
        });
      }

      function enhanceProductSelect(select, line) {
        if (!select || select.dataset.remoteReady === "true" || !productSearchUrl) return;
        select.dataset.remoteReady = "true";
        select.hidden = true;
        // Tailwind's `block` utility on Django form widgets can override the
        // browser's default `[hidden]` rule. Keep the tenant-filtered select as
        // the submitted source of truth, but make it reliably non-visual once
        // the remote combobox has taken over the interaction.
        select.style.setProperty("display", "none", "important");
        select.setAttribute("aria-hidden", "true");
        select.tabIndex = -1;

        var wrapper = document.createElement("div");
        wrapper.className = "relative";
        var input = document.createElement("input");
        input.type = "search";
        input.autocomplete = "off";
        input.className = "block min-h-10 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/30";
        input.placeholder = "Search name, SKU, brand or model…";
        input.setAttribute("aria-label", "Product search");
        input.setAttribute("role", "combobox");
        input.setAttribute("aria-autocomplete", "list");
        input.setAttribute("aria-expanded", "false");
        var list = document.createElement("div");
        list.className = "absolute z-40 mt-1 hidden max-h-64 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white p-1 shadow-xl";
        list.setAttribute("role", "listbox");
        wrapper.appendChild(input);
        wrapper.appendChild(list);
        select.parentNode.insertBefore(wrapper, select);

        var selectedOption = select.options[select.selectedIndex];
        if (select.value && selectedOption) input.value = selectedOption.textContent.trim();
        var timer = null;
        var controller = null;

        function closeList() {
          list.classList.add("hidden");
          input.setAttribute("aria-expanded", "false");
        }

        function renderResults(results) {
          list.replaceChildren();
          if (!results.length) {
            var empty = document.createElement("p");
            empty.className = "px-3 py-2 text-sm text-slate-500";
            empty.textContent = "No matching stock products.";
            list.appendChild(empty);
          }
          results.forEach(function (result) {
            var button = document.createElement("button");
            button.type = "button";
            button.className = "block w-full rounded-md px-3 py-2 text-left hover:bg-slate-100 focus:bg-slate-100 focus:outline-none";
            button.setAttribute("role", "option");
            var title = document.createElement("span");
            title.className = "block text-sm font-semibold text-slate-900";
            title.textContent = result.name;
            var detail = document.createElement("span");
            detail.className = "block text-xs text-slate-500";
            detail.textContent = (result.sku || "No SKU") + " · " + (result.unit || "Unit");
            button.appendChild(title);
            button.appendChild(detail);
            button.addEventListener("click", function () {
              var option = new Option(result.text, String(result.id), true, true);
              select.replaceChildren(new Option("Select a product", ""), option);
              productMeta[String(result.id)] = {
                sku: result.sku || "",
                base_unit: result.unit || "Unit",
                serialized: Boolean(result.serialized),
                expiry: Boolean(result.expiry)
              };
              input.value = result.text;
              closeList();
              select.dispatchEvent(new Event("change", { bubbles: true }));
            });
            list.appendChild(button);
          });
          list.classList.remove("hidden");
          input.setAttribute("aria-expanded", "true");
        }

        function renderMessage(message, error) {
          list.replaceChildren();
          var status = document.createElement("p");
          status.className = "px-3 py-2 text-sm " + (error ? "text-rose-700" : "text-slate-500");
          status.textContent = message;
          list.appendChild(status);
          list.classList.remove("hidden");
          input.setAttribute("aria-expanded", "true");
        }

        function search() {
          if (controller) controller.abort();
          controller = new AbortController();
          renderMessage("Searching products…", false);
          var url = productSearchUrl + "?q=" + encodeURIComponent(input.value.trim());
          fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" }, signal: controller.signal })
            .then(function (response) {
              if (!response.ok) throw new Error("Product search failed");
              return response.json();
            })
            .then(function (payload) { renderResults(payload.results || []); })
            .catch(function (error) {
              if (error.name !== "AbortError") renderMessage("Products could not be loaded. Try again.", true);
            });
        }

        input.addEventListener("focus", search);
        input.addEventListener("input", function () {
          window.clearTimeout(timer);
          timer = window.setTimeout(search, 200);
        });
        input.addEventListener("keydown", function (event) {
          if (event.key === "Escape") closeList();
          if (event.key === "ArrowDown" && !list.classList.contains("hidden")) {
            event.preventDefault();
            var firstResult = list.querySelector("button[role='option']");
            if (firstResult) firstResult.focus();
          }
        });
        list.addEventListener("keydown", function (event) {
          var options = Array.prototype.slice.call(list.querySelectorAll("button[role='option']"));
          var index = options.indexOf(document.activeElement);
          if (event.key === "ArrowDown" && index > -1) {
            event.preventDefault();
            options[Math.min(index + 1, options.length - 1)].focus();
          } else if (event.key === "ArrowUp" && index > -1) {
            event.preventDefault();
            if (index === 0) input.focus(); else options[index - 1].focus();
          } else if (event.key === "Escape") {
            closeList();
            input.focus();
          }
        });
        document.addEventListener("click", function (event) {
          if (!wrapper.contains(event.target)) closeList();
        });
      }

      function syncLine(line) {
        var product = line.querySelector("select[name$='-product']");
        var meta = product ? productMeta[product.value] : null;
        line.querySelectorAll("[data-line-serials]").forEach(function (field) {
          field.classList.toggle("hidden", !(meta && meta.serialized));
        });
        line.querySelectorAll("[data-line-expiry]").forEach(function (field) {
          field.classList.toggle("hidden", !(meta && meta.expiry));
        });
        var hint = line.querySelector("[data-product-hint]");
        if (hint) {
          hint.textContent = meta
            ? meta.sku + (meta.serialized ? " · Serial numbers required" : "") + (meta.expiry ? " · Expiry tracked" : "")
            : "Search by name, SKU, brand or model.";
        }
        var unit = line.querySelector("[data-product-unit]");
        if (unit) unit.textContent = "Unit — " + (meta ? meta.base_unit : "—");
        var quantity = line.querySelector("input[name$='-quantity']");
        var unitCost = line.querySelector("input[name$='-unit_cost']");
        var total = line.querySelector("[data-line-total]");
        if (total) total.textContent = tzsDisplayFormatter.format(number(quantity && quantity.value) * number(unitCost && unitCost.value)) + " TZS";
        syncDuplicateWarnings();
      }

      function bindLine(line) {
        var product = line.querySelector("select[name$='-product']");
        enhanceProductSelect(product, line);
        if (product) product.addEventListener("change", function () { syncLine(line); });
        line.querySelectorAll("input[name$='-quantity'], input[name$='-unit_cost']").forEach(function (input) {
          input.addEventListener("input", function () { syncLine(line); });
        });
        var remove = line.querySelector("[data-remove-line]");
        if (remove) {
          remove.addEventListener("click", function () {
            var deletion = line.querySelector("input[name$='-DELETE']");
            if (deletion) deletion.checked = true;
            line.querySelectorAll("input, select, textarea").forEach(function (field) {
              if (field !== deletion) field.disabled = true;
            });
            line.classList.add("hidden");
            syncDuplicateWarnings();
          });
        }
        syncLine(line);
      }

      function appendLine(result) {
        var index = parseInt(totalForms.value, 10);
        var wrapper = document.createElement("div");
        wrapper.innerHTML = template.innerHTML.replace(/__prefix__/g, String(index)).trim();
        var line = wrapper.firstElementChild;
        if (result) {
          var select = line.querySelector("select[name$='-product']");
          select.replaceChildren(new Option("Select a product", ""), new Option(result.text, String(result.id), true, true));
          productMeta[String(result.id)] = {
            sku: result.sku || "",
            base_unit: result.unit || "Unit",
            serialized: Boolean(result.serialized),
            expiry: Boolean(result.expiry)
          };
        }
        formset.appendChild(line);
        totalForms.value = String(index + 1);
        bindLine(line);
        return line;
      }

      formset.querySelectorAll("[data-purchase-line]").forEach(bindLine);
      if (addButton && totalForms && template) {
        addButton.addEventListener("click", function () {
          var line = appendLine(null);
          var first = line.querySelector("input[type='search'], input:not([type='hidden'])");
          if (first) first.focus();
        });
      }

      var quickProductDialog = document.querySelector("[data-quick-product-dialog]");
      var openQuickProduct = document.querySelector("[data-open-quick-product]");
      if (quickProductDialog && openQuickProduct && purchaseForm) {
        var productUrl = purchaseForm.dataset.productQuickCreateUrl;
        var categoryUrl = purchaseForm.dataset.categoryQuickCreateUrl;
        var csrf = purchaseForm.querySelector("input[name='csrfmiddlewaretoken']");
        var qpName = quickProductDialog.querySelector("[data-quick-product-name]");
        var qpCategory = quickProductDialog.querySelector("[data-quick-product-category]");
        var qpUnit = quickProductDialog.querySelector("[data-quick-product-unit]");
        var qpPrice = quickProductDialog.querySelector("[data-quick-product-price]");
        var qpSerialized = quickProductDialog.querySelector("[data-quick-product-serialized]");
        var qpExpiry = quickProductDialog.querySelector("[data-quick-product-expiry]");
        var qpError = quickProductDialog.querySelector("[data-quick-product-error]");
        var qpFieldErrors = {
          name: quickProductDialog.querySelector("[data-quick-product-name-error]"),
          catalog_category: quickProductDialog.querySelector("[data-quick-product-category-error]"),
          sales_unit: quickProductDialog.querySelector("[data-quick-product-unit-error]"),
          selling_price: quickProductDialog.querySelector("[data-quick-product-price-error]")
        };
        var saveProduct = quickProductDialog.querySelector("[data-save-quick-product]");
        var categoryPanel = quickProductDialog.querySelector("[data-quick-category-panel]");
        var quickCategoryName = quickProductDialog.querySelector("[data-quick-category-name]");
        var quickCategoryUnit = quickProductDialog.querySelector("[data-quick-category-unit]");
        var quickCategoryError = quickProductDialog.querySelector("[data-quick-category-error]");
        var quickCategoryNameError = quickProductDialog.querySelector("[data-quick-category-name-error]");
        var quickCategoryUnitError = quickProductDialog.querySelector("[data-quick-category-unit-error]");

        function showQuickError(node, message) {
          node.textContent = message || "";
          node.classList.toggle("hidden", !message);
        }
        function firstFieldError(errors, field) {
          return errors && errors[field] && errors[field][0] ? errors[field][0].message : "";
        }
        function clearProductErrors() {
          Object.keys(qpFieldErrors).forEach(function (field) { showQuickError(qpFieldErrors[field], ""); });
          showQuickError(qpError, "");
        }
        function showProductErrors(errors) {
          clearProductErrors();
          Object.keys(qpFieldErrors).forEach(function (field) {
            showQuickError(qpFieldErrors[field], firstFieldError(errors, field));
          });
          showQuickError(qpError, firstFieldError(errors, "__all__"));
        }
        function clearCategoryErrors() {
          showQuickError(quickCategoryNameError, "");
          showQuickError(quickCategoryUnitError, "");
          showQuickError(quickCategoryError, "");
        }
        function resetQuickProduct() {
          qpName.value = "";
          qpCategory.value = "";
          qpUnit.value = "";
          qpPrice.value = "";
          qpSerialized.checked = false;
          qpExpiry.checked = false;
          quickCategoryName.value = "";
          quickCategoryUnit.value = "";
          categoryPanel.classList.add("hidden");
          clearCategoryErrors();
          clearProductErrors();
        }
        function closeQuickProduct() {
          quickProductDialog.classList.add("hidden");
          quickProductDialog.classList.remove("flex");
          document.body.classList.remove("overflow-hidden");
          openQuickProduct.focus();
        }
        openQuickProduct.addEventListener("click", function () {
          clearProductErrors();
          clearCategoryErrors();
          quickProductDialog.classList.remove("hidden");
          quickProductDialog.classList.add("flex");
          document.body.classList.add("overflow-hidden");
          qpName.focus();
        });
        quickProductDialog.querySelectorAll("[data-close-quick-product], [data-cancel-quick-product]").forEach(function (button) {
          button.addEventListener("click", closeQuickProduct);
        });
        quickProductDialog.addEventListener("click", function (event) {
          if (event.target === quickProductDialog) closeQuickProduct();
        });
        quickProductDialog.addEventListener("keydown", function (event) {
          if (event.key === "Escape") { closeQuickProduct(); return; }
          if (event.key === "Enter") { event.preventDefault(); return; }
          if (event.key !== "Tab") return;
          var focusable = Array.prototype.filter.call(
            quickProductDialog.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled])"),
            function (element) { return element.offsetParent !== null; }
          );
          if (!focusable.length) return;
          var first = focusable[0];
          var last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        });
        qpCategory.addEventListener("change", function () {
          var option = qpCategory.options[qpCategory.selectedIndex];
          if (option && option.dataset.defaultUnit) qpUnit.value = option.dataset.defaultUnit;
        });
        quickProductDialog.querySelector("[data-toggle-quick-category]").addEventListener("click", function () {
          categoryPanel.classList.toggle("hidden");
        });
        quickProductDialog.querySelector("[data-save-quick-category]").addEventListener("click", function () {
          clearCategoryErrors();
          fetch(categoryUrl, { method: "POST", headers: { "X-CSRFToken": csrf.value, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" }, body: new URLSearchParams({ name: quickCategoryName.value.trim(), default_unit: quickCategoryUnit.value }).toString() })
            .then(function (response) { return response.json().then(function (payload) { return { ok: response.ok, payload: payload }; }); })
            .then(function (result) {
              if (!result.ok) {
                showQuickError(quickCategoryNameError, firstFieldError(result.payload.errors, "name"));
                showQuickError(quickCategoryUnitError, firstFieldError(result.payload.errors, "default_unit"));
                showQuickError(quickCategoryError, firstFieldError(result.payload.errors, "__all__"));
                return;
              }
              var category = result.payload.category;
              var option = new Option(category.text, String(category.id), true, true);
              option.dataset.defaultUnit = String(category.default_unit.id);
              qpCategory.appendChild(option);
              qpUnit.value = String(category.default_unit.id);
              categoryPanel.classList.add("hidden");
              quickCategoryName.value = "";
              quickCategoryUnit.value = "";
              clearCategoryErrors();
            }).catch(function () { showQuickError(quickCategoryError, "Category could not be saved. Check the connection."); });
        });
        saveProduct.addEventListener("click", function () {
          clearProductErrors();
          var body = new URLSearchParams({
            name: qpName.value.trim(), catalog_category: qpCategory.value, sales_unit: qpUnit.value,
            selling_price: qpPrice.value, is_serialized: qpSerialized.checked ? "on" : "",
            track_expiry: qpExpiry.checked ? "on" : ""
          });
          saveProduct.disabled = true;
          fetch(productUrl, { method: "POST", headers: { "X-CSRFToken": csrf.value, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" }, body: body.toString() })
            .then(function (response) { return response.json().then(function (payload) { return { ok: response.ok, payload: payload }; }); })
            .then(function (result) {
              if (!result.ok) { showProductErrors(result.payload.errors); return; }
              var line = appendLine(result.payload.product);
              var quantity = line.querySelector("input[name$='-quantity']");
              resetQuickProduct();
              closeQuickProduct();
              if (quantity) quantity.focus();
            }).catch(function () { showQuickError(qpError, "Product could not be saved. Check the connection."); })
            .finally(function () { saveProduct.disabled = false; });
        });
      }

      var pasteDialog = document.querySelector("[data-paste-rows-dialog]");
      var openPaste = document.querySelector("[data-open-paste-rows]");
      if (pasteDialog && openPaste && purchaseForm) {
        var pasteUrl = purchaseForm.dataset.purchaseRowsPreviewUrl;
        var pasteInput = pasteDialog.querySelector("[data-paste-rows-input]");
        var pasteError = pasteDialog.querySelector("[data-paste-rows-error]");
        var previewPaste = pasteDialog.querySelector("[data-preview-paste-rows]");
        var addPasted = pasteDialog.querySelector("[data-add-pasted-rows]");
        var pastePreview = pasteDialog.querySelector("[data-paste-rows-preview]");
        var pasteResults = pasteDialog.querySelector("[data-paste-rows-results]");
        var pasteSummary = pasteDialog.querySelector("[data-paste-rows-summary]");
        var previewedRows = [];

        function pasteMessage(message) {
          pasteError.textContent = message || "";
          pasteError.classList.toggle("hidden", !message);
        }
        function closePaste() {
          pasteDialog.classList.add("hidden");
          pasteDialog.classList.remove("flex");
          document.body.classList.remove("overflow-hidden");
          openPaste.focus();
        }
        openPaste.addEventListener("click", function () {
          pasteMessage("");
          pasteDialog.classList.remove("hidden");
          pasteDialog.classList.add("flex");
          document.body.classList.add("overflow-hidden");
          pasteInput.focus();
        });
        pasteInput.addEventListener("input", function () {
          previewedRows = [];
          pastePreview.classList.add("hidden");
          addPasted.disabled = true;
          pasteMessage("");
        });
        pasteDialog.querySelectorAll("[data-close-paste-rows], [data-cancel-paste-rows]").forEach(function (button) {
          button.addEventListener("click", closePaste);
        });
        pasteDialog.addEventListener("click", function (event) { if (event.target === pasteDialog) closePaste(); });
        pasteDialog.addEventListener("keydown", function (event) {
          if (event.key === "Escape") { closePaste(); return; }
          if (event.key !== "Tab") return;
          var focusable = Array.prototype.filter.call(
            pasteDialog.querySelectorAll("button:not([disabled]), textarea:not([disabled])"),
            function (element) { return element.offsetParent !== null; }
          );
          if (!focusable.length) return;
          var first = focusable[0];
          var last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        });
        previewPaste.addEventListener("click", function () {
          pasteMessage("");
          previewPaste.disabled = true;
          var csrf = purchaseForm.querySelector("input[name='csrfmiddlewaretoken']");
          fetch(pasteUrl, {
            method: "POST",
            headers: { "X-CSRFToken": csrf.value, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
            body: new URLSearchParams({ rows: pasteInput.value }).toString()
          }).then(function (response) {
            return response.json().then(function (payload) { return { ok: response.ok, payload: payload }; });
          }).then(function (result) {
            if (!result.ok) { pasteMessage(result.payload.error || "Check the pasted rows."); return; }
            previewedRows = result.payload.rows || [];
            pasteResults.replaceChildren();
            previewedRows.forEach(function (row) {
              var tr = document.createElement("tr");
              tr.className = row.valid ? "border-t border-slate-200" : "border-t border-rose-200 bg-rose-50";
              [row.row, row.product ? row.product.name + " · " + row.product.sku : "—", row.quantity || "—", row.unit_cost || "—", row.valid ? "Ready" : row.errors.join(" ")].forEach(function (value) {
                var td = document.createElement("td"); td.className = "p-2 align-top"; td.textContent = value; tr.appendChild(td);
              });
              pasteResults.appendChild(tr);
            });
            pasteSummary.textContent = result.payload.summary.valid + " of " + result.payload.summary.total + " rows ready";
            pastePreview.classList.remove("hidden");
            addPasted.disabled = result.payload.summary.valid === 0;
          }).catch(function () { pasteMessage("Rows could not be previewed. Check the connection."); })
            .finally(function () { previewPaste.disabled = false; });
        });
        addPasted.addEventListener("click", function () {
          var firstLine = null;
          previewedRows.filter(function (row) { return row.valid; }).forEach(function (row) {
            var line = appendLine(row.product);
            if (!firstLine) firstLine = line;
            line.querySelector("input[name$='-quantity']").value = row.quantity;
            line.querySelector("input[name$='-unit_cost']").value = row.unit_cost;
            line.querySelector("input[name$='-batch_reference']").value = row.batch_reference;
            line.querySelector("input[name$='-expiry_date']").value = row.expiry_date;
            line.querySelector("textarea[name$='-serial_numbers']").value = row.serial_numbers;
            syncLine(line);
          });
          closePaste();
          if (firstLine) firstLine.querySelector("input[name$='-quantity']").focus();
          previewedRows = [];
          pasteInput.value = "";
          pastePreview.classList.add("hidden");
          addPasted.disabled = true;
        });
      }

      var bulkDialog = document.querySelector("[data-bulk-product-dialog]");
      var openBulk = document.querySelector("[data-open-bulk-products]");
      if (bulkDialog && openBulk && totalForms && template && productSearchUrl) {
        var bulkSearch = bulkDialog.querySelector("[data-bulk-product-search]");
        var bulkResults = bulkDialog.querySelector("[data-bulk-product-results]");
        var bulkCount = bulkDialog.querySelector("[data-bulk-selected-count]");
        var addBulk = bulkDialog.querySelector("[data-add-bulk-products]");
        var clearBulk = bulkDialog.querySelector("[data-clear-bulk-products]");
        var closeBulk = bulkDialog.querySelector("[data-close-bulk-products]");
        var selectedBulk = new Map();
        var bulkTimer = null;
        var bulkController = null;

        function existingProductIds() {
          return new Set(activeLines().map(function (line) {
            var select = line.querySelector("select[name$='-product']");
            return select ? select.value : "";
          }).filter(Boolean));
        }

        function syncBulkCount() {
          bulkCount.textContent = String(selectedBulk.size);
          addBulk.disabled = selectedBulk.size === 0;
        }

        function renderBulk(results) {
          bulkResults.replaceChildren();
          var existing = existingProductIds();
          if (!results.length) {
            var empty = document.createElement("p");
            empty.className = "p-6 text-center text-sm text-slate-500";
            empty.textContent = "No matching stock products.";
            bulkResults.appendChild(empty);
          }
          results.forEach(function (result) {
            var label = document.createElement("label");
            label.className = "flex items-start gap-3 rounded-lg px-3 py-3 hover:bg-slate-50";
            var checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.className = "mt-1 h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500";
            checkbox.checked = selectedBulk.has(String(result.id));
            checkbox.disabled = existing.has(String(result.id));
            var copy = document.createElement("span");
            copy.className = "min-w-0 flex-1";
            var name = document.createElement("span");
            name.className = "block font-semibold text-slate-900";
            name.textContent = result.name;
            var detail = document.createElement("span");
            detail.className = "block text-xs text-slate-500";
            detail.textContent = (result.sku || "No SKU") + " · " + (result.unit || "Unit") + (checkbox.disabled ? " · Already added" : "");
            copy.appendChild(name);
            copy.appendChild(detail);
            label.appendChild(checkbox);
            label.appendChild(copy);
            checkbox.addEventListener("change", function () {
              if (checkbox.checked) selectedBulk.set(String(result.id), result);
              else selectedBulk.delete(String(result.id));
              syncBulkCount();
            });
            bulkResults.appendChild(label);
          });
        }

        function searchBulk() {
          if (bulkController) bulkController.abort();
          bulkController = new AbortController();
          bulkResults.innerHTML = '<p class="p-6 text-center text-sm text-slate-500">Searching products…</p>';
          fetch(productSearchUrl + "?q=" + encodeURIComponent(bulkSearch.value.trim()), { signal: bulkController.signal })
            .then(function (response) { if (!response.ok) throw new Error(); return response.json(); })
            .then(function (payload) { renderBulk(payload.results || []); })
            .catch(function (error) {
              if (error.name !== "AbortError") bulkResults.innerHTML = '<p class="p-6 text-center text-sm text-rose-700">Products could not be loaded. Try again.</p>';
            });
        }

        function closeBulkDialog() {
          bulkDialog.classList.add("hidden");
          bulkDialog.classList.remove("flex");
          document.body.classList.remove("overflow-hidden");
          openBulk.focus();
        }

        openBulk.addEventListener("click", function () {
          selectedBulk.clear();
          syncBulkCount();
          bulkDialog.classList.remove("hidden");
          bulkDialog.classList.add("flex");
          document.body.classList.add("overflow-hidden");
          bulkSearch.value = "";
          bulkSearch.focus();
          searchBulk();
        });
        closeBulk.addEventListener("click", closeBulkDialog);
        bulkDialog.addEventListener("click", function (event) { if (event.target === bulkDialog) closeBulkDialog(); });
        bulkDialog.addEventListener("keydown", function (event) {
          if (event.key !== "Tab") return;
          var focusable = Array.prototype.filter.call(
            bulkDialog.querySelectorAll("button:not([disabled]), input:not([disabled])"),
            function (element) { return element.offsetParent !== null; }
          );
          if (!focusable.length) return;
          var first = focusable[0];
          var last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault(); last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault(); first.focus();
          }
        });
        document.addEventListener("keydown", function (event) {
          if (event.key === "Escape" && !bulkDialog.classList.contains("hidden")) closeBulkDialog();
        });
        bulkSearch.addEventListener("input", function () {
          window.clearTimeout(bulkTimer);
          bulkTimer = window.setTimeout(searchBulk, 200);
        });
        clearBulk.addEventListener("click", function () {
          selectedBulk.clear();
          syncBulkCount();
          searchBulk();
        });
        addBulk.addEventListener("click", function () {
          var firstAdded = null;
          selectedBulk.forEach(function (result) {
            if (!existingProductIds().has(String(result.id))) {
              var addedLine = appendLine(result);
              if (!firstAdded) firstAdded = addedLine;
            }
          });
          closeBulkDialog();
          if (firstAdded) {
            var quantity = firstAdded.querySelector("input[name$='-quantity']");
            if (quantity) quantity.focus();
          }
        });
      }
    }

    var adjustment = document.querySelector("[data-stock-adjustment]");
    if (adjustment) {
      var stockNode = document.getElementById("stock-level-data");
      var stockLevels = stockNode ? JSON.parse(stockNode.textContent) : {};
      var productInput = document.getElementById("id_product");
      var quantityInput = document.getElementById("id_quantity");
      var currentOutput = document.querySelector("[data-current-stock]");
      var expectedOutput = document.querySelector("[data-expected-stock]");
      var warning = document.querySelector("[data-negative-warning]");

      function syncAdjustment() {
        var current = number(productInput && stockLevels[productInput.value]);
        var quantity = number(quantityInput && quantityInput.value);
        var direction = adjustment.querySelector("input[name='direction']:checked");
        var expected = current + (direction && direction.value === "decrease" ? -quantity : quantity);
        if (currentOutput) currentOutput.textContent = quantityFormatter.format(current);
        if (expectedOutput) expectedOutput.textContent = quantityFormatter.format(expected);
        if (warning) warning.classList.toggle("hidden", expected >= 0);
      }

      [productInput, quantityInput].forEach(function (input) {
        if (input) input.addEventListener("input", syncAdjustment);
        if (input) input.addEventListener("change", syncAdjustment);
      });
      adjustment.querySelectorAll("input[name='direction']").forEach(function (input) {
        input.addEventListener("change", syncAdjustment);
      });
      syncAdjustment();
    }

    var cart = document.querySelector("[data-cart-subtotal]");
    if (cart) {
      var discount = document.getElementById("id_discount_amount");
      var rate = document.getElementById("id_tax_rate");

      function syncCartTotals() {
        var subtotal = number(cart.dataset.cartSubtotal);
        var taxableSubtotal = number(cart.dataset.cartTaxableSubtotal);
        var discountValue = Math.min(Math.max(number(discount && discount.value), 0), subtotal);
        var rateValue = Math.max(number(rate && rate.value), 0);
        var taxableDiscount = subtotal > 0 ? discountValue * taxableSubtotal / subtotal : 0;
        var taxable = Math.max(taxableSubtotal - taxableDiscount, 0);
        var tax = Math.round((taxable * rateValue / 100) * 100) / 100;
        var discountOutput = document.querySelector("[data-live-discount]");
        var rateOutput = document.querySelector("[data-live-tax-rate]");
        var taxOutput = document.querySelector("[data-live-tax]");
        var totalOutput = document.querySelector("[data-live-total]");
        var paymentOutputs = document.querySelectorAll("[data-pos-payment-total]");
        var total = taxable === 0 && taxableSubtotal === 0
          ? subtotal - discountValue
          : subtotal - discountValue + tax;
        if (discountOutput) discountOutput.textContent = "-" + discountValue.toFixed(2);
        if (rateOutput) rateOutput.textContent = rateValue.toFixed(2);
        if (taxOutput) taxOutput.textContent = tax.toFixed(2);
        if (totalOutput) totalOutput.textContent = total.toFixed(2);
        paymentOutputs.forEach(function (output) { output.textContent = total.toFixed(2); });
      }

      if (discount) discount.addEventListener("input", syncCartTotals);
      if (rate) rate.addEventListener("input", syncCartTotals);
      syncCartTotals();
    }

    var pos = document.querySelector("[data-pos]");
    if (pos) {
      var formatter = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

      function showPosFeedback(message, level) {
        var feedback = pos.querySelector("[data-pos-feedback]");
        if (!feedback) return;
        feedback.textContent = message || "";
        feedback.className = "rounded-lg border px-3 py-2 text-sm";
        feedback.classList.add(level === "warning" ? "border-amber-200" : "border-emerald-200");
        feedback.classList.add(level === "warning" ? "bg-amber-50" : "bg-emerald-50");
        feedback.classList.add(level === "warning" ? "text-amber-900" : "text-emerald-900");
      }

      function updatePos(data) {
        var container = pos.querySelector("[data-pos-lines-container]");
        if (container) container.innerHTML = data.cart_html;
        var checkout = pos.querySelector("[data-pos-checkout-container]");
        if (checkout) checkout.innerHTML = data.checkout_html;
        pos.dataset.cartSubtotal = data.subtotal;
        pos.dataset.cartTaxableSubtotal = data.taxable_subtotal;
        var subtotal = pos.querySelector("[data-pos-subtotal]");
        var discountOutput = pos.querySelector("[data-live-discount]");
        var rateOutput = pos.querySelector("[data-live-tax-rate]");
        var taxOutput = pos.querySelector("[data-live-tax]");
        var totalOutput = pos.querySelector("[data-live-total]");
        if (subtotal) subtotal.textContent = formatter.format(number(data.subtotal));
        if (discountOutput) discountOutput.textContent = "-" + formatter.format(number(data.discount));
        if (rateOutput) rateOutput.textContent = formatter.format(number(data.tax_rate));
        if (taxOutput) taxOutput.textContent = formatter.format(number(data.tax));
        if (totalOutput) totalOutput.textContent = formatter.format(number(data.grand_total));
        pos.querySelectorAll("[data-pos-line-count], [data-pos-cart-count]").forEach(function (output) {
          output.textContent = data.line_count;
        });

        pos.querySelectorAll("[data-pos-product-card]").forEach(function (card) {
          var productId = card.dataset.posProductCard;
          var line = (data.lines || []).find(function (item) { return String(item.product_id) === String(productId); });
          var quantity = card.querySelector("[data-pos-card-quantity]");
          var price = card.querySelector("[data-pos-card-price]");
          var priceLabel = card.querySelector("[data-pos-card-price-label]");
          if (quantity) {
            quantity.classList.toggle("hidden", !line);
            quantity.textContent = line ? "In cart: " + quantityFormatter.format(number(line.quantity)) : "";
          }
          if (price && line) price.textContent = formatter.format(number(line.unit_price));
          if (priceLabel && line) {
            var pricingLabels = { wholesale: "Wholesale", technician: "Technician", standard: "Standard", retail: "Legacy retail" };
            priceLabel.textContent = pricingLabels[line.pricing_mode] || "Transaction price";
          }
        });
      }

      var detailsForm = pos.querySelector("form[data-pos-details]");
      var detailsTimer;
      var detailsRequest;

      function setCheckoutPending(pending) {
        pos.querySelectorAll("[data-pos-checkout-container] button[type='submit']").forEach(function (button) {
          button.disabled = pending;
          button.setAttribute("aria-disabled", pending ? "true" : "false");
        });
      }

      function savePosDetails() {
        if (!detailsForm) return;
        setCheckoutPending(true);
        if (detailsRequest) detailsRequest.abort();
        detailsRequest = new AbortController();
        var token = detailsForm.querySelector("input[name='csrfmiddlewaretoken']");
        fetch(detailsForm.action || window.location.href, {
          method: "POST",
          body: new FormData(detailsForm),
          credentials: "same-origin",
          signal: detailsRequest.signal,
          headers: {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": token ? token.value : ""
          }
        }).then(function (response) {
          return response.json().then(function (data) { return { response: response, data: data }; });
        }).then(function (result) {
          if (!result.response.ok) {
            if (result.data.cart_html) updatePos(result.data);
            setCheckoutPending(true);
            var detailsPanel = detailsForm.closest("details");
            if (detailsPanel) detailsPanel.open = true;
            showPosFeedback(result.data.message || "Sale details could not be saved.", "warning");
            return;
          }
          updatePos(result.data);
          setCheckoutPending(false);
          showPosFeedback(result.data.message, result.data.level);
        }).catch(function (error) {
          if (error.name !== "AbortError") showPosFeedback("Connection issue. Sale details were not saved; try again.", "warning");
        });
      }

      function queuePosDetailsSave() {
        syncCartTotals();
        setCheckoutPending(true);
        window.clearTimeout(detailsTimer);
        detailsTimer = window.setTimeout(savePosDetails, 450);
      }

      if (detailsForm) {
        detailsForm.addEventListener("input", queuePosDetailsSave);
        detailsForm.addEventListener("change", queuePosDetailsSave);
        detailsForm.addEventListener("submit", function (event) {
          if (event.submitter && event.submitter.matches("[data-pos-convert]")) {
            window.clearTimeout(detailsTimer);
            if (detailsRequest) detailsRequest.abort();
            return;
          }
          event.preventDefault();
          window.clearTimeout(detailsTimer);
          savePosDetails();
        });
      }

      pos.addEventListener("submit", function (event) {
        var form = event.target.closest("form[data-pos-adjust]");
        if (!form) return;
        event.preventDefault();
        var button = form.querySelector("button");
        if (button && button.disabled) return;
        if (button) button.disabled = true;
        var token = form.querySelector("input[name='csrfmiddlewaretoken']");
        fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": token ? token.value : ""
          }
        }).then(function (response) {
          return response.json().then(function (data) { return { response: response, data: data }; });
        }).then(function (result) {
          if (result.data.redirect_url) {
            window.location.assign(result.data.redirect_url);
            return;
          }
          if (!result.response.ok) {
            showPosFeedback(result.data.message || "The cart could not be updated.", "warning");
            return;
          }
          updatePos(result.data);
          if (result.data.message) showPosFeedback(result.data.message, result.data.level);
        }).catch(function () {
          showPosFeedback("Connection issue. Your cart was not changed; try again.", "warning");
        }).finally(function () {
          if (button) button.disabled = false;
        });
      });
    }
  });
})();
