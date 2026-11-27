// Small progressive enhancements. Everything works without JavaScript too.
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });
  document.querySelectorAll("button.print").forEach(function (b) {
    b.addEventListener("click", function () { window.print(); });
  });
  document.querySelectorAll("input.selectall").forEach(function (box) {
    box.addEventListener("change", function () {
      box.closest("form").querySelectorAll("input[name=id]").forEach(function (c) { c.checked = box.checked; });
    });
  });
  document.querySelectorAll("input.copy").forEach(function (input) {
    input.addEventListener("focus", function () { input.select(); });
  });

  var form = document.getElementById("resform");
  var box = document.getElementById("availability");
  if (form && box) {
    var fmt = function (tpl, vals) {
      return tpl.replace(/\{(\w+)\}/g, function (_, k) { return vals[k]; });
    };
    var check = function () {
      var params = new URLSearchParams({
        item_id: form.item_id.value, quantity: form.quantity.value,
        start_at: form.start_at.value, end_at: form.end_at.value, exclude: form.dataset.exclude || ""
      });
      if (!form.item_id.value || !form.start_at.value || !form.end_at.value) { box.textContent = ""; return; }
      fetch(form.dataset.availability + "?" + params.toString(), { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          box.textContent = "";
          if (!d.ok) return;
          box.className = "availability " + (d.clash ? "bad" : "good");
          var p = document.createElement("p");
          p.textContent = (d.clash ? "⚠ " : "✓ ") + fmt(d.clash ? box.dataset.tClash : box.dataset.tFree,
            { a: d.available, t: d.total });
          box.appendChild(p);
          if (d.overlaps.length) {
            var h = document.createElement("p");
            h.textContent = box.dataset.tOverlap;
            box.appendChild(h);
            var ul = document.createElement("ul");
            d.overlaps.forEach(function (o) {
              var li = document.createElement("li");
              li.textContent = o.who + " · ×" + o.quantity + " · " + o.start + " – " + o.end + " · " + o.status;
              ul.appendChild(li);
            });
            box.appendChild(ul);
          }
        })
        .catch(function () {});
    };
    ["item_id", "quantity", "start_at", "end_at"].forEach(function (n) {
      form[n].addEventListener("change", check);
    });
    form.start_at.addEventListener("change", function () {
      if (form.start_at.value && !form.end_at.value) {
        var d = new Date(form.start_at.value);
        d.setHours(d.getHours() + 2);
        var pad = function (x) { return String(x).padStart(2, "0"); };
        form.end_at.value = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
          "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
      }
    });
    check();
  }
});
