/* Live feedback is advisory: final writes always revalidate on the server. */
(function (global) {
  'use strict';
  function parse(value) {
    return String(value || '').split(/[,\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+/).map(function (s) { return s.trim().toUpperCase(); }).filter(Boolean);
  }
  function duplicates(entries) {
    var seen = new Map(), errors = new Map();
    entries.forEach(function (entry) {
      parse(entry.value).forEach(function (serial) {
        if (!seen.has(serial)) seen.set(serial, []);
        seen.get(serial).push(entry);
      });
    });
    seen.forEach(function (owners, serial) {
      if (owners.length < 2) return;
      var labels = Array.from(new Set(owners.map(function (e) { return e.label; }))).sort(function (a, b) {
        return a.localeCompare(b, 'en', {numeric: true});
      }).join(', ');
      Array.from(new Map(owners.map(function (owner) { return [owner.key, owner]; })).values()).forEach(function (owner) {
        if (!errors.has(owner.key)) errors.set(owner.key, []);
        errors.get(owner.key).push(serial + ' is repeated in ' + labels + '.');
      });
    });
    return errors;
  }
  function manualEntries(form) {
    var labels = new Map();
    Array.from(form.querySelectorAll('[data-purchase-line]')).filter(function (row) {
      var deleted = row.querySelector('[name$="-DELETE"]');
      return !(deleted && deleted.checked);
    }).forEach(function (row, index) {
      var product = row.querySelector('select[name$="-product"]');
      if (product) labels.set(product.name.replace(/-product$/, ''), 'Line ' + (index + 1));
    });
    return Array.from(form.querySelectorAll('textarea[name="serial_numbers"], textarea[name$="-serial_numbers"]')).filter(function (control) {
      if (control.disabled) return false;
      var prefix = control.name.replace(/-serial_numbers$/, '');
      var deleted = Array.from(form.elements).find(function (el) { return el.name === prefix + '-DELETE'; });
      return !(deleted && deleted.checked);
    }).map(function (control) {
      return { key: control.name, label: labels.get(control.name.replace(/-serial_numbers$/, '')) || 'Serial numbers',
        value: control.value, control: control };
    });
  }
  function importEntries(rows, root) {
    return rows.map(function (row, index) {
      return { key: 'import-' + index, label: 'Import row ' + (row.row || index + 1), value: String(row.serial_numbers || '').replace(/\|/g, ','),
        control: root && root.querySelector('[data-serial-import-index="' + index + '"]'), excluded: row.excluded };
    }).filter(function (e) { return !e.excluded; });
  }
  function Controller(root, options) {
    this.root = root; this.options = options || {}; this.form = options.form || root;
    this.revision = 0; this.remote = new Set(); this.remoteReason = 'exists'; this.verified = null;
    this.timer = null; this.request = null; this.errors = new Map();
    this.status = document.createElement('div'); this.status.className = 'rounded-lg border border-slate-200 p-3 text-sm';
    this.status.setAttribute('role', 'status'); this.status.setAttribute('aria-live', 'polite');
    this.status.setAttribute('data-live-serial-status', ''); this.status.hidden = true;
    this.status.style.gridColumn = '1 / -1';
    (options.review ? root.querySelector('.purchase-import-shell') || root : root).prepend(this.status);
    var self = this;
    root.addEventListener('input', function () { self.schedule(); });
    root.addEventListener('change', function () { self.schedule(); });
    root.addEventListener('jims:serials-changed', function () { self.schedule(); });
    if (!options.review) {
      this.form.addEventListener('invalid', function (event) {
        if (event.target.dataset.liveSerialError) self.reveal(event.target);
      }, true);
      this.form.addEventListener('submit', function (event) {
        if (self.resubmitting) return;
        event.preventDefault(); event.stopImmediatePropagation();
        var submitter = event.submitter;
        self.validate().then(function (valid) {
          if (!valid) { self.focusError(); return; }
          self.resubmitting = true;
          try { self.form.requestSubmit(submitter || undefined); } finally { self.resubmitting = false; }
        });
      }, true);
    }
    // Rows can be added/removed or moved into the purchase tracking drawer.
    this.observer = new MutationObserver(function (records) {
      if (records.some(function (r) { return Array.from(r.addedNodes).concat(Array.from(r.removedNodes)).some(function (n) {
        return n.nodeType === 1 && (n.matches('textarea, [data-purchase-line]') || n.querySelector('textarea, [data-purchase-line]'));
      }); })) self.schedule();
    });
    this.observer.observe(root, {childList: true, subtree: true});
    this.schedule();
  }
  Controller.prototype.entries = function () {
    if (this.options.entries) return this.options.entries();
    var entries = manualEntries(this.form), applied = this.form.querySelector('[data-import-rows]');
    if (applied && applied.value) {
      try { entries = entries.concat(importEntries(JSON.parse(applied.value))); } catch (_) { /* Server reports invalid import JSON. */ }
    }
    return entries;
  };
  Controller.prototype.snapshot = function (entries) {
    var data = new FormData(this.form), serials = Array.from(new Set(entries.flatMap(function (e) { return parse(e.value); })));
    return { serials: serials, mode: this.form.dataset.serialMode || 'purchase', product: data.get('product') || '',
      direction: data.get('direction') || '' };
  };
  Controller.prototype.reveal = function (control) {
    control.dispatchEvent(new CustomEvent('jims:serial-focus', { bubbles: true }));
  };
  Controller.prototype.focusError = function () {
    var entry = this.entries().find(function (e) { return this.errors.has(e.key) && e.control; }, this);
    if (entry) { this.reveal(entry.control); entry.control.focus(); }
    else { this.status.tabIndex = -1; this.status.focus(); this.status.scrollIntoView({block: 'center'}); }
  };
  Controller.prototype.render = function (entries, notice) {
    var errors = duplicates(entries), self = this;
    entries.forEach(function (entry) {
      var conflicts = parse(entry.value).filter(function (serial) { return self.remote.has(serial); });
      if (conflicts.length) errors.set(entry.key, (errors.get(entry.key) || []).concat(conflicts.map(function (serial) {
        return serial + (self.remoteReason === 'unavailable' ? ' is not available for this product.' : ' already exists in this organization.');
      })));
      if (!entry.control) return;
      var control = entry.control, message = (errors.get(entry.key) || []).slice(0, 5).join(' ');
      if (control.dataset.serialWasInvalid === undefined) control.dataset.serialWasInvalid = String(control.getAttribute('aria-invalid') === 'true');
      // The import editor uses buttons, and may contain unapplied changes when
      // closed. It must not make the outer purchase form natively invalid.
      control.setCustomValidity(this.options.review ? '' : message);
      control.dataset.liveSerialError = message;
      if (message) control.setAttribute('aria-invalid', 'true');
      else if (control.dataset.serialWasInvalid !== 'true') control.removeAttribute('aria-invalid');
      var hint = control.nextElementSibling;
      if (!hint || !hint.hasAttribute('data-live-serial-error')) {
        hint = document.createElement('p'); hint.setAttribute('data-live-serial-error', '');
        hint.className = 'mt-1 text-sm text-rose-700'; hint.id = 'serial-feedback-' + (++Controller.ids);
        hint.setAttribute('aria-live', 'polite'); control.after(hint);
        control.setAttribute('aria-describedby', ((control.getAttribute('aria-describedby') || '') + ' ' + hint.id).trim());
      }
      hint.textContent = message; hint.hidden = !message;
    }, this);
    this.errors = errors;
    var messages = [];
    errors.forEach(function (items, key) {
      var owner = entries.find(function (e) { return e.key === key; });
      messages.push((owner ? owner.label + ': ' : '') + items.slice(0, 3).join(' '));
    });
    this.status.textContent = messages.slice(0, 8).join(' ') || notice || '';
    this.status.hidden = !this.status.textContent;
    if (this.options.onResult) this.options.onResult(errors.size === 0);
    return errors.size === 0;
  };
  Controller.ids = 0;
  Controller.prototype.schedule = function () {
    clearTimeout(this.timer); this.revision++;
    if (this.request) this.request.abort();
    var entries = this.entries(), snapshot = this.snapshot(entries);
    if (JSON.stringify(snapshot) !== this.verified) { this.remote = new Set(); this.verified = null; }
    this.render(entries, snapshot.serials.length ? 'Checking serial numbers...' : '');
    var self = this;
    this.timer = setTimeout(function () { self.validate(); }, 350);
  };
  Controller.prototype.validate = async function () {
    clearTimeout(this.timer);
    var revision = ++this.revision, entries = this.entries(), snapshot = this.snapshot(entries), signature = JSON.stringify(snapshot);
    if (this.request) this.request.abort();
    if (!snapshot.serials.length) { this.remote = new Set(); return this.render(entries); }
    if (snapshot.serials.length > 1000 || snapshot.serials.some(function (s) { return s.length > 160; })) {
      this.remote = new Set(); this.render(entries, 'Too many serials or a serial exceeds 160 characters. Check at most 1,000 serials at a time.');
      this.errors.set('limit', ['Serial check limit exceeded.']); return false;
    }
    if (signature === this.verified) return this.render(entries);
    if (snapshot.mode === 'adjustment' && (!snapshot.product || !snapshot.direction)) return this.render(entries, 'Choose a product and adjustment direction to check availability.');
    this.request = new AbortController();
    var request = this.request, timedOut = false;
    var timeout = setTimeout(function () { timedOut = true; request.abort(); }, 8000);
    var data = new FormData(); data.set('mode', snapshot.mode); data.set('serials', JSON.stringify(snapshot.serials));
    data.set('product', snapshot.product); data.set('direction', snapshot.direction);
    try {
      var response = await fetch(this.form.dataset.serialValidationUrl, {method: 'POST', credentials: 'same-origin',
        headers: {'X-CSRFToken': this.form.querySelector('[name=csrfmiddlewaretoken]').value}, body: data, signal: this.request.signal});
      if (!response.ok) throw Error('Serial check failed');
      var payload = await response.json();
      if (revision !== this.revision) return false;
      this.remote = new Set(payload.conflicts || []); this.remoteReason = payload.reason; this.verified = signature;
      return this.render(this.entries());
    } catch (error) {
      if (revision !== this.revision || (error.name === 'AbortError' && !timedOut)) return false;
      this.remote = new Set();
      // A network failure is not a claim that serials are valid. Normal server
      // validation is still available and remains the final mutation boundary.
      return this.render(this.entries(), 'Live serial check unavailable. Serials will be checked again before stock is changed.');
    } finally {
      clearTimeout(timeout);
    }
  };
  var api = {parse: parse, duplicates: duplicates, manualEntries: manualEntries, importEntries: importEntries, Controller: Controller};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else {
    global.JimsSerialValidation = api;
    document.addEventListener('DOMContentLoaded', function () {
      document.querySelectorAll('form[data-serial-validation-url]').forEach(function (form) { form.serialValidator = new Controller(form, {}); });
      document.querySelectorAll('[data-serial-selection-form]').forEach(function (form) {
        var status = document.createElement('p'); status.setAttribute('aria-live', 'polite'); status.setAttribute('role', 'status');
        status.className = 'text-sm text-rose-700'; form.prepend(status);
        function sync() {
          var controls = Array.from(form.querySelectorAll('[data-serial-selection]'));
          var selected = controls.filter(function (el) { return el.checked; });
          var seen = new Set(), repeated = new Set();
          selected.forEach(function (el) { if (seen.has(el.value)) repeated.add(el.value); seen.add(el.value); });
          controls.forEach(function (el) {
            var conflict = el.checked && repeated.has(el.value);
            el.setCustomValidity(conflict ? 'This serial is selected on another invoice line.' : '');
            if (conflict) el.setAttribute('aria-invalid', 'true'); else el.removeAttribute('aria-invalid');
          });
          status.textContent = repeated.size ? 'A serial is selected on more than one invoice line. Keep it on only one line.' : '';
        }
        form.addEventListener('change', sync); sync();
      });
    });
  }
}(typeof window === 'undefined' ? globalThis : window));
