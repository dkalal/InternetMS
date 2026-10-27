(() => {
  'use strict';
  const initialized = new WeakSet();
  function decimal(value) {
    const match = /^(-?)(\d*)(?:\.(\d*))?$/.exec(value);
    if (!match || (!match[2] && !match[3])) return null;
    const fraction = match[3] || '';
    return { units: BigInt((match[1] || '') + (match[2] || '0') + fraction), scale: fraction.length };
  }
  function scaled(number, scale) { return number.units * 10n ** BigInt(scale - number.scale); }
  function move(input, direction) {
    if (input.disabled || input.readOnly) return;
    const current = decimal(input.value || '0');
    if (!current) return;
    const minimum = decimal(input.min), maximum = decimal(input.max);
    const scale = Math.max(current.scale, minimum?.scale || 0, maximum?.scale || 0);
    let result = scaled(current, scale) + BigInt(direction) * 10n ** BigInt(scale);
    if (minimum && result < scaled(minimum, scale)) result = scaled(minimum, scale);
    if (maximum && result > scaled(maximum, scale)) result = scaled(maximum, scale);
    const negative = result < 0n;
    const digits = (negative ? -result : result).toString().padStart(scale + 1, '0');
    input.value = (negative ? '-' : '') + (scale ? digits.slice(0, -scale) + '.' + digits.slice(-scale) : digits);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }
  function enhance(input) {
    if (initialized.has(input)) return;
    initialized.add(input);
    input.step = 'any';
    let wrapper = input.parentElement;
    if (!wrapper.classList.contains('jims-money-input')) {
      wrapper = document.createElement('span');
      wrapper.className = 'jims-money-input';
      input.before(wrapper);
      wrapper.append(input);
    }
    let buttons = wrapper.querySelector('.jims-money-buttons');
    if (!buttons) {
      buttons = document.createElement('span');
      buttons.className = 'jims-money-buttons';
      [1, -1].forEach(direction => {
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.direction = direction;
        button.textContent = direction > 0 ? '\u25b4' : '\u25be';
        button.tabIndex = -1;
        buttons.append(button);
      });
      wrapper.append(buttons);
    }
    buttons.querySelectorAll('button').forEach(button => {
      const direction = Number(button.dataset.direction);
      const label = input.getAttribute('aria-label') || document.querySelector(`label[for="${CSS.escape(input.id)}"]`)?.textContent.trim() || 'price';
      button.setAttribute('aria-label', `${direction > 0 ? 'Increase' : 'Decrease'} ${label} by 1`);
      button.addEventListener('click', () => { move(input, direction); input.focus(); });
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
        event.preventDefault();
        move(input, event.key === 'ArrowUp' ? 1 : -1);
      }
    });
  }
  function scan(root) {
    if (root.matches?.('input[data-money-step]')) enhance(root);
    root.querySelectorAll?.('input[data-money-step]').forEach(enhance);
  }
  document.addEventListener('DOMContentLoaded', () => {
    scan(document);
    new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
      if (node.nodeType === 1) scan(node);
    }))).observe(document.body, { childList: true, subtree: true });
  });
})();
