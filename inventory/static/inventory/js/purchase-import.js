/* Purchase import state is advisory. Preview is read-only; draft save revalidates. */
(function () {
  "use strict";
  function ready() {
    var form = document.querySelector(".purchase-workspace-form");
    var dialog = document.querySelector("[data-paste-rows-dialog]");
    if (!form || !dialog) return;
    var $ = function (selector) { return dialog.querySelector(selector); };
    var $$ = function (selector) { return Array.from(dialog.querySelectorAll(selector)); };
    var opener = form.querySelector("[data-open-paste-rows]");
    var hidden = form.querySelector("[data-import-rows]");
    var applied = form.querySelector("[data-import-applied]");
    var appliedText = form.querySelector("[data-import-applied-text]");
    var input = $("[data-paste-rows-input]");
    var file = $("[data-import-file]");
    var primary = $("[data-import-primary]");
    var editor = $("[data-import-editor]");
    var rows = [], step = 1, method = "upload", filter = "all", page = 0, active = null;
    var revision = 0, busy = false, sourceName = "Pasted spreadsheet", sourceFormat = "", sourceIgnored = 0;
    var searchRevision = 0, searchTimer, returnFocus, background = [], savedOverflow, savedScroll = 0;
    var mapping = null;
    var fields = ["sku", "product_name", "quantity", "unit_cost", "category", "unit", "selling_price",
      "brand", "model_number", "serialized", "track_expiry", "batch_reference", "expiry_date", "serial_numbers", "staging_reference"];
    var snapshots = ["expected_sku", "expected_serialized", "expected_expiry", "expected_unit_id"];
    var PAGE_SIZE = 20;
    function node(tag, cls, text) {
      var el = document.createElement(tag);
      if (cls) el.className = cls;
      if (text !== undefined) el.textContent = text;
      return el;
    }
    function button(text, cls, action) {
      var el = node("button", cls, text); el.type = "button";
      el.addEventListener("click", action); return el;
    }
    function compact(row) {
      var result = { row: row.row, resolution: row.resolution || "", product_id: row.product_id || "", excluded: !!row.excluded };
      fields.concat(snapshots).forEach(function (key) { if (row[key] !== undefined) result[key] = row[key]; });
      // Preserve formula/length errors on the reviewed payload as well as raw values.
      result.input_errors = row.input_errors || [];
      return result;
    }
    function group(row) { return row.excluded ? "excluded" : !row.valid ? "attention" : row.status === "matched" ? "matched" : "new"; }
    function counts() {
      var value = { all: rows.length, matched: 0, new: 0, attention: 0, excluded: 0 };
      rows.forEach(function (r) { value[group(r)]++; }); return value;
    }
    function filtered() { return rows.filter(function (r) { return filter === "all" || group(r) === filter; }); }
    function message(text) {
      var el = $("[data-import-error]"); el.textContent = text || ""; el.classList.toggle("hidden", !text);
      if (text) el.focus();
    }
    function changed(row) {
      revision++; busy = false;
      if (row) { row.valid = false; row.status = row.excluded ? "excluded" : "unresolved"; row.errors = ["Changes need validation. Save this item to check them."]; }
      renderList();
    }
    function updateFooter() {
      var c = counts(), included = c.all - c.excluded;
      $("[data-import-has-header]").disabled = busy;
      $("[data-import-choose-columns]").disabled = busy;
      $("[data-import-summary]").textContent = rows.length ? c.matched + " matched · " + c.new + " new · " + c.attention + " need attention · " + c.excluded + " excluded" : "";
      $("[data-import-hint]").textContent = step === 1 ? "Choose a file or paste spreadsheet cells to begin." : c.attention ? "Resolve or exclude remaining items to continue." : step === 2 ? "All included items are ready. Continue to the summary." : "Adds items to this workspace. Save the draft to persist them.";
      primary.textContent = busy ? "Checking items…" : step === 1 ? "Review items" : step === 2 ? "Continue to summary" : "Add " + included + " items to purchase";
      primary.disabled = busy || (step > 1 && (!included || c.attention > 0));
      if (step === 1 && mapping) {
        $("[data-import-summary]").textContent = mapping.row_count + " rows · " + mapping.column_count + " source columns";
        $("[data-import-hint]").textContent = mappingProblem() || "Confirm your column choices, then review the items.";
        primary.textContent = busy ? "Checking items…" : "Review mapped items";
        primary.disabled = busy || Boolean(mappingProblem());
      } else if (step === 1 && $("[data-import-format]").value === "custom" && !busy) primary.textContent = "Choose columns";
      $("[data-import-back]").textContent = step === 1 ? "Cancel" : "Back";
      $$("[data-import-filter]").forEach(function (tab) {
        tab.querySelector("span").textContent = c[tab.dataset.importFilter];
        tab.setAttribute("aria-selected", String(tab.dataset.importFilter === filter));
        tab.tabIndex = tab.dataset.importFilter === filter ? 0 : -1;
      });
    }
    function setStep(value, focus) {
      step = value; message("");
      $$("[data-import-page]").forEach(function (section) { section.classList.toggle("hidden", Number(section.dataset.importPage) !== step); });
      $$("[data-import-step]").forEach(function (li) {
        var n = Number(li.dataset.importStep);
        if (n === step) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
        li.classList.toggle("import-complete", n < step);
      });
      if (step === 3) renderFinal();
      updateFooter();
      if (focus) { var target = step === 1 ? (method === "upload" ? file : input) : step === 2 ? $("[data-import-filter='all']") : primary; target.focus(); }
    }
    function setMethod(value) {
      method = value;
      $$("[data-import-method]").forEach(function (tab) {
        var selected = tab.dataset.importMethod === method;
        tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
      });
      $("#import-upload").classList.toggle("hidden", method !== "upload");
      $("#import-paste").classList.toggle("hidden", method !== "paste");
    }
    function invalidateSource() {
      revision++; busy = false; rows = []; active = null; page = 0; filter = "all";
      mapping = null; $("[data-import-column-mapping]").classList.add("hidden");
      sourceIgnored = 0;
      editor.replaceChildren(node("p", "", "Select an item to review its details."));
      $("[data-import-columns]").classList.remove("import-editor-open");
      renderList(); setStep(1);
    }
    function show() {
      returnFocus = document.activeElement; savedOverflow = document.body.style.overflow;
      savedScroll = window.scrollY;
      document.documentElement.classList.add("purchase-import-open");
      document.body.style.top = -savedScroll + "px";
      dialog.classList.remove("hidden"); dialog.style.display = "flex";
      // Inert siblings at every ancestor, leaving only this dialog interactive.
      background = [];
      for (var current = dialog; current && current !== document.body; current = current.parentElement) {
        Array.from(current.parentElement.children).forEach(function (sibling) {
          if (sibling !== current && sibling.tagName !== "SCRIPT" && !sibling.inert) { sibling.inert = true; background.push(sibling); }
        });
      }
      document.body.style.overflow = "hidden"; setStep(rows.length ? 2 : 1, true);
    }
    function close() {
      // Closing preserves source, resolutions and row edits for the next opening.
      dialog.classList.add("hidden"); dialog.style.display = "none";
      background.forEach(function (el) { el.inert = false; }); background = [];
      document.body.style.overflow = savedOverflow || "";
      document.documentElement.classList.remove("purchase-import-open");
      document.body.style.top = ""; window.scrollTo(0, savedScroll);
      (returnFocus || opener).focus();
    }
    async function post(data) {
      var response = await fetch(form.dataset.purchaseRowsPreviewUrl, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value }, body: data });
      var payload;
      try { payload = await response.json(); } catch (_) { throw Error("The server could not check these items. Try again."); }
      if (!response.ok) {
        var error = Error(payload.error || "Check the items and try again."); error.payload = payload; throw error;
      }
      return payload;
    }
    function sourceData() {
      var data = new FormData();
      if (method === "upload") {
        if (!file.files.length) throw Error("Choose an .xlsx file first.");
        data.append("file", file.files[0]); sourceName = file.files[0].name;
      } else {
        data.append("rows", input.value);
        var layout = $("[data-import-format]").value;
        data.append("format", layout === "custom" ? "auto" : layout);
        sourceName = "Pasted spreadsheet";
      }
      return data;
    }
    function mappingProblem() {
      if (!mapping) return "Choose your spreadsheet columns first.";
      if (mapping.needs_inspection) return "Column headings changed. Choose columns again to refresh the preview.";
      var choices = mapping.columns.map(function (column) { return column.field; });
      if (choices.some(function (choice) { return !choice; })) return "Choose a field or Do not import for every source column.";
      var selected = choices.filter(function (choice) { return choice !== "__ignore__"; });
      if (new Set(selected).size !== selected.length) return "Each purchase field can be assigned to only one column.";
      if (!selected.includes("quantity") || !selected.includes("unit_cost") || !(selected.includes("sku") || selected.includes("product_name"))) return "Map Quantity, Unit cost, and SKU or Product name before review.";
      return "";
    }
    function updateMapping() {
      if (!mapping) return;
      var choices = mapping.columns.map(function (column) { return column.field; });
      $$("[data-import-map-column]").forEach(function (select, i) {
        select.setAttribute("aria-invalid", String(Boolean(choices[i]) && choices[i] !== "__ignore__" && choices.filter(function (value) { return value === choices[i]; }).length > 1));
      });
      var ignored = choices.filter(function (choice) { return choice === "__ignore__"; }).length;
      $("[data-import-mapping-status]").textContent = (mappingProblem() || "Column choices are ready for review.") + (ignored ? " " + ignored + " source columns will not be imported." : " All source columns will be imported once mapped.");
      updateFooter();
    }
    function renderMapping(structure, preserve) {
      mapping = structure;
      rows = []; active = null; page = 0; filter = "all";
      if (preserve && preserve.length === mapping.columns.length) mapping.columns.forEach(function (column, i) { column.field = preserve[i]; });
      $("[data-import-column-mapping]").classList.remove("hidden");
      $("[data-import-has-header]").checked = mapping.has_header;
      $("[data-import-mapping-description]").textContent = mapping.row_count + " rows detected across " + mapping.column_count + " columns. Examples are shown exactly as supplied, including blank cells.";
      var container = $("[data-import-mapping-columns]"); container.replaceChildren();
      mapping.columns.forEach(function (column, index) {
        var wrap = node("div", "import-map-column"), source = node("div");
        source.append(node("strong", "", "Column " + column.source_column + (column.heading ? " · " + column.heading : "")), node("p", "", column.examples.join(" / ")));
        var label = node("label", "", "Import as"), select = node("select");
        select.dataset.importMapColumn = String(index);
        select.setAttribute("aria-label", "Column " + column.source_column + " purchase field");
        [{ value: "", label: "Choose field…" }].concat(mapping.fields, [{ value: "__ignore__", label: "Do not import this column" }]).forEach(function (choice) { var option = node("option", "", choice.label); option.value = choice.value; select.appendChild(option); });
        select.value = column.field;
        select.addEventListener("change", function () {
          column.field = select.value; revision++; busy = false; rows = []; active = null;
          renderList(); updateMapping(); message("");
        });
        label.appendChild(select); wrap.append(source, label); container.appendChild(wrap);
      });
      renderList(); setStep(1); updateMapping(); $("#import-mapping-title").focus();
      $("[data-import-column-mapping]").scrollIntoView({ block: "nearest" });
    }
    async function inspectColumns(headerChoice, preserve) {
      if (busy) return;
      if (mapping) {
        if (headerChoice === undefined) headerChoice = mapping.has_header;
        if (preserve === undefined) preserve = mapping.columns.map(function (column) { return column.field; });
      }
      var data;
      try { data = sourceData(); } catch (exc) { message(exc.message); return; }
      data.append("operation", "inspect_columns");
      if (headerChoice !== undefined) data.append("has_header", String(headerChoice));
      var expected = ++revision; busy = true; updateFooter(); message("");
      try {
        var payload = await post(data);
        if (revision !== expected) return;
        busy = false; renderMapping(payload.column_mapping, preserve);
      } catch (exc) { if (revision === expected) message(exc.message); }
      finally { if (revision === expected) { busy = false; updateFooter(); } }
    }
    async function validate(source, next) {
      if (busy) return;
      var data = new FormData(), expected = revision;
      if (source) {
        try { data = sourceData(); } catch (exc) { message(exc.message); return; }
        if (mapping) {
          if (mappingProblem()) { message(mappingProblem()); return; }
          data.append("column_mapping", JSON.stringify(mapping.columns.map(function (column) { return column.field; })));
          data.append("has_header", String(mapping.has_header));
        }
      } else data.append("review_rows", JSON.stringify(rows.map(compact)));
      busy = true; message(""); updateFooter();
      try {
        var payload = await post(data);
        if (revision !== expected) return;
        busy = false;
        var selected = rows.map(function (r) { return !!r.bulk_selected; });
        rows = payload.rows || [];
        rows.forEach(function (r, i) { r.bulk_selected = !source && selected[i]; });
        if (source) { active = null; page = 0; filter = "all"; sourceFormat = payload.format_label || "Spreadsheet columns"; sourceIgnored = payload.ignored_columns || 0; }
        $("[data-import-source-name]").textContent = sourceName;
        $("[data-import-source-info]").textContent = rows.length + " rows detected · " + sourceFormat + (sourceIgnored ? " · " + sourceIgnored + " source columns not imported" : "");
        renderList();
        if (source) setStep(2, true);
        if (active !== null && rows[active]) renderEditor(rows[active]);
        if (next) {
          var nextIndex = rows.findIndex(function (r, i) { return i > active && group(r) === "attention"; });
          if (nextIndex < 0) nextIndex = rows.findIndex(function (r) { return group(r) === "attention"; });
          if (nextIndex >= 0) openRow(nextIndex);
        }
      } catch (exc) {
        if (revision === expected) {
          if (source && exc.payload && exc.payload.code === "column_mapping_required") { busy = false; renderMapping(exc.payload.column_mapping); }
          message(exc.message);
        }
      }
      finally { if (revision === expected) { busy = false; updateFooter(); } }
    }
    function amount(value) {
      var parts = String(value || "0").split(".");
      return parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (parts[1] ? "." + parts[1] : "");
    }
    function renderList() {
      var list = $("[data-import-results]"); list.replaceChildren();
      var matches = filtered(); page = Math.min(page, Math.max(0, Math.ceil(matches.length / PAGE_SIZE) - 1));
      var visible = matches.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
      visible.forEach(function (row) {
        var index = rows.indexOf(row), g = group(row), tr = node("tr", g === "attention" ? "import-attention" : "");
        tr.setAttribute("aria-selected", String(index === active));
        function cell(cls) { var td = node("td", cls); tr.appendChild(td); return td; }
        var check = node("input"); check.type = "checkbox"; check.checked = !!row.bulk_selected;
        check.setAttribute("aria-label", "Select source row " + row.row);
        check.addEventListener("change", function () { row.bulk_selected = check.checked; renderList(); }); cell().appendChild(check);
        var name = row.product ? row.product.name : row.product_name || row.sku || "Unnamed product";
        var open = button("", "import-row-open", function () { openRow(index); });
        open.append(node("strong", "", name), node("small", "", (row.product ? row.product.sku : row.sku || "SKU assigned on save") + " · Row " + row.row));
        cell().appendChild(open); cell("import-number").textContent = amount(row.quantity); cell("import-number").textContent = amount(row.unit_cost);
        var labels = { attention: "! Needs attention", matched: "✓ Matched", new: "+ New", excluded: "− Excluded" };
        var badge = node("span", "import-status import-status-" + g, labels[g]);
        badge.title = (row.errors || []).join(" "); cell().appendChild(badge);
        var more = button("›", "import-link", function () { openRow(index); }); more.setAttribute("aria-label", "Review source row " + row.row); cell().appendChild(more);
        list.appendChild(tr);
      });
      if (!visible.length) { var empty = node("tr"); var td = node("td", "", "No items in this filter."); td.colSpan = 6; empty.appendChild(td); list.appendChild(empty); }
      $("[data-import-page-label]").textContent = matches.length ? "Showing " + (page * PAGE_SIZE + 1) + "–" + Math.min((page + 1) * PAGE_SIZE, matches.length) + " of " + matches.length : "0 items";
      $("[data-import-previous]").disabled = page === 0;
      $("[data-import-next]").disabled = (page + 1) * PAGE_SIZE >= matches.length;
      var selected = rows.filter(function (r) { return r.bulk_selected; });
      var newSelected = selected.filter(function (r) { return r.resolution === "new" && !r.excluded; });
      $("[data-import-bulk]").classList.toggle("hidden", !selected.length);
      $("[data-import-selected-count]").textContent = selected.length + " selected · " + newSelected.length + " new";
      $("[data-import-bulk-apply]").disabled = !newSelected.length || busy;
      $("[data-import-exclude]").disabled = busy;
      var all = $("[data-import-select-page]");
      all.checked = !!visible.length && visible.every(function (r) { return r.bulk_selected; });
      all.indeterminate = visible.some(function (r) { return r.bulk_selected; }) && !all.checked;
      $("[data-import-list-description]").textContent = counts().attention + " items need a decision. Select an item to review.";
      updateFooter();
    }
    function openRow(index) {
      active = index; $("[data-import-columns]").classList.add("import-editor-open");
      renderList(); renderEditor(rows[index]); editor.querySelector("h3").focus();
    }
    function field(parent, row, key, label, choices) {
      var wrap = node("label", "import-field", label), control, displayValue = row[key] || "";
      if (key === "serialized" || key === "track_expiry") {
        if (/^(yes|true|1)$/i.test(displayValue)) displayValue = "Yes";
        else if (/^(no|false|0)?$/i.test(displayValue)) displayValue = "No";
      }
      if (choices) {
        control = node("select");
        choices.forEach(function (choice) { var opt = node("option", "", choice.label); opt.value = choice.value; control.appendChild(opt); });
        // An imported invalid value stays visible until corrected.
        if (displayValue && !choices.some(function (c) { return c.value === displayValue; })) {
          var invalid = node("option", "", displayValue + " (check value)"); invalid.value = displayValue; control.appendChild(invalid);
        }
      } else { control = node(key === "serial_numbers" ? "textarea" : "input"); if (key !== "serial_numbers") control.type = key === "expiry_date" ? "date" : "text"; }
      control.value = displayValue;
      control.dataset.field = key;
      control.addEventListener(choices ? "change" : "input", function () {
        row[key] = control.value;
        if (key === "category") row.unit = defaultCategoryUnit(row.category);
        changed(row);
        if (key === "category" || key === "serialized" || key === "track_expiry") {
          renderEditor(row);
          var replacement = editor.querySelector('[data-field="' + key + '"]');
          if (replacement) replacement.focus();
        }
      });
      wrap.appendChild(control); parent.appendChild(wrap); return control;
    }
    function categoryChoices() {
      return Array.from($("[data-bulk-category]").options).map(function (o) { return { value: o.value, label: o.textContent }; });
    }
    function unitChoices(categoryName) {
      var category = Array.from($("[data-bulk-category]").options).find(function (o) { return o.value === categoryName; });
      var allowed = category && category.dataset.units ? category.dataset.units.split(",") : [];
      return Array.from($("[data-bulk-unit]").options).filter(function (o) { return !o.value || allowed.includes(o.dataset.id); }).map(function (o) { return { value: o.value, label: o.textContent }; });
    }
    function defaultCategoryUnit(categoryName) {
      var category = Array.from($("[data-bulk-category]").options).find(function (option) { return option.value === categoryName; });
      if (!category || !category.value) return "";
      var allowed = unitChoices(categoryName).filter(function (choice) { return choice.value; });
      var preferred = Array.from($("[data-bulk-unit]").options).find(function (option) {
        return option.dataset.id === category.dataset.defaultUnit && allowed.some(function (choice) { return choice.value === option.value; });
      });
      if (preferred) return preferred.value;
      return allowed.length === 1 ? allowed[0].value : "";
    }
    function renderEditor(row) {
      searchRevision++; clearTimeout(searchTimer); editor.replaceChildren();
      editor.appendChild(button("‹ Back to items", "import-link import-mobile-back", function () { $("[data-import-columns]").classList.remove("import-editor-open"); var first = $("[data-import-results] button"); if (first) first.focus(); }));
      var heading = node("h3", "", "Resolve item"); heading.tabIndex = -1;
      editor.append(heading, node("p", "", "Source row " + row.row + " · " + (row.product_name || row.sku || "New item")));
      var issue = node("div", "import-row-issue");
      issue.textContent = row.excluded ? "Excluded from this purchase." : (row.errors || []).join(" ") || (row.product ? "Matched to your catalog. Catalog settings are authoritative." : "This item is ready for draft save.");
      editor.appendChild(issue);
      if (row.excluded) {
        editor.appendChild(button("Include this item", "jims-btn jims-btn-secondary", function () { row.excluded = false; changed(row); validate(false); })); return;
      }
      var resolution = node("div", "import-resolution");
      ["existing", "new"].forEach(function (value) {
        var b = button(value === "existing" ? "Use existing" : "Create new", "", function () {
          row.resolution = value;
          if (value === "new") { row.product_id = ""; row.product = null; snapshots.forEach(function (k) { delete row[k]; }); row.staging_reference = row.staging_reference || "row-" + row.row; }
          changed(row); renderEditor(row);
        });
        b.setAttribute("aria-pressed", String((row.resolution || (row.product ? "existing" : "")) === value));
        b.disabled = value === "new" && form.dataset.canCreateImportProduct !== "true"; resolution.appendChild(b);
      }); editor.appendChild(resolution);
      if (row.resolution === "new") {
        if (!row.unit && row.category) row.unit = defaultCategoryUnit(row.category);
        field(editor, row, "product_name", "Product name");
        var sku = field(editor, row, "sku", "SKU (optional)"); sku.placeholder = "Assigned on save";
        field(editor, row, "category", "Category", categoryChoices());
        field(editor, row, "unit", "Sales / stock unit", unitChoices(row.category));
        field(editor, row, "selling_price", "Selling price (TZS)");
        field(editor, row, "brand", "Brand (optional)"); field(editor, row, "model_number", "Model (optional)");
        var yesNo = [{ value: "No", label: "No" }, { value: "Yes", label: "Yes" }];
        ["serialized", "track_expiry"].forEach(function (key) {
          field(editor, row, key, key === "serialized" ? "Track serial numbers" : "Track expiry dates", yesNo);
        });
      } else renderSearch(row);
      var details = node("details"); details.open = !row.valid;
      details.appendChild(node("summary", "", "Purchase details"));
      field(details, row, "quantity", "Quantity"); field(details, row, "unit_cost", "Unit cost (TZS)");
      field(details, row, "batch_reference", "Batch reference (optional)");
      var serialized = row.product ? row.product.serialized : /^(yes|true|1)$/i.test(row.serialized || "");
      var expiry = row.product ? row.product.expiry : /^(yes|true|1)$/i.test(row.track_expiry || "");
      if (serialized || row.serial_numbers) field(details, row, "serial_numbers", "Serial numbers (one per line)");
      if (expiry || row.expiry_date) field(details, row, "expiry_date", "Expiry date");
      editor.appendChild(details);
      var save = button("Save item", "jims-btn jims-btn-primary", function () { validate(false); }); save.disabled = busy; editor.appendChild(save);
      var next = button("Save and next needing attention →", "import-link", function () { validate(false, true); }); next.disabled = busy; editor.appendChild(next);
      editor.appendChild(node("p", "import-stock-note", "ⓘ No stock changes at this stage."));
    }
    function renderSearch(row) {
      var label = node("label", "import-field", "Search for a product");
      var search = node("input"); search.type = "search"; search.placeholder = "Name, SKU, brand or model";
      label.appendChild(search); editor.appendChild(label);
      var options = node("div"); options.setAttribute("role", "group"); options.setAttribute("aria-label", "Catalog matches"); options.setAttribute("aria-live", "polite"); editor.appendChild(options);
      var candidate = null;
      var confirm = button("Confirm match", "jims-btn jims-btn-primary", function () {
        if (!candidate) return;
        var oldSku = row.sku;
        row.product = candidate; row.product_id = candidate.id; row.resolution = "existing";
        // Explicit confirmation approves replacement of an unknown supplied SKU.
        row.sku = candidate.sku; row.expected_sku = candidate.sku;
        row.expected_serialized = candidate.serialized; row.expected_expiry = candidate.expiry; row.expected_unit_id = candidate.unit_id;
        if (oldSku && oldSku !== candidate.sku) row.original_sku = oldSku;
        changed(row); validate(false);
      }); confirm.disabled = true;
      function showMatches(products) {
        candidate = null; confirm.disabled = true; options.replaceChildren();
        products.forEach(function (product) {
          var wrap = node("label", "import-choice"), radio = node("input"); radio.type = "radio"; radio.name = "import-catalog-choice"; radio.value = product.id;
          radio.addEventListener("change", function () { candidate = product; confirm.disabled = false; });
          var text = node("span"); text.append(node("strong", "", product.name), node("small", "", [product.sku, product.brand, product.model_number, "Unit: " + product.unit].filter(Boolean).join(" · ") + (product.serialized ? " · Serial tracking" : "") + (product.expiry ? " · Expiry tracking" : "")));
          wrap.append(radio, text); options.appendChild(wrap);
        });
        if (!products.length) options.appendChild(node("p", "", "Search your catalog to choose an existing product."));
      }
      showMatches(row.product ? [Object.assign({ unit_id: row.expected_unit_id }, row.product)] : row.suggestions || []);
      if (row.product) {
        var radio = options.querySelector("input"); if (radio) { radio.checked = true; candidate = Object.assign({ unit_id: row.expected_unit_id }, row.product); confirm.disabled = false; }
      }
      search.addEventListener("input", function () {
        clearTimeout(searchTimer); var query = search.value.trim(), expected = ++searchRevision;
        showMatches([]);
        if (query.length < 2) return;
        searchTimer = setTimeout(async function () {
          try {
            var response = await fetch(form.dataset.productSearchUrl + "?q=" + encodeURIComponent(query), { credentials: "same-origin" });
            if (!response.ok) throw Error(); var payload = await response.json();
            if (expected === searchRevision) { showMatches(payload.results || []); if (!(payload.results || []).length) options.replaceChildren(node("p", "", "No catalog matches. Try another name or choose Create new.")); }
          } catch (_) { if (expected === searchRevision) options.replaceChildren(node("p", "", "Product search failed. Try again.")); }
        }, 250);
      });
      editor.appendChild(confirm);
    }
    function total() {
      // Decimal arithmetic in millionths; HALF_EVEN cents matches Decimal.quantize.
      function scaled(value) { var parts = String(value).split("."); return BigInt(parts[0] || "0") * 1000000n + BigInt(((parts[1] || "") + "000000").slice(0, 6)); }
      var cents = rows.filter(function (r) { return !r.excluded; }).reduce(function (n, r) {
        var line = scaled(r.quantity) * scaled(r.unit_cost);
        var rounded = line / 10000000000n, rem = line % 10000000000n;
        if (rem > 5000000000n || (rem === 5000000000n && rounded % 2n)) rounded++;
        return n + rounded;
      }, 0n);
      return amount(String(cents / 100n) + "." + String(cents % 100n).padStart(2, "0"));
    }
    function renderFinal() {
      var c = counts(), dl = $("[data-import-final-summary]"); dl.replaceChildren();
      var definitions = new Set(rows.filter(function (r) { return !r.excluded && r.resolution === "new"; }).map(function (r) { return r.staging_reference; }));
      [["Existing product lines", c.matched], ["New products on draft save", definitions.size], ["Included purchase lines", c.all - c.excluded], ["Excluded lines", c.excluded], ["Estimated purchase total (TZS)", total()]].forEach(function (pair) { var item = node("div"); item.append(node("dt", "", pair[0]), node("dd", "", pair[1])); dl.appendChild(item); });
    }
    function apply() {
      if (busy || counts().attention || rows.every(function (r) { return r.excluded; })) return;
      var manual = Number(form.querySelector('[name="lines-TOTAL_FORMS"]').value);
      var included = rows.filter(function (r) { return !r.excluded; }).length;
      // Match the existing server submission limit, including excluded entries
      // carried for review and all Django management-form entries.
      if (manual + rows.length > 250) { message("The workspace supports at most 250 submitted rows. Reduce the source batch or workspace rows before adding these items."); return; }
      hidden.value = JSON.stringify(rows.map(compact)); hidden.dispatchEvent(new Event("change", { bubbles: true }));
      appliedText.textContent = included + " imported items added. Save the draft to preserve proposed products and purchase rows. New products and stock are created only when receiving succeeds.";
      applied.classList.remove("hidden"); close();
      var save = form.querySelector("button[value=save_review]"); if (save) save.focus();
    }
    opener.addEventListener("click", show);
    $("[data-import-close]").addEventListener("click", close);
    $("[data-import-back]").addEventListener("click", function () { if (step === 1) close(); else setStep(step - 1, true); });
    primary.addEventListener("click", function () {
      if (step === 1 && !mapping && $("[data-import-format]").value === "custom" && method === "paste") inspectColumns();
      else if (step === 1) validate(true); else if (step === 2) setStep(3, true); else apply();
    });
    $("[data-import-replace]").addEventListener("click", function () { setStep(1, true); });
    $$("[data-import-method]").forEach(function (tab) { tab.addEventListener("click", function () { if (method !== tab.dataset.importMethod) { setMethod(tab.dataset.importMethod); invalidateSource(); } }); });
    input.addEventListener("input", invalidateSource); $("[data-import-format]").addEventListener("change", invalidateSource);
    $("[data-import-choose-columns]").addEventListener("click", function () { inspectColumns(); });
    $("[data-import-has-header]").addEventListener("change", function () {
      if (mapping) { mapping.has_header = this.checked; mapping.needs_inspection = true; rows = []; active = null; renderList(); updateMapping(); }
      inspectColumns(this.checked, mapping ? mapping.columns.map(function (column) { return column.field; }) : null);
    });
    $("[data-import-reset-mapping]").addEventListener("click", function () {
      if (!mapping) return;
      revision++; busy = false; mapping.columns.forEach(function (column) { column.field = ""; }); renderMapping(mapping);
    });
    file.addEventListener("change", function () { $("[data-import-filename]").textContent = file.files.length ? file.files[0].name : "No file selected"; $("[data-import-remove-file]").classList.toggle("hidden", !file.files.length); invalidateSource(); });
    $("[data-import-remove-file]").addEventListener("click", function () { file.value = ""; file.dispatchEvent(new Event("change")); });
    $$("[data-import-filter]").forEach(function (tab) { tab.addEventListener("click", function () { filter = tab.dataset.importFilter; page = 0; renderList(); }); });
    $("[data-import-previous]").addEventListener("click", function () { page--; renderList(); });
    $("[data-import-next]").addEventListener("click", function () { page++; renderList(); });
    $("[data-import-select-page]").addEventListener("change", function (event) { filtered().slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).forEach(function (r) { r.bulk_selected = event.target.checked; }); renderList(); });
    $("[data-import-clear-selection]").addEventListener("click", function () { rows.forEach(function (r) { r.bulk_selected = false; }); renderList(); });
    $("[data-import-exclude]").addEventListener("click", function () { rows.forEach(function (r) { if (r.bulk_selected) { r.excluded = true; r.valid = true; r.bulk_selected = false; } }); revision++; renderList(); if (active !== null) renderEditor(rows[active]); });
    $("[data-import-bulk-apply]").addEventListener("click", function () {
      var category = $("[data-bulk-category]").value, unit = $("[data-bulk-unit]").value;
      if (!category && !unit) { message("Select a category or unit to apply to selected new items."); return; }
      rows.forEach(function (r) { if (r.bulk_selected && r.resolution === "new" && !r.excluded) { if (category) { r.category = category; r.unit = unit || defaultCategoryUnit(category); } else if (unit) r.unit = unit; r.valid = false; } });
      revision++; renderList(); validate(false);
    });
    $("[data-bulk-category]").addEventListener("change", function () {
      var allowed = unitChoices($("[data-bulk-category]").value).map(function (choice) { return choice.value; });
      Array.from($("[data-bulk-unit]").options).forEach(function (option) { option.disabled = !allowed.includes(option.value); });
      $("[data-bulk-unit]").value = defaultCategoryUnit($("[data-bulk-category]").value);
    });
    dialog.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return; }
      if (event.target.getAttribute("role") === "tab" && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        var tabs = Array.from(event.target.parentElement.querySelectorAll("[role=tab]"));
        var n = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (tabs.indexOf(event.target) + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        event.preventDefault(); tabs[n].click(); tabs[n].focus();
      }
      if (event.key !== "Tab") return;
      var controls = $$("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], summary").filter(function (el) { return el.getClientRects().length && el.tabIndex >= 0; });
      if (!controls.length) return;
      if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls[controls.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === controls[controls.length - 1]) { event.preventDefault(); controls[0].focus(); }
    });
    // Enter in the contextual editor never submits the outer purchase form.
    dialog.addEventListener("keypress", function (event) { if (event.key === "Enter" && event.target.tagName === "INPUT") event.preventDefault(); });
    form.querySelector("[data-clear-import]").addEventListener("click", function () { hidden.value = ""; applied.classList.add("hidden"); opener.focus(); });
    if (hidden.value) {
      try {
        rows = JSON.parse(hidden.value); sourceName = "Preserved import"; sourceFormat = "Review saved items";
        rows.forEach(function (r, i) { r.row = r.row || i + 1; r.valid = false; r.status = r.excluded ? "excluded" : "unresolved"; r.errors = ["Check this preserved item before adding it again."]; });
        $("[data-import-source-name]").textContent = sourceName; $("[data-import-source-info]").textContent = rows.length + " preserved rows";
        applied.classList.remove("hidden"); appliedText.textContent = "Imported items are preserved after the validation error. Open the import to review them.";
      } catch (_) { message("Saved import rows could not be restored. Paste or upload the source again."); }
    }
    setMethod(method); renderList(); setStep(1);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready); else ready();
}());
