// Momira TC Rollover -- content script v5
// Handles two cases:
//   A) Idea page (#tcrollover=DD.MM.YYYY in hash): clicks "Book directly without changes",
//      then waits for date field on the same page (or TC navigates — handled by case B)
//   B) Any subsequent momira.travel page: reads pending date from sessionStorage,
//      fills the date field, clicks Search (handles post-navigation booking flows)
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
    // On any other page (TC may have navigated here after the button click)
    try { DATE = sessionStorage.getItem(STORAGE_KEY); } catch (e) {}
  }

  if (!DATE) return;

  // ── Status overlay ───────────────────────────────────────────────────────
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;top:12px;right:12px;z-index:2147483647;" +
    "background:#111;color:#fff;font:13px/1.5 system-ui,sans-serif;" +
    "padding:12px 16px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.4);" +
    "max-width:380px;min-width:220px;word-break:break-word;";

  function say(html) { box.innerHTML = "TC Rollover<br>" + html; }
  function attach() {
    if (document.body && !box.isConnected) document.body.appendChild(box);
  }

  say("Target: <b>" + DATE + "</b><br>Starting...");
  if (document.body) attach();
  else document.addEventListener("DOMContentLoaded", attach);

  // ── Helpers ──────────────────────────────────────────────────────────────

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

  // ── Find departure date input (broad search) ─────────────────────────────

  function findDateInput() {
    // Ordered from most specific to broadest
    var selectors = [
      "[id*='departureDateMultidestination']",
      "[name*='departureDateMultidestination']",
      "[id*='departureDate']",
      "[name*='departureDate']",
      "[id*='startDate']",
      "[name*='startDate']",
      "[id*='travelDate']",
      "[name*='travelDate']",
      "[id*='date' i]",
      "[placeholder*='Departure' i]",
      "[placeholder*='Start date' i]",
      "[placeholder*='date' i]",
      "input[type='date']"
    ];
    for (var i = 0; i < selectors.length; i++) {
      var el = document.querySelector("input" + selectors[i]);
      if (el) return el;
    }
    // Look inside PrimeFaces / TC dialogs / panels
    var containers = document.querySelectorAll(
      ".ui-dialog, [role='dialog'], .ui-panel, .booking-widget, .search-widget, " +
      "[class*='booking'], [class*='search-form'], [class*='travel-search']"
    );
    for (var c = 0; c < containers.length; c++) {
      var inp = containers[c].querySelector("input[type='text'], input[type='date'], input:not([type='hidden'])");
      if (inp) return inp;
    }
    return null;
  }

  // ── Find Search button ───────────────────────────────────────────────────

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

  // ── List all visible inputs (debug helper) ───────────────────────────────

  function listInputs() {
    var inputs = document.querySelectorAll("input:not([type='hidden'])");
    var names = [];
    for (var i = 0; i < inputs.length && i < 8; i++) {
      var el = inputs[i];
      names.push((el.id || el.name || el.placeholder || el.type || "?").substring(0, 30));
    }
    return names.length ? names.join(", ") : "none";
  }

  // ── Main flows ───────────────────────────────────────────────────────────

  if (!isIdea) {
    // We are on a page TC navigated to after clicking the button
    // Go straight to Step 2: find the date input
    say("New page detected. Looking for date field...<br><small>URL: " + pathname.substring(0, 60) + "</small>");
    setTimeout(step2, 1500);
    return;
  }

  // Idea page: Step 1 - click the Book button
  say("Looking for Book directly without changes...");
  var s1tries = 0;
  var s1iv = setInterval(function () {
    attach();
    var btn = findBookButton();
    if (btn) {
      clearInterval(s1iv);
      var label = (btn.textContent || "").trim().substring(0, 40);
      say("Clicking: <b>" + label + "</b>");
      fireClick(btn);
      // Wait 3 seconds for widget/page to load, then look for date field
      setTimeout(function () {
        // If TC navigated away, this setTimeout fires on the OLD page and step2 will time out quickly.
        // The new page's content script instance (triggered by sessionStorage) handles it.
        say("Waiting for widget / new page...");
        setTimeout(step2, 1000);
      }, 2000);
    } else if (++s1tries > 100) {
      clearInterval(s1iv);
      say("Book button not found on this page.<br>Click it manually then enter <b>" + DATE + "</b>.");
    }
  }, 200);

  // Step 2: find date input and fill it
  function step2() {
    say("Looking for date field...<br><small>Inputs found: " + listInputs() + "</small>");
    var s2tries = 0;
    var s2iv = setInterval(function () {
      attach();
      var inp = findDateInput();
      if (inp) {
        clearInterval(s2iv);
        say("Filling date: <b>" + DATE + "</b>");
        inp.focus();
        inp.click();
        setTimeout(function () {
          setValue(inp, DATE);
          say("Date set! Clicking Search...");
          setTimeout(step3, 800);
        }, 400);
      } else if (++s2tries > 60) {
        clearInterval(s2iv);
        say("Date field not found.<br>" +
            "Inputs on page: <small>" + listInputs() + "</small><br>" +
            "Enter <b>" + DATE + "</b> manually.");
      }
    }, 300);
  }

  // Step 3: click Search
  function step3() {
    attach();
    var tries = 0;
    var iv = setInterval(function () {
      attach();
      var btn = findSearchButton();
      if (btn) {
        clearInterval(iv);
        fireClick(btn);
        say("Search clicked! TC is loading...");
        try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
      } else if (++tries > 20) {
        clearInterval(iv);
        say("Date set to <b>" + DATE + "</b>.<br>Click Search to continue.");
      }
    }, 300);
  }

})();
