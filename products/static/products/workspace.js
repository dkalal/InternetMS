(() => {
'use strict';
const form=document.querySelector('#product-workspace'); if(!form)return;
const body=document.querySelector('#product-rows'), template=document.querySelector('#product-row-template');
const rules=JSON.parse(document.querySelector('#product-unit-rules').textContent);
let dirty=false,saving=false,serial=0,selected=null;
const rows=()=>[...body.querySelectorAll('.product-entry')];
const controls=row=>[...row.querySelectorAll('[data-field]'),...row.nextElementSibling.querySelectorAll('[data-field]')];
const value=c=>c.type==='checkbox'?c.checked:c.value;
const values=row=>Object.fromEntries(controls(row).filter(c=>c.type!=='file').map(c=>[c.dataset.field,value(c)]));
function meaningful(row){return controls(row).some(c=>c.type==='file'?c.files.length:JSON.stringify(value(c))!==c.dataset.initial);}
function update(){const count=rows().filter(meaningful).length;document.querySelector('#product-count').textContent=`${count} products entered · ${dirty?'Unsaved':'Ready'}`;document.querySelector('#create-products').textContent=`Create ${count} product${count===1?'':'s'}`;rows().forEach((r,i)=>{r.querySelector('.row-number').textContent=i+1;controls(r).forEach(c=>c.setAttribute('aria-label',`Row ${i+1}: ${c.dataset.field.replaceAll('_',' ')}`));});}
function add(){if(rows().length>=200)throw Error('Maximum 200 rows.');const fragment=template.content.cloneNode(true),row=fragment.querySelector('.product-entry');row.dataset.id='r'+(++serial);
const ids = new Map();
fragment.querySelectorAll('[id]').forEach(element => {
  const old = element.id;
  const next = element.dataset.field ? `${row.dataset.id}-${element.dataset.field}` : `${row.dataset.id}-${old}`;
  ids.set(old, next);
  element.id = next;
});
fragment.querySelectorAll('label[for],[aria-describedby],[aria-labelledby]').forEach(element => {
  for (const attr of ['for','aria-describedby','aria-labelledby']) {
    if (element.hasAttribute(attr)) element.setAttribute(attr, element.getAttribute(attr).split(' ').map(id => ids.get(id) || id).join(' '));
  }
});
body.append(fragment);controls(row).forEach(c=>{c.id=`${row.dataset.id}-${c.dataset.field}`;c.name=c.id;c.setAttribute('aria-label',`Row ${serial}: ${c.dataset.field.replaceAll('_',' ')}`);c.dataset.initial=JSON.stringify(value(c));const label=c.parentElement.querySelector('label');if(label)label.htmlFor=c.id;});row.querySelectorAll('[data-col]').forEach(c=>{c.dataset.label=document.querySelector(`thead [data-col=${c.dataset.col}]`).textContent;const toggle=document.querySelector(`[data-column=${c.dataset.col}]`);c.hidden=toggle&&!toggle.checked;});update();return row;}
function release(row){const image=row.querySelector('img');if(image?.dataset.url)URL.revokeObjectURL(image.dataset.url);}
function applyUnits(row){const fields=controls(row),category=fields.find(c=>c.dataset.field==='catalog_category'),unit=fields.find(c=>c.dataset.field==='sales_unit'),rule=rules[category.value];[...unit.options].forEach(o=>o.disabled=!!(o.value&&rule&&!rule.allowed.includes(Number(o.value))));if(!unit.value&&rule?.default)unit.value=rule.default;const problem=unit.selectedOptions[0]?.dataset.invalid?'Unknown or ambiguous sales unit.':unit.selectedOptions[0]?.disabled?'Select a unit allowed by this category.':'';unit.setCustomValidity(problem);unit.parentElement.querySelector('.cell-error').textContent=problem;}
for(let i=0;i<5;i++)add();
document.querySelector('#add-product-row').onclick=()=>{try{add();}catch(e){alert(e.message);}};
body.addEventListener('focusin',e=>{if(e.target.dataset.field && e.target.closest('.product-entry'))selected=e.target;});
body.addEventListener('input',e=>{dirty=true;update();});
body.addEventListener('change',e=>{const row=e.target.closest('.product-entry');if(!row)return;dirty=true;if(e.target.dataset.field==='catalog_category')applyUnits(row);if(e.target.type==='file'){release(row);const image=row.querySelector('img');image.hidden=!e.target.files.length;if(!image.hidden){image.dataset.url=URL.createObjectURL(e.target.files[0]);image.src=image.dataset.url;}}update();});
body.addEventListener('click',e=>{const row=e.target.closest('.product-entry');if(!row)return;if(e.target.closest('[data-remove-row]')){release(row);row.nextElementSibling.remove();row.remove();dirty=true;update();}if(e.target.closest('[data-details]')){const panel=row.nextElementSibling;panel.hidden=!panel.hidden;e.target.setAttribute('aria-expanded',String(!panel.hidden));}if(e.target.closest('[data-remove-image]')){row.querySelector('input[type=file]').value='';release(row);row.querySelector('img').hidden=true;dirty=true;update();}});
body.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.matches('input:not([type=file]):not([type=checkbox])')){e.preventDefault();const inputs=controlsInBody().filter(c=>!c.closest('[hidden]')&&c.type!=='file'&&c.type!=='checkbox');inputs[inputs.indexOf(e.target)+1]?.focus();}if(e.key==='Escape'){e.target.blur();const panel=e.target.closest('.product-detail-row');if(panel){panel.hidden=true;panel.previousElementSibling.querySelector('[data-details]').focus();}}});
document.querySelector('#product-columns').onchange=e=>{const name=e.target.dataset.column;document.querySelectorAll(`[data-col=${name}]`).forEach(c=>c.hidden=!e.target.checked);};
// TSV with escaped quotes and embedded newlines; never interpret formulas.
function parse(text){const result=[];let row=[],cell='',quoted=false;for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){cell+='"';i++;}else if(quoted||!cell)quoted=!quoted;else cell+=c;}else if(!quoted&&(c==='\t'||c==='\r'||c==='\n')){row.push(cell);cell='';if(c!=='\t'){result.push(row);row=[];if(c==='\r'&&text[i+1]==='\n')i++;}}else cell+=c;}if(quoted)throw Error('Unclosed quoted cell.');if(cell||row.length){row.push(cell);result.push(row);}return result;}
const normalize=s=>s.trim().toLocaleLowerCase();
function paste(text, target) {
  const data = parse(text);
  if (!data.length) return false;
  let names = [...document.querySelectorAll('thead [data-col]')]
    .filter(cell => !cell.hidden).map(cell => cell.dataset.col);
  const aliases = {
    'name': 'name', 'name / brand': 'name', 'name/brand': 'name', 'model': 'model_number', 'model_number': 'model_number',
    'sku': 'sku', 'category': 'catalog_category', 'sales unit': 'sales_unit',
    'buying price': 'buying_price', 'buying price (tzs)': 'buying_price',
    'standard price': 'selling_price', 'standard price (tzs)': 'selling_price',
    'technician price': 'technician_price', 'technician price (tzs)': 'technician_price',
    'description': 'description'
  };
  const header = data[0].map(cell => aliases[normalize(cell)]);
  if (header.every(Boolean) && new Set(header).size === header.length) {
    names = header;
    data.shift();
  } else if (target) {
    names = names.slice(names.indexOf(target.dataset.field));
  } else {
    throw Error('Include supported column headers or select a starting cell first.');
  }
  if (data.some(row => row.length > names.length)) {
    throw Error('Pasted columns exceed the available columns.');
  }
  const start = target ? rows().indexOf(target.closest('.product-entry')) : 0;
  if (start < 0 || start + data.length > 200) throw Error('Paste exceeds 200 rows.');
  const changes = [];
  data.forEach((row, i) => row.forEach((text, j) => {
    const name = names[j];
    if (name === 'image') throw Error('Pictures require an upload, not pasted text.');
    const existing = rows()[start + i];
    const control = existing ? controls(existing).find(c => c.dataset.field === name)
      : template.content.querySelector(`[data-field=${name}]`);
    let nextValue = text, error = '';
    if (control?.tagName === 'SELECT' && text) {
      const matches = [...control.options].filter(option => !option.dataset.invalid && normalize(option.text) === normalize(text));
      if (matches.length === 1) nextValue = matches[0].value;
      else {
        nextValue = 'unrecognized:' + text;
        error = `Unknown or ambiguous choice: ${text}`;
      }
    }
    changes.push({index: start + i, name, value: nextValue, text, error, previous: control?.value || ''});
  }));
  const preview = changes.slice(0, 12).map(c => `${c.name}: ${c.previous} -> ${c.text}${c.error ? ' (needs attention)' : ''}`).join('\n');
  if (!confirm(`Paste ${data.length} rows (${changes.length} cells)?\n${preview}${changes.length > 12 ? '\n...' : ''}`)) return false;
  while (rows().length < start + data.length) add();
  changes.forEach(change => {
    const control = controls(rows()[change.index]).find(c => c.dataset.field === change.name);
    if (!control) return;
    if (change.error) {
      const option = new Option(change.text + ' (unrecognized)', change.value);
      option.dataset.invalid = 'true';
      control.add(option);
      control.setAttribute('aria-invalid', 'true');
    }
    control.value = change.value;
    control.closest('td,.detail-field').querySelector('.cell-error').textContent = change.error;
  });
  rows().forEach(applyUnits);
  dirty = true;
  update();
  return true;
}
body.addEventListener('paste',e=>{const text=e.clipboardData.getData('text/plain');if(!text.includes('\t')&&!/[\r\n]/.test(text))return;if(!e.target.dataset.field||e.target.tagName==='TEXTAREA'&&!text.includes('\t'))return;e.preventDefault();try{paste(text,e.target);}catch(err){alert(err.message);}});
const pasteDialog = document.querySelector('#product-paste-dialog');
document.querySelector('#paste-product-rows').onclick = () => {
  document.querySelector('#product-paste-error').textContent = '';
  pasteDialog.showModal();
  document.querySelector('#product-paste-text').focus();
};
document.querySelector('#apply-product-paste').onclick = () => {
  try {
    if (paste(document.querySelector('#product-paste-text').value, selected)) pasteDialog.close();
  } catch (error) {
    document.querySelector('#product-paste-error').textContent = error.message;
  }
};
form.onsubmit=async e=>{e.preventDefault();if(saving)return;const active=rows().filter(meaningful),summary=document.querySelector('#batch-errors');summary.textContent='';document.querySelectorAll('.cell-error,.details-errors').forEach(c=>c.textContent='');controlsInBody().forEach(c=>c.removeAttribute('aria-invalid'));if(!active.length){summary.textContent='Enter at least one product.';return;}const payload=new FormData();payload.append('csrfmiddlewaretoken',form.querySelector('[name=csrfmiddlewaretoken]').value);payload.append('batch_token',form.querySelector('[name=batch_token]').value);payload.append('rows',JSON.stringify(active.map(row=>({id:row.dataset.id,values:values(row)}))));active.forEach(row=>{const file=row.querySelector('input[type=file]')?.files[0];if(file)payload.append(row.dataset.id,file);});saving=true;document.querySelector('#create-products').disabled=true;try{const response=await fetch(location.href,{method:'POST',body:payload});const result=await response.json();if(response.ok){dirty=false;location.assign(result.redirect);return;}summary.textContent=result.message||`${Object.keys(result.errors||{}).length} rows need attention. No products were saved.`;let first=null;Object.entries(result.errors||{}).forEach(([id,fields])=>{const row=rows().find(r=>r.dataset.id===id);Object.entries(fields).forEach(([name,errors])=>{const c=controls(row).find(c=>c.dataset.field===name);if(!c){summary.textContent+=' '+errors.map(e=>e.message).join(' ');return;}c.setAttribute('aria-invalid','true');c.closest('td,.detail-field').querySelector('.cell-error').textContent=errors.map(e=>e.message).join(' ');if(c.closest('details'))c.closest('details').open=true;if(c.closest('.product-detail-row')){row.nextElementSibling.hidden=false;row.querySelector('[data-details]').setAttribute('aria-expanded','true');row.querySelector('.details-errors').textContent='Needs attention';}const cell=c.closest('[data-col]');if(cell){const toggle=document.querySelector(`[data-column=${cell.dataset.col}]`);if(toggle){toggle.checked=true;toggle.dispatchEvent(new Event('change',{bubbles:true}));}}first||=c;});});(first||summary).focus();}catch(error){summary.textContent='Could not confirm saving. Your entries and pictures are retained. Retry this submission safely.';}finally{saving=false;document.querySelector('#create-products').disabled=false;}};
let customFieldRow = null;
body.addEventListener('click', event => {
  if (!event.target.closest('[data-grid-custom-field-open]')) return;
  customFieldRow = event.target.closest('.product-detail-row');
  document.querySelector('#grid-custom-field-opener').click();
});
document.addEventListener('custom-field:grid-created', event => {
  const {html, fieldName, definition} = event.detail;
  if (!definition?.is_active || !definition?.show_on_create || definition.target_model !== 'product') return;
  function insert(panel, row) {
    if (panel.querySelector(`[data-field="${CSS.escape(fieldName)}"]`)) return;
    const field = document.createElement('div');
    field.className = 'detail-field';
    field.dataset.detail = fieldName;
    field.innerHTML = html;
    const input = field.querySelector('input,select,textarea');
    if (!input) return;
    input.dataset.field = fieldName;
    input.dataset.initial = JSON.stringify(value(input));
    const prefix = row ? row.dataset.id : 'newrow';
    field.querySelectorAll('[id]').forEach(element => {
      const old = element.id;
      const next = prefix + '-' + old;
      field.querySelectorAll('label').forEach(label => {if (label.htmlFor === old) label.htmlFor = next;});
      for (const attr of ['aria-describedby', 'aria-labelledby']) {
        field.querySelectorAll('[' + attr + ']').forEach(control => {
          control.setAttribute(attr, control.getAttribute(attr).split(' ').map(id => id === old ? next : id).join(' '));
        });
      }
      element.id = next;
    });
    input.name = prefix + '-' + fieldName;
    const errors = document.createElement('span');
    errors.className = 'cell-error';
    errors.setAttribute('role', 'alert');
    field.append(errors);
    panel.append(field);
    if (input.required) {
      const button = row ? row.querySelector('[data-details]') : template.content.querySelector('[data-details]');
      if (!button.textContent.includes('*')) button.textContent += ' *';
    }
  }
  insert(template.content.querySelector('[data-grid-custom-fields]'), null);
  rows().forEach(row => insert(row.nextElementSibling.querySelector('[data-grid-custom-fields]'), row));
  if (customFieldRow?.isConnected) {
    customFieldRow.querySelector('[data-additional-information]').open = true;
    customFieldRow.querySelector(`[data-field="${CSS.escape(fieldName)}"]`)?.focus();
  }
  dirty = rows().some(meaningful);
  update();
});
function controlsInBody(){return rows().flatMap(controls);}
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
window.addEventListener('pagehide',()=>rows().forEach(release));
})();
