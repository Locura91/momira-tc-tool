// Momira TC Rollover -- content script v6 (debug edition)
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
    "background:#111;color:#fff;font:12px/1.5 monospace;" +
    "padding:12px 16px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.5);" +
    "max-width:420px;min-width:260px;word-break:break-word;";

  function say(html) { box.innerHTML = "<b style='font-size:13px'>TC Rollover v6</b><br>" + html; }
  function attach() {
    if (document.body && !box.isConnected) document.body.appendChild(box);
  }

  say("Date: <b>" + DATE + "</b><br>Page: " + pathname.substring(0, 60));
  if (document.body) attach();
  else document.addEventListener("DOMContentLoaded", attach);

  // ── Helpers ──────────────────────────────────────────────────────────────

  function elInfo(el) {
    if (!el) return "null";
    return (el.tagName || "?") + "#" + (el.id || "-") +
           " ." + (el.className || "-").toString().split(" ")[0] +
           ' "' + (el.textContent || el.value || "").trim().substring(0, 30) + '"';
  }

  function fireClick(el) {
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    el.dispatchEvent(new MouseEvent("mouseup",   { bubbles: true, cancelable: true }));
    el.dispatchEvent(new MouseEvent("click",     { bubbles: true, cancelable: true }));
    el.click();
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
    var evts = ["focus", "input", "change", "keyup", "blur"];
    for (var i = 0; i < evts.length; i++) {
      el.dispatchEvent(new Event(evts[i], { bubbles: true }));
    }
  }

  // ── Find "Book directly without changes" button ──────────────────────────

  var BOOK_TEXTS = [
    "book directly without changes",
    "reserva directamente sin cambios",
    "reservar directamente",
    "book directly"
  ];

  function findBookButton() {
    var byId = document.querySelector("[id*='directBook']");
    if (byId) return byId;

    var candidates = [];
    var els = document.querySelectorAll("a, button, input[type='submit'], input[type='button']");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
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
      "input[name*='travelDate']",
      "input[id*='date' i]",
      "input[placeholder*='Departure' i]",
      "input[placeholder*='Start date' i]",
      "input[placeholder*='date' i]",
      "input[type='date']"
    ];
    for (var i = 0; i < selectors.length; i++) {
      var el = document.querySelector(selectors[i]);
      if (el) return el;
    }
    // Look inside dialogs / widgets
    var containers = document.querySelectorAll(
      ".ui-dialog, [role='dialog'], .ui-panel, " +
      "[class*='booking'], [class*='search-form'], [class*='travel-search']"
    );
    for (var c = 0; c < containers.length; c++) {
      var inp = containers[c].querySelector(
        "input[type='text'], input[type='date'], input:not([type='hidden'])"
      );
      if (inp) return inp;
    }
    return null;
  }

  function listAllInputs() {
    var inputs = document.querySelectorAll("input:not([type='hidden'])");
    var info = [];
    for (var i = 0; i < inputs.length && i < 10; i++) {
      var el = inputs[i];
      info.push((el.type || "text") + ":" + (el.id || el.name || el.placeholder || "?").substring(0, 25));
    }
    return inputs.length + " inputs: " + (info.join(" | ") || "none");
  }

  // ── Search button ────────────────────────────────────────────────────────

  var SEARCH_TEXTS = ["search", "buscar", "suchen", "start trip", "iniciar", "find"];

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

  // ── Non-idea page: TC navigated here from idea page ──────────────────────

  if (!isIdea) {
    say("New page (after navigation)<br>" +
        "URL: " + pathname.substring(0, 70) + "<br>" +
        "Looking for date field...");
    setTimeout(step2, 2000);
    return;
  }

  // ── Idea page: Step 1 — find & click the Book button ────────────────────

  say("Idea page. Searching for Book button...");
  var s1tries = 0;
  var s1iv = setInterval(function () {
    attach();
    var btn = findBookButton();
    if (btn) {
      clearInterval(s1iv);
      var info = elInfo(btn);
      say("CLICKING:<br><small>" + info + "</small><br>" +
          "href=" + (btn.href || "none").substring(0, 60));
      // Record URL before click to detect navigation
      var urlBefore = location.href;
      fireClick(btn);
      setTimeout(function () {
        var urlAfter = location.href;
        var navigated = urlAfter !== urlBefore;
        say("Clicked. Navigated=" + navigated + "<br>" +
            "URL now: " + location.pathname.substring(0, 70) + "<br>" +
            "Waiting for widget...");
        setTimeout(step2, 2000);
      }, 500);
    } else if (++s1tries > 100) {
      clearInterval(s1iv);
      say("Book button NOT found.<br>All buttons on page:<br><small>" + listAllButtons() + "</small>");
    }
  }, 200);

  function listAllButtons() {
    var els = document.querySelectorAll("a, button");
    var info = [];
    for (var i = 0; i < els.length && i < 8; i++) {
      var t = (els[i].textContent || "").trim().substring(0, 30);
      if (t) info.push(t);
    }
    return info.join(" | ");
  }

  // ── Step 2 — find date field ─────────────────────────────────────────────

  function step2() {
    say("Looking for date field...<br><small>" + listAllInputs() + "</small>");
    var s2tries = 0;
    var s2iv = setInterval(function () {
      attach();
      var inp = findDateInput();
      if (inp) {
        clearInterval(s2iv);
        say("Found date input:<br><small>" + elInfo(inp) + "</small><br>Setting <b>" + DATE + "</b>...");
        inp.focus();
        inp.click();
        setTimeout(function () {
          setValue(inp, DATE);
          say("Date set to <b>" + DATE + "</b>!<br>Clicking Search...");
          setTimeout(step3, 800);
        }, 400);
      } else if (++s2tries > 60) {
        clearInterval(s2iv);
        say("Date field NOT found.<br><small>" + listAllInputs() + "</small><br>" +
            "Enter <b>" + DATE + "</b> manually.");
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
        say("Clicking Search: <small>" + elInfo(btn) + "</small>");
        fireClick(btn);
        say("Search clicked! TC loading...");
        try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
      } else if (++tries > 20) {
        clearInterval(iv);
        say("Date = <b>" + DATE + "</b> set.<br>Click Search manually.");
      }
    }, 300);
  }

})();
