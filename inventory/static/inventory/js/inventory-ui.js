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
    var productForm = document.querySelector("[data-product-form]");
    if (productForm) {
      var itemType = document.getElementById("id_item_type");
      var trackStock = document.getElementById("id_track_stock");
      var serialized = document.getElementById("id_is_serialized");
      var buying = document.getElementById("id_buying_price");
      var selling = document.getElementById("id_selling_price");
      var retail = document.getElementById("id_retail_price");
      var wholesaleToggle = document.getElementById("id_allow_wholesale");
      var wholesalePanel = document.querySelector("[data-wholesale-panel]");

      function money(value) {
        if (value === null || !isFinite(value)) return "—";
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value) + " TZS";
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
      }

      function syncPricing() {
        var buy = optionalNumber(buying);
        var sell = optionalNumber(selling);
        var retailValue = optionalNumber(retail);
        var effective = retailValue !== null ? retailValue : sell;
        var profit = buy !== null && effective !== null ? effective - buy : null;
        var margin = document.querySelector("[data-product-margin]");
        var marginRate = document.querySelector("[data-product-margin-rate]");
        var effectiveRetail = document.querySelector("[data-product-effective-retail]");
        if (margin) margin.textContent = money(profit);
        if (marginRate) marginRate.textContent = profit !== null && effective ? ((profit / effective) * 100).toFixed(1) + "%" : "—";
        if (effectiveRetail) effectiveRetail.textContent = money(effective);
      }

      function syncWholesale() {
        if (wholesalePanel && wholesaleToggle) wholesalePanel.classList.toggle("hidden", !wholesaleToggle.checked);
      }

      [itemType, trackStock, serialized].forEach(function (input) {
        if (input) input.addEventListener("change", syncProductFields);
      });
      [buying, selling, retail].forEach(function (input) {
        if (input) input.addEventListener("input", syncPricing);
      });
      if (wholesaleToggle) wholesaleToggle.addEventListener("change", syncWholesale);
      syncProductFields();
      syncPricing();
      syncWholesale();
    }

    var formset = document.querySelector("[data-purchase-formset]");
    if (formset) {
      var totalForms = document.getElementById("id_lines-TOTAL_FORMS");
      var template = document.getElementById("purchase-line-template");
      var addButton = document.querySelector("[data-add-purchase-line]");
      var metaNode = document.getElementById("purchase-product-meta");
      var productMeta = metaNode ? JSON.parse(metaNode.textContent) : {};

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
            : "Choose a stock-tracked product.";
        }
      }

      function bindLine(line) {
        var product = line.querySelector("select[name$='-product']");
        if (product) product.addEventListener("change", function () { syncLine(line); });
        var remove = line.querySelector("[data-remove-line]");
        if (remove) {
          remove.addEventListener("click", function () {
            var deletion = line.querySelector("input[name$='-DELETE']");
            if (deletion) deletion.checked = true;
            line.classList.add("hidden");
          });
        }
        syncLine(line);
      }

      formset.querySelectorAll("[data-purchase-line]").forEach(bindLine);
      if (addButton && totalForms && template) {
        addButton.addEventListener("click", function () {
          var index = parseInt(totalForms.value, 10);
          var wrapper = document.createElement("div");
          wrapper.innerHTML = template.innerHTML.replace(/__prefix__/g, String(index)).trim();
          var line = wrapper.firstElementChild;
          formset.appendChild(line);
          totalForms.value = String(index + 1);
          bindLine(line);
          var first = line.querySelector("select, input");
          if (first) first.focus();
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
        if (currentOutput) currentOutput.textContent = current.toFixed(2);
        if (expectedOutput) expectedOutput.textContent = expected.toFixed(2);
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
        var discountValue = Math.min(Math.max(number(discount && discount.value), 0), subtotal);
        var rateValue = Math.max(number(rate && rate.value), 0);
        var taxable = subtotal - discountValue;
        var tax = Math.round((taxable * rateValue / 100) * 100) / 100;
        var discountOutput = document.querySelector("[data-live-discount]");
        var rateOutput = document.querySelector("[data-live-tax-rate]");
        var taxOutput = document.querySelector("[data-live-tax]");
        var totalOutput = document.querySelector("[data-live-total]");
        if (discountOutput) discountOutput.textContent = "-" + discountValue.toFixed(2);
        if (rateOutput) rateOutput.textContent = rateValue.toFixed(2);
        if (taxOutput) taxOutput.textContent = tax.toFixed(2);
        if (totalOutput) totalOutput.textContent = (taxable + tax).toFixed(2);
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
            quantity.textContent = line ? "In cart: " + line.quantity : "";
          }
          if (price && line) price.textContent = formatter.format(number(line.unit_price));
          if (priceLabel && line) priceLabel.textContent = line.pricing_mode === "wholesale" ? "Wholesale price" : "Retail price";
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
