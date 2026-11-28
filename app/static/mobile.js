// Quick check (phone app): scan a QR label, record condition and location, keep it offline, sync later.
(function () {
  "use strict";
  var root = document.getElementById("quick");
  if (!root) return;
  var T = JSON.parse(document.getElementById("t").textContent);
  var userId = root.dataset.user;
  var items = {};
  var $ = function (id) { return document.getElementById(id); };
  var fmt = function (tpl, vals) { return tpl.replace(/\{(\w+)\}/g, function (_, k) { return vals[k]; }); };

  // --- tiny IndexedDB queue --------------------------------------------------------------
  var dbp = new Promise(function (resolve, reject) {
    var req = indexedDB.open("church-inventory", 1);
    req.onupgradeneeded = function () { req.result.createObjectStore("queue", { keyPath: "uid" }); };
    req.onsuccess = function () { resolve(req.result); };
    req.onerror = function () { reject(req.error); };
  });
  function store(mode) {
    return dbp.then(function (db) { return db.transaction("queue", mode).objectStore("queue"); });
  }
  function all() {
    return store("readonly").then(function (s) {
      return new Promise(function (resolve) {
        var r = s.getAll();
        r.onsuccess = function () {
          // Only this person's checks: on a shared phone, someone else's stay until they log in.
          resolve(r.result.filter(function (e) { return String(e.user_id) === String(userId); }));
        };
      });
    });
  }
  function put(entry) {
    return store("readwrite").then(function (s) {
      return new Promise(function (resolve) { s.put(entry).onsuccess = resolve; });
    });
  }
  function remove(uid) {
    return store("readwrite").then(function (s) {
      return new Promise(function (resolve) { s.delete(uid).onsuccess = resolve; });
    });
  }

  function say(text, kind) {
    var m = $("message");
    m.textContent = text;
    m.className = "flash " + (kind || "success");
    m.hidden = !text;
  }

  function newUid() {
    var a = new Uint8Array(12);
    crypto.getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }

  function localNow() {
    var d = new Date();
    var p = function (n) { return ("0" + n).slice(-2); };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  // --- item list (cached by the service worker for offline use) ---------------------------
  function loadItems() {
    return fetch(root.dataset.items, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) { d.items.forEach(function (i) { items[i.id] = i; }); })
      .catch(function () {});
  }

  function conditionLabel(code) {
    var opt = document.querySelector('#checkform select[name=condition] option[value="' + code + '"]');
    return opt ? opt.textContent : code;
  }

  function showItem(id) {
    var item = items[id];
    if (!item) { say(fmt(T.notfound, { id: id }), "warning"); return; }
    say("");
    var f = $("checkform");
    f.hidden = false;
    f.item_id.value = item.id;
    f.condition.value = item.condition;
    f.location.value = item.location || "";
    f.note.value = "";
    $("itemname").textContent = (item.kind === "space" ? "🏛 " : "📦 ") + item.name + "  #" + item.id;
    $("iteminfo").textContent = T.qty + ": " + item.quantity + " · " + T.location + ": " + (item.location || "—")
      + " · " + T.condition + ": " + conditionLabel(item.condition);
    $("itemlink").href = "/items/" + item.id;
    f.scrollIntoView({ behavior: "smooth" });
  }

  function idFromText(text) {
    var m = /\/items\/(\d+)/.exec(text || "") || /^\s*#?(\d+)\s*$/.exec(text || "");
    return m ? parseInt(m[1], 10) : null;
  }

  // --- camera + QR -----------------------------------------------------------------------
  var stream = null;
  var detector = ("BarcodeDetector" in window) ? new window.BarcodeDetector({ formats: ["qr_code"] }) : null;

  function stopCamera() {
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    stream = null;
    $("camera").hidden = true;
  }

  function found(text) {
    var id = idFromText(text);
    if (id === null) { say(T.noqr, "warning"); return false; }
    stopCamera();
    if (navigator.vibrate) navigator.vibrate(60);
    showItem(id);
    return true;
  }

  function tick() {
    if (!stream) return;
    var video = $("video");
    if (video.readyState < 2) { requestAnimationFrame(tick); return; }
    if (detector) {
      detector.detect(video).then(function (codes) {
        if (codes.length && found(codes[0].rawValue)) return;
        setTimeout(tick, 200);
      }).catch(function () { detector = null; setTimeout(tick, 200); });
      return;
    }
    var canvas = $("canvas");
    var w = video.videoWidth, h = video.videoHeight;
    var scale = Math.min(1, 640 / Math.max(w, h));
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(h * scale);
    var ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    var img = ctx.getImageData(0, 0, canvas.width, canvas.height);
    var code = window.jsQR ? window.jsQR(img.data, img.width, img.height, { inversionAttempts: "dontInvert" }) : null;
    if (code && found(code.data)) return;
    setTimeout(tick, 200);
  }

  $("scan").addEventListener("click", function () {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { say(T.nocamera, "warning"); return; }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then(function (s) {
        stream = s;
        var video = $("video");
        video.srcObject = s;
        $("camera").hidden = false;
        video.play();
        tick();
      })
      .catch(function () { say(T.nocamera, "warning"); });
  });
  $("stop").addEventListener("click", stopCamera);
  $("manual").addEventListener("submit", function (e) {
    e.preventDefault();
    var id = idFromText(this.id.value);
    if (id !== null) showItem(id);
  });

  // --- saving and syncing ----------------------------------------------------------------
  function render() {
    return all().then(function (entries) {
      $("queuecount").textContent = entries.length;
      $("queueempty").hidden = entries.length > 0;
      var ul = $("queue");
      ul.textContent = "";
      entries.sort(function (a, b) { return a.checked_at < b.checked_at ? -1 : 1; }).forEach(function (e) {
        var li = document.createElement("li");
        var item = items[e.item_id];
        li.textContent = e.checked_at.replace("T", " ") + " · " + (item ? item.name : "#" + e.item_id)
          + " · " + conditionLabel(e.condition) + (e.note ? " — " + e.note : "") + (e.error ? " ⚠ " + e.error : "");
        ul.appendChild(li);
      });
      return entries;
    });
  }

  var syncing = false;
  function sync() {
    if (syncing || !navigator.onLine) return Promise.resolve();
    syncing = true;
    return all().then(function (entries) {
      if (!entries.length) return;
      return fetch(root.dataset.sync, {
        method: "POST", credentials: "same-origin", redirect: "manual",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": root.dataset.csrf },
        body: JSON.stringify({ entries: entries })
      }).then(function (r) {
        if (r.status === 401 || r.type === "opaqueredirect") { say(T.login, "warning"); return; }
        if (r.status === 400 || r.status === 403) { say(r.status === 403 ? T.readonly : T.login, "warning"); return; }
        return r.json().then(function (d) {
          var done = d.saved.concat(d.duplicate);
          var jobs = done.map(remove);
          Object.keys(d.errors || {}).forEach(function (uid) {
            var e = entries.filter(function (x) { return x.uid === uid; })[0];
            if (e) { e.error = d.errors[uid]; jobs.push(put(e)); }
          });
          return Promise.all(jobs).then(function () {
            if (d.saved.length) say(fmt(T.sent, { n: d.saved.length }));
          });
        });
      });
    }).catch(function () { /* still offline: try again later */ })
      .then(function () { syncing = false; return render(); });
  }

  $("checkform").addEventListener("submit", function (e) {
    e.preventDefault();
    if (root.dataset.readonly === "1") { say(T.readonly, "warning"); return; }
    var f = this;
    var entry = {
      uid: newUid(), user_id: userId, item_id: parseInt(f.item_id.value, 10), checked_at: localNow(),
      condition: f.condition.value, location: f.location.value.trim(), note: f.note.value.trim()
    };
    put(entry).then(function () {
      var item = items[entry.item_id];
      if (item) { item.condition = entry.condition; if (entry.location) item.location = entry.location; }
      f.hidden = true;
      say(T.saved);
      window.scrollTo({ top: 0, behavior: "smooth" });
      return render();
    }).then(sync);
  });
  $("syncnow").addEventListener("click", sync);

  function net() {
    var on = navigator.onLine;
    var s = $("netstatus");
    s.textContent = on ? T.online : T.offline;
    s.className = "status " + (on ? "s-approved" : "s-pending");
    if (on) sync();
  }
  window.addEventListener("online", net);
  window.addEventListener("offline", net);

  // Opened from a label's link (/m/?item=12): show that item straight away.
  loadItems().then(render).then(function () {
    var id = idFromText(new URLSearchParams(location.search).get("item") || "");
    if (id !== null) showItem(id);
    net();
  });
})();
