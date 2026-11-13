// Momira TC Rollover — content script v2
// Runs on every momira.travel page.
//
// FLOW on idea page:
//   1. Detect #tcrollover=DD.MM.YYYY in URL hash
//   2. Wait for "Book directly without changes" button → click it
//   3. Wait for the booking widget / date input to appear
//   4. Fill the departure date
//   5. Click the Search button
//
// FLOW on travel-map page:
//   Restore saved date from sessionStorage and fill the date field.
(function () {
  "use strict";

  var STORAGE_KEY = "tcrollover_pending_date";
  var pathname = location.pathname;
  var hash     = location.hash || "";

  var isIdea = /\/idea\//.test(pathname);
  var isMap  = /travel-map/.test(pathname);

  // ── Detect date ──────────────────────────────────────────────────────────
  var DATE = null;

  if (isIdea) {
    var m = hash.match(/tcrollover=([0-9]{2}\.[0-9]{2}\.[0-9]{4})/);
    if (!m) return;
    DATE = m[1];
    try { sessionStorage.setItem(STORAGE_KEY, DATE); } catch (e) {}
  } else if (isMap) {
    try { DATE = sessionStorage.getItem(STORAGE_KEY); } catch (e) {}
    if (!DATE) return;
  } else {
    return;
  }

  // ── Status overlay ───────────────────────────────────────────────────────
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;top:12px;right:12px;z-index:2147483647;" +
    "background:#111;color:#fff;font:13px/1.5 system-ui,Segoe UI,sans-serif;" +
    "padding:12px 16px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.4);" +
    "max-width:360px;min-width:220px;";

  function say(html) {
    box.innerHTML = "🔁 <b>TC Rollover</b><br>" + html;
  }
  function attach() {
    if (document.body && !box.isConnected) document.body.appendChild(box);
  }

  say("Target date: <b>" + DATE + "</b><br>Starting…");
  if (document.body) attach();
  else document.addEventListener("DOMContentLoaded", attach);

  // ── Helpers ──────────────────────────────────────────────────────────────

  // Sets a value on an input in a way that React / PrimeFaces pick up.
  function setValue(el, val) {
    try {
      var proto = Object.getPrototypeOf(el);
      var desc  = Object.getOwnPropertyDescriptor(proto, "value") ||
                  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
      if (desc && desc.set) desc.set.call(el, val);
      else el.value = val;
    } catch (e) { el.value = val; }
    el.value = val;
    ["focus", "input", "change", "keyup", "blur"].forEach(function (t) {
      el.dispatchEvent(new Event(t, { bubbles: true }));
    });
  }

  var BOOK_TEXTS = [
    "book directly without changes",
    "reserva directamente sin cambios",
    "book directly",
    "reservar directamente",
    "direkt buchen ohne",
  ];

  function findBookButton() {
    // Try text match on all clickable elements
    var els = Array.from(
      document.querySelectorAll("a, button, span[role='button'], div[role='button'], input[type='submit']")
    );
    var found = els.find(function (el) {
      var t = (el.textContent || el.value || "").trim().toLowerCase();
      return BOOK_TEXTS.some(function (n) { return t.indexOf(n) > -1; });
    });
    if (found) return found;
    // Fallback: id-based
    return document.querySelector("[id*='directBook']");
  }

  function findDateInput() {
    return (
      document.querySelector("input[name*='departureDateMultidestination']") ||
      document.querySelector("input[id*='departureDateMultidestination']")   ||
      document.querySelector("input[name*='startDate']")                     ||
      document.querySelector("input[name*='departureDate']")                 ||
      document.querySelector("input[id*='departureDate']")                   ||
      document.querySelector("input[id*='startDate']")                       ||
      document.querySelector("input[placeholder*='Start date' i]")           ||
      document.querySelector("input[placeholder*='date' i]")
    );
  }

  var SEARCH_TEXTS = ["search", "buscar", "suchen", "start trip", "iniciar viaje", "empezar"];

  function findSearchButton() {
    var els = Array.from(
      document.querySelectorAll("button, a, input[type='submit'], span[role='button']")
    );
    return els.find(function (el) {
      var t = (el.textContent || el.value || "").trim().toLowerCase();
      return SEARCH_TEXTS.some(function (n) { return t === n || t.indexOf(n) > -1; });
    });
  }

  // ── travel-map flow ──────────────────────────────────────────────────────
  if (isMap) {
    say("On travel map — filling departure date…");
    var mapTries = 0;
    var mapIv = setInterval(function () {
      attach();
      var inp = findDateInput();
      if (inp) {
        clearInterval(mapIv);
        setValue(inp, DATE);
        say("Date set to <b>" + DATE + "</b> ✅<br>Review and confirm.");
        try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
      } else if (++mapTries > 80) {
        clearInterval(mapIv);
        say("Date field not found.<br>Enter <b>" + DATE + "</b> manually.");
      }
    }, 250);
    return;
  }

  // ── Idea page flow ───────────────────────────────────────────────────────
  // Step 1: Find and click "Book directly without changes"
  say("Looking for \"Book directly without changes\"…");
  var step1Tries = 0;

  var step1Iv = setInterval(function () {
    attach();
    var btn = findBookButton();
    if (btn) {
      clearInterval(step1Iv);
      say("Clicking \"Book directly without changes\"…");
      btn.click();
      // Step 2: Wait for date widget to appear
      startStep2();
    } else if (++step1Tries > 100) {
      clearInterval(step1Iv);
      say(
        "\"Book directly without changes\" button not found.<br>" +
        "Click it manually, then enter <b>" + DATE + "</b> in the date field."
      );
    }
  }, 200);

  function startStep2() {
    say("Widget opening — waiting for date field…");
    var step2Tries = 0;

    var step2Iv = setInterval(function () {
      attach();
      var inp = findDateInput();
      if (inp && inp.offsetParent !== null) {  // visible
        clearInterval(step2Iv);
        say("Setting date to <b>" + DATE + "</b>…");
        inp.focus();
        inp.click();
        setTimeout(function () {
          setValue(inp, DATE);
          say("Date set ✅ — clicking Search…");
          setTimeout(startStep3, 600);
        }, 300);
      } else if (++step2Tries > 80) {
        clearInterval(step2Iv);
        say("Date field not found in widget.<br>Enter <b>" + DATE + "</b> manually.");
      }
    }, 300);
  }

  function startStep3() {
    attach();
    var searchBtn = findSearchButton();
    if (searchBtn && !searchBtn.disabled) {
      searchBtn.click();
      say("Search clicked ✅ — TC is loading results.");
    } else {
      // Retry a few times in case Search is briefly disabled
      var tries = 0;
      var iv = setInterval(function () {
        attach();
        var btn = findSearchButton();
        if (btn && !btn.disabled) {
          clearInterval(iv);
          btn.click();
          say("Search clicked ✅ — TC is loading results.");
        } else if (++tries > 20) {
          clearInterval(iv);
          say("Date set to <b>" + DATE + "</b> ✅<br>Click <b>Search</b> to continue.");
        }
      }, 300);
    }
  }
})();
