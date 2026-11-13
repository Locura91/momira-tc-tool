// Momira TC Rollover -- content script v3
// Runs on every momira.travel page.
// Flow on idea page:
//   1. Detect #tcrollover=DD.MM.YYYY in URL hash
//   2. Wait for "Book directly without changes" button -> click it
//   3. Wait for the booking widget / date input to appear
//   4. Fill the departure date
//   5. Click the Search button
(function () {
  "use strict";

  var STORAGE_KEY = "tcrollover_pending_date";
  var pathname = location.pathname;
  var hash = location.hash || "";

  var isIdea = pathname.indexOf("/idea/") > -1;
  var isMap  = pathname.indexOf("travel-map") > -1;

  // Detect date from hash or sessionStorage
  var DATE = null;

  if (isIdea) {
    var m = hash.match(/tcrollover=(\d{2}\.\d{2}\.\d{4})/);
    if (m) {
      DATE = m[1];
      try { sessionStorage.setItem(STORAGE_KEY, DATE); } catch (e) {}
    } else {
      try { DATE = sessionStorage.getItem(STORAGE_KEY); } catch (e) {}
    }
  } else if (isMap) {
    try { DATE = sessionStorage.getItem(STORAGE_KEY); } catch (e) {}
  }

  if (!DATE) return;

  // Status overlay
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;top:12px;right:12px;z-index:2147483647;" +
    "background:#111;color:#fff;font:13px/1.5 system-ui,sans-serif;" +
    "padding:12px 16px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.4);" +
    "max-width:360px;min-width:220px;";

  function say(html) {
    box.innerHTML = "TC Rollover<br>" + html;
  }
  function attach() {
    if (document.body && !box.isConnected) document.body.appendChild(box);
  }

  say("Target date: <b>" + DATE + "</b><br>Starting...");
  if (document.body) {
    attach();
  } else {
    document.addEventListener("DOMContentLoaded", attach);
  }

  // Sets a value on a React/PrimeFaces input
  function setValue(el, val) {
    try {
      var proto = Object.getPrototypeOf(el);
      var desc = Object.getOwnPropertyDescriptor(proto, "value") ||
                 Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
      if (desc && desc.set) { desc.set.call(el, val); }
      else { el.value = val; }
    } catch (e) { el.value = val; }
    el.value = val;
    var events = ["focus", "input", "change", "keyup", "blur"];
    for (var i = 0; i < events.length; i++) {
      el.dispatchEvent(new Event(events[i], { bubbles: true }));
    }
  }

  var BOOK_TEXTS = [
    "book directly without changes",
    "reserva directamente sin cambios",
    "book directly",
    "reservar directamente",
    "direkt buchen"
  ];

  function findBookButton() {
    var all = document.querySelectorAll("a, button, span, div");
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var t = (el.textContent || "").trim().toLowerCase();
      for (var j = 0; j < BOOK_TEXTS.length; j++) {
        if (t.indexOf(BOOK_TEXTS[j]) > -1) return el;
      }
    }
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

  var SEARCH_TEXTS = ["search", "buscar", "suchen", "start trip", "iniciar"];

  function findSearchButton() {
    var all = document.querySelectorAll("button, a, input[type='submit']");
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var t = (el.textContent || el.value || "").trim().toLowerCase();
      for (var j = 0; j < SEARCH_TEXTS.length; j++) {
        if (t.indexOf(SEARCH_TEXTS[j]) > -1 && !el.disabled) return el;
      }
    }
    return null;
  }

  // travel-map flow: just fill the date
  if (isMap) {
    say("On travel map -- filling date...");
    var mapTries = 0;
    var mapIv = setInterval(function () {
      attach();
      var inp = findDateInput();
      if (inp) {
        clearInterval(mapIv);
        setValue(inp, DATE);
        say("Date set to <b>" + DATE + "</b> OK");
        try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
      } else if (++mapTries > 80) {
        clearInterval(mapIv);
        say("Date field not found. Enter <b>" + DATE + "</b> manually.");
      }
    }, 250);
    return;
  }

  // Idea page flow:
  // Step 1 -- find and click "Book directly without changes"
  say("Looking for the Book button...");
  var step1Tries = 0;
  var step1Iv = setInterval(function () {
    attach();
    var btn = findBookButton();
    if (btn) {
      clearInterval(step1Iv);
      say("Clicking Book directly without changes...");
      btn.click();
      startStep2();
    } else if (++step1Tries > 100) {
      clearInterval(step1Iv);
      say("Book button not found.<br>Click it manually, then enter <b>" + DATE + "</b>.");
    }
  }, 200);

  // Step 2 -- wait for date widget, fill date, click Search
  function startStep2() {
    say("Widget opening -- waiting for date field...");
    var step2Tries = 0;
    var step2Iv = setInterval(function () {
      attach();
      var inp = findDateInput();
      if (inp && inp.offsetParent !== null) {
        clearInterval(step2Iv);
        say("Setting date to <b>" + DATE + "</b>...");
        inp.focus();
        inp.click();
        setTimeout(function () {
          setValue(inp, DATE);
          say("Date set OK -- clicking Search...");
          setTimeout(startStep3, 600);
        }, 300);
      } else if (++step2Tries > 80) {
        clearInterval(step2Iv);
        say("Date field not found. Enter <b>" + DATE + "</b> manually.");
      }
    }, 300);
  }

  // Step 3 -- click Search
  function startStep3() {
    attach();
    var tries = 0;
    var iv = setInterval(function () {
      attach();
      var btn = findSearchButton();
      if (btn) {
        clearInterval(iv);
        btn.click();
        say("Search clicked -- TC is loading results!");
        try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
      } else if (++tries > 20) {
        clearInterval(iv);
        say("Date set to <b>" + DATE + "</b> OK<br>Click Search to continue.");
      }
    }, 300);
  }

})();
