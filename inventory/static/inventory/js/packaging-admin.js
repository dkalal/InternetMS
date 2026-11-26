"use strict";
document.addEventListener("DOMContentLoaded", function () {
  const transports = {
    counted: {pattern: /^counted-/, name: "counted_payload"},
    cart: {pattern: /^container_[0-9]+$/, name: "container_payload"},
    invoice: {pattern: /^line_[0-9]+_container_[0-9]+$/, name: "selection_payload"}
  };
  document.querySelectorAll("[data-packaging-transport]").forEach(function (target) {
    const spec = transports[target.dataset.packagingTransport];
    if (!spec) return;
    target.addEventListener("formdata", function (event) {
      const fields = {};
      Array.from(event.formData.keys()).forEach(function (name) {
        if (!spec.pattern.test(name)) return;
        fields[name] = String(event.formData.get(name));
        event.formData.delete(name);
      });
      if (Object.keys(fields).length) event.formData.set(spec.name, JSON.stringify(fields));
    });
  });
  const form = document.querySelector("[data-counted-containers]");
  if (!form) return;
  const add = form.querySelector("[data-counted-add]");
  if (!add) return;
  add.addEventListener("click", function () {
    const total = form.querySelector('[name="counted-TOTAL_FORMS"]');
    const index = Number(total.value);
    if (!Number.isInteger(index) || index < 0 || index >= 2000) return;
    const holder = document.createElement("div");
    holder.innerHTML = form.querySelector("[data-counted-empty]").innerHTML.replace(/__prefix__/g, String(index));
    form.querySelector("[data-counted-rows]").appendChild(holder.firstElementChild);
    total.value = String(index + 1);
  });
});
