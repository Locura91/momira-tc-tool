// Momira TC Rollover -- content script v12
// Runs in world: "MAIN" so PrimeFaces.ab() is accessible when clicking buttons.
//
// GOAL: drive the package rollover automatically to the booking SUMMARY, then stop and let
// the human accept. The flow is: idea page -> "Book directly without changes" -> set departure
// date -> "Start trip / Search" -> summary. At the summary the human clicks
// "Save and overwrite the existing package". The AI must NEVER click "Book the trip".
//
// WHY A STATE MACHINE (not the old linear chain): clicking "Start trip / Search" navigates the
// tab to the booking page, which destroys this script's execution context. A linear chain can't
// survive that. Instead we persist {date, phase} in sessionStorage -- which is same-origin on
// momira.travel and SURVIVES the tab's internal navigations -- and on every injection we resume
// from the stored phase. The chosen date is seeded once from the URL hash (#tcr=DD.MM.YYYY),
// the only channel that crosses the origin boundary from the tool (tool.momira.travel).
//
// Real TC selectors: a.button-select-idea, input.departure-date, a.dev-button-startTrip.
(function () {
  "use strict";

  var SS_KEY = "tcRolloverState";
  var MAX_AGE_MS = 600000; // 10 min: abandon a stale run rather than resume it

  // ── state persistence (sessionStorage survives TC's internal navigations) ────────────────
  function loadState() {
    try { return JSON.parse(sessionStorage.getItem(SS_KEY) || "null"); } catch (e) { return null; }
  }
  function saveState(s) { try { sessionStorage.setItem(SS_KEY, JSON.stringify(s)); } catch (e) {} }
  function clearState() { try { sessionStorage.removeItem(SS_KEY); } catch (e) {} }

  // ── seed from the URL hash on first arrival, else resume stored state ────────────────────
  var state = loadState();
  try {
    var m = (location.hash || "").match(/[#&]tcr=([^&]+)/);
    if (m) {
      state = { active: true, date: decodeURIComponent(m[1]), phase: "click_book", ts: Date.now() };
      saveState(state);
      // strip so a refresh doesn't re-seed and TC's own navigations don't carry it forward
      try { history.replaceState(null, "", location.pathname + location.search); } catch (e) {}
    }
  } catch (e) {}

  if (!state || !state.active) return;
  if (!state.ts || Date.now() - state.ts > MAX_AGE_MS) { clearState(); return; }

  var DATE = state.date; // DD.MM.YYYY

  function setPhase(p) { state.phase = p; state.ts = Date.now(); saveState(state); }

  // ── status overlay ───────────────────────────────────────────────────────────────────────
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;top:12px;right:12px;z-index:2147483647;" +
    "background:#111;color:#fff;font:12px/1.5 system-ui,sans-serif;" +
    "padding:14px 18px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.5);" +
    "max-width:400px;min-width:240px;word-break:break-word;";
  function say(html) { box.innerHTML = "<b>TC Rollover</b><br>" + html; }
  function attach() { if (document.body && !box.isConnected) document.body.appendChild(box); }
  function onReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  // ── safety: text helpers + a guard that refuses to click the real "Book the trip" ────────
  function textOf(el) { return ((el.textContent || el.value || "") + "").trim().toLowerCase(); }
  // Kept intentionally specific so it never matches the SAFE "Book directly without changes".
  var BOOK_TRIP_TEXTS = ["book the trip", "book this trip", "confirm booking", "reservar el viaje"];
  function isBookTrip(el) { var t = textOf(el); for (var i = 0; i < BOOK_TRIP_TEXTS.length; i++) if (t.indexOf(BOOK_TRIP_TEXTS[i]) > -1) return true; return false; }
  function safeClick(el) {
    if (!el) return false;
    if (isBookTrip(el)) { say("Refused to click a <b>Book the trip</b> button. AI never books."); return false; }
    el.click();
    return true;
  }

  // ── set value on PrimeFaces / jQuery UI datepicker input ──────────────────────────────────
  function setDateValue(el, dateStr) {
    if (typeof jQuery !== "undefined" && jQuery.fn.datepicker) {
      try {
        var p = dateStr.split(".");
        jQuery(el).datepicker("setDate", new Date(parseInt(p[2]), parseInt(p[1]) - 1, parseInt(p[0])));
        jQuery(el).trigger("change");
        return;
      } catch (e) { /* fall through */ }
    }
    try {
      var desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value") ||
                 Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
      if (desc && desc.set) desc.set.call(el, dateStr); else el.value = dateStr;
    } catch (e) { el.value = dateStr; }
    el.value = dateStr;
    ["focus", "input", "change", "keyup", "blur"].forEach(function (n) {
      el.dispatchEvent(new Event(n, { bubbles: true }));
    });
  }

  // ── element finders (real TC selectors first, then fallbacks) ─────────────────────────────
  function findBookButton() {
    // "Book directly without changes" sits right next to "Customize & Book Your Trip" and both
    // carry the same CSS class (a.button-select-idea), so a plain class selector grabs the wrong
    // one. Match the visible text first, and never return a "Customize" button.
    var texts = ["book directly without changes", "book directly",
                 "reserva directamente sin cambios", "reservar directamente"];
    var all = document.querySelectorAll("a, button");
    var candidates = [];
    for (var i = 0; i < all.length; i++) {
      var el = all[i], t = textOf(el);
      if (t.indexOf("customi") > -1) continue; // "Customize & Book Your Trip" is never the target
      for (var j = 0; j < texts.length; j++) if (t.indexOf(texts[j]) > -1) { candidates.push(el); break; }
    }
    if (candidates.length) {
      candidates.sort(function (a, b) { return (a.textContent || "").length - (b.textContent || "").length; });
      return candidates[0];
    }
    // Fallback to the class/id selectors, but skip a Customize button if those are shared.
    var byClass = document.querySelectorAll("a.button-select-idea, [id*='directBook']");
    for (var k = 0; k < byClass.length; k++) if (textOf(byClass[k]).indexOf("customi") === -1) return byClass[k];
    return null;
  }

  function findDateInput() {
    var inp = document.querySelector("input.departure-date");
    if (inp) return inp;
    var selectors = [
      "input[id*='departureDateMultidestination']", "input[name*='departureDateMultidestination']",
      "input[id*='departureDate']", "input[name*='departureDate']",
      "input[id*='startDate']", "input[name*='startDate']",
      "input[placeholder*='Departure' i]", "input[placeholder*='Start date' i]",
      "input[placeholder*='date' i]", "input[type='date']"
    ];
    for (var i = 0; i < selectors.length; i++) { var el = document.querySelector(selectors[i]); if (el) return el; }
    return null;
  }

  function findSearchButton() {
    var btn = document.querySelector("a.dev-button-startTrip");
    if (btn) return btn;
    btn = document.querySelector("[id*='startTrip'], [id*='searchBtn'], [id*='searchButton']");
    if (btn && !btn.disabled) return btn;
    var texts = ["start trip", "search", "buscar", "iniciar"];
    var els = document.querySelectorAll("button, a, input[type='submit']");
    for (var i = 0; i < els.length; i++) {
      var el = els[i]; if (el.disabled) continue;
      var t = textOf(el);
      for (var j = 0; j < texts.length; j++) if (t === texts[j] || t.indexOf(texts[j]) > -1) return el;
    }
    return null;
  }

  // The rollover "accept" button. Matched by text; selector is confirmed via record mode below.
  var SAVE_OVERWRITE_TEXTS = [
    "save and overwrite", "save & overwrite", "overwrite the existing", "overwrite existing package",
    "overwrite package", "overwrite the existing trip", "overwrite existing trip",
    "guardar y sobrescribir", "sobrescribir", "speichern und überschreiben", "überschreiben"
  ];
  function findSaveOverwriteButton() {
    // Widen selector: TC may render the overwrite action as a div, span or label, not just a/button
    var els = document.querySelectorAll("a, button, input[type='submit'], div[role='button'], span[role='button'], [onclick], .confirm-trip, [class*='overwrite'], [class*='Overwrite'], [id*='overwrite'], [id*='Overwrite']");
    for (var i = 0; i < els.length; i++) {
      var t = textOf(els[i]);
      for (var j = 0; j < SAVE_OVERWRITE_TEXTS.length; j++) if (t.indexOf(SAVE_OVERWRITE_TEXTS[j]) > -1) return els[i];
    }
    // Broadest fallback: scan ALL elements for the text
    var allEls = document.querySelectorAll("*");
    for (var k = 0; k < allEls.length; k++) {
      var el = allEls[k];
      if (el.children && el.children.length > 3) continue; // skip containers
      var t2 = textOf(el);
      for (var j2 = 0; j2 < SAVE_OVERWRITE_TEXTS.length; j2++) if (t2.indexOf(SAVE_OVERWRITE_TEXTS[j2]) > -1) return el;
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

  // Record mode: dump every clickable control with its text + a selector hint, so the exact
  // "Save and overwrite" / "Book the trip" selectors can be mapped from a real run.
  function recordButtons(compact) {
    var els = document.querySelectorAll("a, button, input[type='submit'], div[role='button'], span[role='button'], [onclick], [class*='overwrite'], [class*='Overwrite'], [id*='overwrite'], [id*='Overwrite'], .confirm-trip");
    var out = [];
    for (var i = 0; i < els.length && out.length < 30; i++) {
      var el = els[i];
      var t = (el.textContent || el.value || "").trim();
      if (!t) continue;
      var cls = (el.className && typeof el.className === "string")
        ? "." + el.className.trim().split(/\s+/).join(".") : "";
      var sel = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + cls;
      out.push(t.substring(0, 45) + "  →  " + sel.substring(0, 90));
    }
    try { console.log("[TC Rollover] clickable controls on this page:\n" + out.join("\n")); } catch (e) {}
    if (compact) return out.map(function (x) { return x.split("  →")[0]; }).join(" | ");
    return out.join("\n");
  }

  // ── generic wait-for-element with retry + timeout ─────────────────────────────────────────
  function waitFor(find, onFound, onTimeout, maxTries, interval) {
    var n = 0; attach();
    var iv = setInterval(function () {
      attach();
      var r = find();
      if (r) { clearInterval(iv); onFound(r); }
      else if (++n > maxTries) { clearInterval(iv); if (onTimeout) onTimeout(); }
    }, interval || 300);
  }

  // ── phases ────────────────────────────────────────────────────────────────────────────────
  function run() {
    attach();
    switch (state.phase) {
      case "click_book":  return phaseClickBook();
      case "set_date":    return phaseSetDate();
      case "click_search":return phaseClickSearch();
      case "summary":     return phaseSummary();
      default: return;
    }
  }

  function phaseClickBook() {
    say("Date: <b>" + DATE + "</b><br>Looking for “Book directly without changes”…");
    waitFor(findBookButton, function (btn) {
      say("Clicking: <b>" + (btn.textContent || "").trim().substring(0, 40) + "</b>");
      setPhase("set_date");          // persist BEFORE clicking (the click may navigate)
      safeClick(btn);
      setTimeout(run, 900);          // same-page AJAX: continue here; if it navigated, re-injection resumes
    }, function () {
      say("“Book directly” not found. Click it by hand — the date you want is <b>" + DATE + "</b>.");
    }, 100, 200);
  }

  function phaseSetDate() {
    say("Looking for the departure-date field…<br><small>" + listInputs() + "</small>");
    waitFor(findDateInput, function (inp) {
      inp.focus(); inp.click();
      setTimeout(function () {
        setDateValue(inp, DATE);
        say("Date set to <b>" + DATE + "</b>. Looking for Search…");
        setPhase("click_search");
        setTimeout(run, 600);
      }, 400);
    }, function () {
      say("Date field not found.<br><small>" + listInputs() + "</small><br>Enter <b>" + DATE + "</b> by hand.");
    }, 60, 300);
  }

  function phaseClickSearch() {
    waitFor(findSearchButton, function (btn) {
      say("Starting search for <b>" + DATE + "</b>…");
      setPhase("summary");           // persist BEFORE clicking: Search navigates; re-injection resumes at summary
      safeClick(btn);
      setTimeout(run, 1500);         // in case Search renders the summary in-page instead of navigating
    }, function () {
      say("Search button not found. Click <b>Start trip / Search</b> by hand.");
    }, 40, 300);
  }

  function phaseSummary() {
    say("Reached the summary. Looking for <b>Save &amp; overwrite</b>…");
    recordButtons(); // log controls for selector mapping
    waitFor(findSaveOverwriteButton, function (btn) {
      clearState(); // automation is done — the human decides from here
      btn.style.outline = "4px solid #22c55e";
      btn.style.outlineOffset = "3px";
      try { btn.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (e) {}
      say("✅ Ready — date is <b>" + DATE + "</b>.<br>Review the trip, then click the highlighted " +
          "<b>Save &amp; overwrite the existing package</b> button to finish.<br>" +
          "<b>The AI will never click “Book the trip”.</b>");
    }, function () {
      clearState();
      say("Summary reached, but couldn’t spot <b>Save &amp; overwrite</b> by name.<br>" +
          "<small>Controls here: " + recordButtons(true) + "</small><br>" +
          "Click <b>Save and overwrite the existing package</b> by hand — never “Book the trip”.");
    }, 80, 400);
  }

  onReady(run);
})();
