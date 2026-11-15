// Momira TC Rollover -- content script v10
// Runs in world: "MAIN" so PrimeFaces.ab() is accessible when clicking buttons.
// Reads rollover data from localStorage (set by the Streamlit tool before opening this tab).
// Uses real TC CSS selectors: a.button-select-idea, input.departure-date, a.dev-button-startTrip
(function () {
  "use strict";

  var LS_KEY = "tcRollover";

  // ── Read rollover data from localStorage ─────────────────────────────────
  var raw;
  try { raw = localStorage.getItem(LS_KEY); } catch (e) { return; }
  if (!raw) return;

  var data;
  try { data = JSON.parse(raw); } catch (e) { return; }

  // Ignore stale entries (older than 2 minutes)
  if (!data.date || !data.timestamp || Date.now() - data.timestamp > 120000) {
    try { localStorage.removeItem(LS_KEY); } catch (e) {}
    return;
  }

  // Clear immediately so it doesn't re-trigger on refresh
  try { localStorage.removeItem(LS_KEY); } catch (e) {}

  var DATE = data.date; // DD.MM.YYYY

  // ── Status overlay ───────────────────────────────────────────────────────
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;top:12px;right:12px;z-index:2147483647;" +
    "background:#111;color:#fff;font:12px/1.5 system-ui,sans-serif;" +
    "padding:14px 18px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.5);" +
    "max-width:380px;min-width:240px;word-break:break-word;";

  function say(html) {
    box.innerHTML = "<b>TC Rollover</b><br>" + html;
  }
  function attach() {
    if (document.body && !box.isConnected) document.body.appendChild(box);
  }

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  // ── Set value on PrimeFaces / jQuery UI datepicker input ─────────────────
  function setDateValue(el, dateStr) {
    // Try jQuery UI datepicker first (TC uses it)
    if (typeof jQuery !== "undefined" && jQuery.fn.datepicker) {
      try {
        var parts = dateStr.split(".");
        var dateObj = new Date(parseInt(parts[2]), parseInt(parts[1]) - 1, parseInt(parts[0]));
        jQuery(el).datepicker("setDate", dateObj);
        jQuery(el).trigger("change");
        return;
      } catch (e) { /* fall through to manual */ }
    }
    // Manual: React-safe value setter + events
    try {
      var desc = Object.getOwnPropertyDescriptor(
        Object.getPrototypeOf(el), "value"
      ) || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
      if (desc && desc.set) desc.set.call(el, dateStr);
      else el.value = dateStr;
    } catch (e) { el.value = dateStr; }
    el.value = dateStr;
    ["focus", "input", "change", "keyup", "blur"].forEach(function (n) {
      el.dispatchEvent(new Event(n, { bubbles: true }));
    });
  }

  // ── Find elements using real TC selectors + fallbacks ────────────────────

  function findBookButton() {
    // Real TC selector first
    var btn = document.querySelector("a.button-select-idea");
    if (btn) return btn;
    // Fallback: PrimeFaces ID
    btn = document.querySelector("[id*='directBook']");
    if (btn) return btn;
    // Fallback: text match
    var texts = ["book directly without changes", "book directly",
                 "reserva directamente sin cambios", "reservar directamente"];
    var candidates = [];
    document.querySelectorAll("a, button").forEach(function (el) {
      var t = (el.textContent || "").trim().toLowerCase();
      for (var i = 0; i < texts.length; i++) {
        if (t.indexOf(texts[i]) > -1) { candidates.push(el); break; }
      }
    });
    if (candidates.length > 0) {
      candidates.sort(function (a, b) {
        return (a.textContent || "").length - (b.textContent || "").length;
      });
      return candidates[0];
    }
    return null;
  }

  function findDateInput() {
    // Real TC selector first
    var inp = document.querySelector("input.departure-date");
    if (inp) return inp;
    // Fallbacks
    var selectors = [
      "input[id*='departureDateMultidestination']",
      "input[name*='departureDateMultidestination']",
      "input[id*='departureDate']",
      "input[name*='departureDate']",
      "input[id*='startDate']",
      "input[name*='startDate']",
      "input[placeholder*='Departure' i]",
      "input[placeholder*='Start date' i]",
      "input[placeholder*='date' i]",
      "input[type='date']"
    ];
    for (var i = 0; i < selectors.length; i++) {
      var el = document.querySelector(selectors[i]);
      if (el) return el;
    }
    return null;
  }

  function findSearchButton() {
    // Real TC selector first
    var btn = document.querySelector("a.dev-button-startTrip");
    if (btn) return btn;
    // Fallback: PrimeFaces ID
    btn = document.querySelector("[id*='startTrip'], [id*='searchBtn'], [id*='searchButton']");
    if (btn && !btn.disabled) return btn;
    // Fallback: text match
    var texts = ["search", "start trip", "buscar", "suchen", "iniciar"];
    var els = document.querySelectorAll("button, a, input[type='submit']");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.disabled) continue;
      var t = (el.textContent || el.value || "").trim().toLowerCase();
      for (var j = 0; j < texts.length; j++) {
        if (t === texts[j] || t.indexOf(texts[j]) > -1) return el;
      }
    }
    return null;
  }

  function listInputs() {
    var inputs = document.querySelectorAll("input:not([type='hidden'])");
    var info = [];
    for (var i = 0; i < inputs.length && i < 10; i++) {
      var el = inputs[i];
      info.push((el.className || el.id || el.name || el.type || "?").substring(0, 30));
    }
    return inputs.length + " inputs: " + info.join(" | ");
  }

  // ── Main flow ────────────────────────────────────────────────────────────

  onReady(function () {
    attach();
    say("Date: <b>" + DATE + "</b><br>Looking for Book button...");

    // Step 1: Find and click "Book directly without changes"
    var s1tries = 0;
    var s1iv = setInterval(function () {
      attach();
      var btn = findBookButton();
      if (btn) {
        clearInterval(s1iv);
        var label = (btn.textContent || "").trim().substring(0, 40);
        say("Clicking: <b>" + label + "</b>");
        // world: MAIN — btn.click() runs in TC's JS context, PrimeFaces works
        btn.click();
        // Step 2: wait for the date widget to appear
        setTimeout(waitForDateField, 2000);
      } else if (++s1tries > 100) {
        clearInterval(s1iv);
        say("Book button not found.<br>Click it manually, then enter <b>" + DATE + "</b>.");
      }
    }, 200);
  });

  function waitForDateField() {
    say("Widget should be open. Looking for date field...<br><small>" + listInputs() + "</small>");
    var s2tries = 0;
    var s2iv = setInterval(function () {
      attach();
      var inp = findDateInput();
      if (inp) {
        clearInterval(s2iv);
        var id = inp.className || inp.id || inp.name || "?";
        say("Found date input: <b>" + id + "</b><br>Setting <b>" + DATE + "</b>...");
        inp.focus();
        inp.click();
        setTimeout(function () {
          setDateValue(inp, DATE);
          say("Date set to <b>" + DATE + "</b>!<br>Clicking Search...");
          setTimeout(clickSearch, 1000);
        }, 500);
      } else if (++s2tries > 60) {
        clearInterval(s2iv);
        say("Date field not found.<br><small>" + listInputs() + "</small><br>" +
            "Enter <b>" + DATE + "</b> manually.");
      }
    }, 300);
  }

  function clickSearch() {
    attach();
    var tries = 0;
    var iv = setInterval(function () {
      attach();
      var btn = findSearchButton();
      if (btn) {
        clearInterval(iv);
        btn.click();
        say("Search clicked! TC is loading results...");
      } else if (++tries > 20) {
        clearInterval(iv);
        say("Date = <b>" + DATE + "</b>.<br>Click Search/Start Trip manually.");
      }
    }, 300);
  }

})();
