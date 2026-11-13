// Momira TC Rollover -- content script v7
// Key fixes vs v6:
//  - fireClick: removed duplicate el.click() (caused double-trigger on PrimeFaces buttons,
//    second call bypassed preventDefault and followed the href to TC homepage)
//  - findBookButton: skips any <a> whose href points to /home or home?
//    (those are the wrong "Customize & Book" links that navigate to homepage)
//  - Debug box still shows full href + element info
(function () {
  "use strict";

  var STORAGE_KEY = "tcrollover_pending_date";
  var pathname = location.pathname;
  var hash = location.hash || "";
  var isIdea = pathname.indexOf("/idea/") > -1;

  var DATE = null;
  if (isIdea) {
    var m = hash.match(/tcrollover=(\d{2}\.\d{2}\.\d{4})/);
    if (m) {
      DATE = m[1];
      try { sessionStorage.setItem(STORAGE_KEY, DATE); } catch (e) {}
    } else {
      try { DATE = sessionStorage.getItem(STORAGE_KEY); } catch (e) {}
    }
  } else {
    try { DATE = sessionStorage.getItem(STORAGE_KEY); } catch (e) {}
  }

  if (!DATE) return;

  // ── Status overlay ───────────────────────────────────────────────────────
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;top:12px;right:12px;z-index:2147483647;" +
    "background:#111;color:#fff;font:11px/1.5 monospace;" +
    "padding:12px 16px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.5);" +
    "max-width:440px;min-width:260px;word-break:break-all;";

  function say(html) { box.innerHTML = "<b style='font-size:13px;font-family:sans-serif'>TC Rollover v7</b><br>" + html; }
  function attach() { if (document.body && !box.isConnected) document.body.appendChild(box); }
  say("Date: <b>" + DATE + "</b><br>" + pathname.substring(0, 70));
  if (document.body) attach();
  else document.addEventListener("DOMContentLoaded", attach);

  // ── Helpers ──────────────────────────────────────────────────────────────

  function elInfo(el) {
    if (!el) return "null";
    return el.tagName + "#" + (el.id || "-") +
           " [" + (el.className || "").toString().split(" ").slice(0, 2).join(" ") + "]" +
           " href=" + ((el.href || "").substring(0, 80) || "none") +
           '\n"' + (el.textContent || el.value || "").trim().substring(0, 50) + '"';
  }

  // Single-fire click — does NOT call el.click() to avoid double-trigger.
  // PrimeFaces <a> buttons: first dispatchEvent fires onclick (which opens the widget
  // and returns false to prevent navigation). A second el.click() would re-fire onclick
  // and bypass the preventDefault, causing the browser to follow href to homepage.
  function fireClick(el) {
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    el.dispatchEvent(new MouseEvent("mouseup",   { bubbles: true, cancelable: true }));
    el.dispatchEvent(new MouseEvent("click",     { bubbles: true, cancelable: true }));
  }

  function setValue(el, val) {
    try {
      var proto = Object.getPrototypeOf(el);
      var desc = Object.getOwnPropertyDescriptor(proto, "value") ||
                 Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
      if (desc && desc.set) desc.set.call(el, val);
      else el.value = val;
    } catch (e) { el.value = val; }
    el.value = val;
    ["focus", "input", "change", "keyup", "blur"].forEach(function (n) {
      el.dispatchEvent(new Event(n, { bubbles: true }));
    });
  }

  // Returns true if href points to TC homepage (wrong button)
  function isHomeLink(el) {
    var href = el.href || el.getAttribute("href") || "";
    return href.indexOf("/home") > -1 || href.indexOf("home?") > -1 || href === "#" && false;
  }

  // ── Find "Book directly without changes" button ──────────────────────────

  var BOOK_TEXTS = [
    "book directly without changes",
    "reserva directamente sin cambios",
    "reservar directamente",
    "book directly"
  ];

  function findBookButton() {
    // 1. ID-based — most reliable for PrimeFaces; skip if href is a home link
    var byId = document.querySelector("[id*='directBook']");
    if (byId && !isHomeLink(byId)) return byId;

    // 2. Text match on <a> and <button>, skip homepage links, sort shortest-text-first
    var candidates = [];
    var els = document.querySelectorAll("a, button, input[type='submit'], input[type='button']");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (isHomeLink(el)) continue;
      var t = (el.textContent || el.value || "").trim().toLowerCase();
      for (var j = 0; j < BOOK_TEXTS.length; j++) {
        if (t.indexOf(BOOK_TEXTS[j]) > -1) { candidates.push(el); break; }
      }
    }
    if (candidates.length > 0) {
      candidates.sort(function (a, b) {
        return (a.textContent || "").length - (b.textContent || "").length;
      });
      return candidates[0];
    }

    // 3. Last resort: include home links (maybe the href IS the booking page)
    // — log a warning so we can spot this in the debug box
    if (byId) return byId;
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var t = (el.textContent || el.value || "").trim().toLowerCase();
      for (var j = 0; j < BOOK_TEXTS.length; j++) {
        if (t.indexOf(BOOK_TEXTS[j]) > -1) return el;
      }
    }
    return null;
  }

  // ── Find departure date input ────────────────────────────────────────────

  function findDateInput() {
    var selectors = [
      "input[id*='departureDateMultidestination']",
      "input[name*='departureDateMultidestination']",
      "input[id*='departureDate']",
      "input[name*='departureDate']",
      "input[id*='startDate']",
      "input[name*='startDate']",
      "input[id*='travelDate']",
      "input[placeholder*='Departure' i]",
      "input[placeholder*='Start date' i]",
      "input[placeholder*='date' i]",
      "input[type='date']"
    ];
    for (var i = 0; i < selectors.length; i++) {
      var el = document.querySelector(selectors[i]);
      if (el) return el;
    }
    var containers = document.querySelectorAll(
      ".ui-dialog, [role='dialog'], .ui-panel, [class*='booking'], [class*='search-form']"
    );
    for (var c = 0; c < containers.length; c++) {
      var inp = containers[c].querySelector("input[type='text'], input[type='date'], input:not([type='hidden'])");
      if (inp) return inp;
    }
    return null;
  }

  function listAllInputs() {
    var inputs = document.querySelectorAll("input:not([type='hidden'])");
    var info = [];
    for (var i = 0; i < inputs.length && i < 10; i++) {
      var el = inputs[i];
      info.push((el.id || el.name || el.placeholder || el.type || "?").substring(0, 25));
    }
    return inputs.length + ": " + info.join(" | ");
  }

  // ── Find Search button ───────────────────────────────────────────────────

  var SEARCH_TEXTS = ["search", "buscar", "suchen", "start trip", "iniciar"];

  function findSearchButton() {
    var byId = document.querySelector("[id*='startTrip'], [id*='searchBtn'], [id*='searchButton']");
    if (byId && !byId.disabled) return byId;
    var els = document.querySelectorAll("button, a, input[type='submit']");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.disabled) continue;
      var t = (el.textContent || el.value || "").trim().toLowerCase();
      for (var j = 0; j < SEARCH_TEXTS.length; j++) {
        if (t === SEARCH_TEXTS[j] || t.indexOf(SEARCH_TEXTS[j]) > -1) return el;
      }
    }
    return null;
  }

  // ── Non-idea page (TC navigated here) ────────────────────────────────────

  if (!isIdea) {
    say("New page after navigation<br>Path: " + pathname.substring(0, 70) + "<br>Looking for date field...");
    setTimeout(step2, 2000);
    return;
  }

  // ── Idea page: Step 1 — find & click the Book button ────────────────────

  say("Searching for Book button...");
  var s1tries = 0;
  var s1iv = setInterval(function () {
    attach();
    var btn = findBookButton();
    if (btn) {
      clearInterval(s1iv);
      var info = elInfo(btn);
      var homeWarn = isHomeLink(btn) ? "<br><span style='color:#f90'>WARNING: href points to /home!</span>" : "";
      say("CLICKING:<br><pre style='font-size:10px;white-space:pre-wrap'>" + info + "</pre>" + homeWarn);
      fireClick(btn);
      setTimeout(function () {
        say("Clicked. URL now: " + location.pathname.substring(0, 70) +
            "<br>Waiting for widget / new page...");
        setTimeout(step2, 2500);
      }, 600);
    } else if (++s1tries > 100) {
      clearInterval(s1iv);
      var btns = [];
      document.querySelectorAll("a, button").forEach(function (el) {
        var t = (el.textContent || "").trim().substring(0, 40);
        if (t) btns.push(t);
      });
      say("Book button NOT FOUND.<br><small>Buttons: " + btns.slice(0, 8).join(" | ") + "</small>");
    }
  }, 200);

  // ── Step 2 — find date field ─────────────────────────────────────────────

  function step2() {
    say("Looking for date input...<br>" + listAllInputs());
    var s2tries = 0;
    var s2iv = setInterval(function () {
      attach();
      var inp = findDateInput();
      if (inp) {
        clearInterval(s2iv);
        say("Found: <small>" + elInfo(inp) + "</small><br>Setting <b>" + DATE + "</b>...");
        inp.focus(); inp.click();
        setTimeout(function () {
          setValue(inp, DATE);
          say("Date set to <b>" + DATE + "</b>! Clicking Search...");
          setTimeout(step3, 800);
        }, 400);
      } else if (++s2tries > 60) {
        clearInterval(s2iv);
        say("Date field NOT found.<br>" + listAllInputs() + "<br>Enter <b>" + DATE + "</b> manually.");
      }
    }, 300);
  }

  // ── Step 3 — click Search ────────────────────────────────────────────────

  function step3() {
    attach();
    var tries = 0;
    var iv = setInterval(function () {
      attach();
      var btn = findSearchButton();
      if (btn) {
        clearInterval(iv);
        fireClick(btn);
        say("Search clicked! TC loading...");
        try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
      } else if (++tries > 20) {
        clearInterval(iv);
        say("Date = <b>" + DATE + "</b>. Click Search manually.");
      }
    }, 300);
  }

})();
