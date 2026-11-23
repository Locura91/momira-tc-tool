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
      var seedDate = decodeURIComponent(m[1]);
      state = { active: true, date: seedDate, originalDate: seedDate, flexDays: 0, phase: "click_book", ts: Date.now() };
      saveState(state);
      // strip so a refresh doesn't re-seed and TC's own navigations don't carry it forward
      try { history.replaceState(null, "", location.pathname + location.search); } catch (e) {}
    }
  } catch (e) {}

  if (!state || !state.active) return;
  if (!state.ts || Date.now() - state.ts > MAX_AGE_MS) { clearState(); return; }

  var DATE = state.date; // DD.MM.YYYY
  var DATE_FLEX_DAYS = state.flexDays || 0; // how many days we've shifted forward so far
  var MAX_FLEX_DAYS = 30; // scan up to +30 days for an available date
  var ORIGINAL_DATE = state.originalDate || DATE; // the date the user originally chose

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

  // ── date arithmetic helpers ───────────────────────────────────────────────────────────────
  function parseDDMMYYYY(s) {
    var p = s.split("."); return new Date(parseInt(p[2]), parseInt(p[1]) - 1, parseInt(p[0]));
  }
  function formatDDMMYYYY(d) {
    var dd = ("0" + d.getDate()).slice(-2), mm = ("0" + (d.getMonth() + 1)).slice(-2);
    return dd + "." + mm + "." + d.getFullYear();
  }
  function addDays(dateStr, n) {
    var d = parseDDMMYYYY(dateStr); d.setDate(d.getDate() + n); return formatDDMMYYYY(d);
  }

  // ── calendar panel detection (TC uses multiple datepicker variants) ────────────────────────
  var PANEL_SELECTORS = [
    "#ui-datepicker-div", ".ui-datepicker", ".hasDatepicker + .ui-datepicker",
    ".ui-datepicker-inline", "[class*='datepicker']", "[class*='calendar']",
    ".p-datepicker", ".p-datepicker-panel"  // PrimeFaces
  ];
  function findCalendarPanel() {
    for (var i = 0; i < PANEL_SELECTORS.length; i++) {
      var el = document.querySelector(PANEL_SELECTORS[i]);
      if (el && el.offsetParent !== null) return el;
      if (el && el.style && el.style.display !== "none") return el;
    }
    return null;
  }

  function logCal(msg) { try { console.log("[TC Rollover Cal] " + msg); } catch (e) {} }

  // Dump the calendar structure for debugging
  function dumpCalendar() {
    var panel = findCalendarPanel();
    if (!panel) { logCal("No calendar panel found. Selectors tried: " + PANEL_SELECTORS.join(", ")); return; }
    logCal("Panel found: " + panel.tagName + " class=" + (panel.className || ""));
    var tds = panel.querySelectorAll("td");
    var dump = [];
    for (var i = 0; i < tds.length; i++) {
      var td = tds[i];
      var child = td.querySelector("a, span");
      if (!child) continue;
      var num = child.textContent.trim();
      if (!num || isNaN(parseInt(num))) continue;
      var tag = child.tagName;
      var tdClass = td.className || "";
      var childClass = child.className || "";
      var isLink = tag === "A";
      dump.push(num + "(" + tag + " cls=" + childClass.substring(0, 30) + " td=" + tdClass.substring(0, 30) + " link=" + isLink + ")");
    }
    logCal("Calendar days: " + dump.join(" | "));
  }

  // Check if a date is available (clickable) in an open datepicker calendar.
  // In TC's jQuery UI datepicker: available dates have <a> tags, disabled dates have <span> tags
  // or the td has class ui-state-disabled / ui-datepicker-unselectable.
  function checkDateInCalendar(dateStr) {
    var panel = findCalendarPanel();
    if (!panel) { logCal("checkDate: no panel"); return "no_calendar"; }
    var d = parseDDMMYYYY(dateStr);
    var day = d.getDate(), month = d.getMonth(), year = d.getFullYear();
    logCal("checkDate: looking for day=" + day + " month=" + month + " year=" + year);

    // Strategy 1: data attributes (jQuery UI standard)
    var cells = panel.querySelectorAll("td[data-handler='selectDay']");
    for (var i = 0; i < cells.length; i++) {
      var a = cells[i].querySelector("a");
      if (!a) continue;
      var cellDay = parseInt(a.getAttribute("data-date") || a.textContent);
      var cellMonth = parseInt(a.getAttribute("data-month"));
      var cellYear = parseInt(a.getAttribute("data-year"));
      if (cellDay === day && (isNaN(cellMonth) || cellMonth === month) && (isNaN(cellYear) || cellYear === year)) {
        logCal("checkDate: found via data-handler, day=" + cellDay + " available");
        return "available";
      }
    }

    // Strategy 2: scan all td cells by day number
    var allTds = panel.querySelectorAll("td");
    for (var j = 0; j < allTds.length; j++) {
      var td = allTds[j];
      var child = td.querySelector("a, span");
      if (!child) continue;
      var num = parseInt(child.textContent.trim());
      if (num !== day) continue;
      // Skip "other month" cells (previous/next month day numbers)
      if (td.classList.contains("ui-datepicker-other-month")) continue;
      if (td.classList.contains("other-month")) continue;

      var isDisabled = td.classList.contains("ui-state-disabled") ||
                       td.classList.contains("ui-datepicker-unselectable") ||
                       td.classList.contains("disabled") ||
                       child.tagName === "SPAN"; // jQuery UI: disabled days use <span>, enabled use <a>
      logCal("checkDate: day " + num + " tag=" + child.tagName + " tdClass=" + (td.className || "") + " disabled=" + isDisabled);
      return isDisabled ? "disabled" : "available";
    }

    logCal("checkDate: day " + day + " not found in visible calendar");
    return "not_visible";
  }

  // Navigate the datepicker to show a specific month/year
  function navigateCalendarTo(dateStr, cb) {
    var d = parseDDMMYYYY(dateStr);
    var targetMonth = d.getMonth(), targetYear = d.getFullYear();
    var attempts = 0;
    var maxClicks = 24; // don't loop forever

    function tryNav() {
      var panel = findCalendarPanel();
      if (!panel) {
        if (++attempts < 15) { setTimeout(tryNav, 400); return; }
        logCal("navigateTo: panel never appeared"); cb(false); return;
      }

      // Read current displayed month/year from calendar header
      var monthEl = panel.querySelector(".ui-datepicker-month, .p-datepicker-month");
      var yearEl = panel.querySelector(".ui-datepicker-year, .p-datepicker-year");
      var headerText = "";
      var curMonth, curYear;

      if (monthEl && yearEl) {
        if (monthEl.tagName === "SELECT") curMonth = parseInt(monthEl.value);
        else {
          var monthText = monthEl.textContent.trim();
          var testDate = Date.parse(monthText + " 1, 2000");
          curMonth = isNaN(testDate) ? -1 : new Date(testDate).getMonth();
        }
        if (yearEl.tagName === "SELECT") curYear = parseInt(yearEl.value);
        else curYear = parseInt(yearEl.textContent.trim());
      } else {
        // Try reading a combined header like "March 2027"
        var titleEl = panel.querySelector(".ui-datepicker-title, .p-datepicker-title");
        if (titleEl) {
          headerText = titleEl.textContent.trim();
          var parts = headerText.match(/([A-Za-zäöüÄÖÜéè]+)\s+(\d{4})/);
          if (parts) {
            var testDate2 = Date.parse(parts[1] + " 1, " + parts[2]);
            curMonth = isNaN(testDate2) ? -1 : new Date(testDate2).getMonth();
            curYear = parseInt(parts[2]);
          }
        }
      }

      logCal("navigateTo: target=" + targetMonth + "/" + targetYear + " current=" + curMonth + "/" + curYear + " header=" + headerText);

      if (isNaN(curMonth) || isNaN(curYear) || curMonth < 0) {
        if (++attempts < 15) { setTimeout(tryNav, 400); return; }
        logCal("navigateTo: can't read month/year"); cb(false); return;
      }

      if (curYear === targetYear && curMonth === targetMonth) { cb(true); return; }

      if (--maxClicks <= 0) { logCal("navigateTo: max clicks reached"); cb(false); return; }

      var diff = (targetYear - curYear) * 12 + (targetMonth - curMonth);
      var arrow = diff > 0
        ? panel.querySelector("a.ui-datepicker-next, .ui-datepicker-next a, .ui-datepicker-next, .p-datepicker-next")
        : panel.querySelector("a.ui-datepicker-prev, .ui-datepicker-prev a, .ui-datepicker-prev, .p-datepicker-prev");
      if (arrow) {
        logCal("navigateTo: clicking " + (diff > 0 ? "next" : "prev"));
        arrow.click();
        setTimeout(tryNav, 500);
      } else {
        logCal("navigateTo: no arrow found");
        cb(false);
      }
    }
    tryNav();
  }

  // Click a specific day in the open calendar
  function clickDateInCalendar(dateStr) {
    var d = parseDDMMYYYY(dateStr);
    var day = d.getDate();
    var panel = findCalendarPanel();
    if (!panel) { logCal("clickDate: no panel"); return false; }
    var allTds = panel.querySelectorAll("td");
    for (var j = 0; j < allTds.length; j++) {
      var td = allTds[j];
      if (td.classList.contains("ui-datepicker-other-month") || td.classList.contains("other-month")) continue;
      var link = td.querySelector("a");
      if (!link) continue;
      if (parseInt(link.textContent.trim()) !== day) continue;
      if (td.classList.contains("ui-state-disabled") || td.classList.contains("ui-datepicker-unselectable")) continue;
      logCal("clickDate: clicking day " + day);
      link.click();
      return true;
    }
    logCal("clickDate: no clickable cell for day " + day);
    return false;
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
        logCal("Date input found. Dumping calendar structure…");
        dumpCalendar();
        // Try to find an available date in the calendar (up to +MAX_FLEX_DAYS)
        findAvailableDate(inp, DATE, function (finalDate, shifted) {
          if (shifted) {
            say("Date <b>" + ORIGINAL_DATE + "</b> not available.<br>Using nearest: <b>" + finalDate + "</b> (+" + DATE_FLEX_DAYS + " days).<br>Looking for Search…");
          } else {
            say("Date set to <b>" + finalDate + "</b>. Looking for Search…");
          }
          // Update state with the actual date used
          DATE = finalDate;
          state.date = finalDate;
          setPhase("click_search");
          setTimeout(run, 600);
        });
      }, 400);
    }, function () {
      say("Date field not found.<br><small>" + listInputs() + "</small><br>Enter <b>" + DATE + "</b> by hand.");
    }, 60, 300);
  }

  // Smart date finder: checks if the target date is available in the calendar.
  // If not, scans forward day by day up to +MAX_FLEX_DAYS.
  // If no calendar is detected (plain text input), just sets the date directly.
  function findAvailableDate(inp, targetDate, callback) {
    var panel = document.querySelector(".ui-datepicker, #ui-datepicker-div");
    if (!panel || panel.offsetParent === null) {
      // No calendar visible — just type the date (some packages use a plain input)
      setDateValue(inp, targetDate);
      callback(targetDate, false);
      return;
    }

    function tryDate(candidateDate, dayOffset) {
      if (dayOffset > MAX_FLEX_DAYS) {
        // Exhausted all days — fall back to typing the original date
        say("No available date within +" + MAX_FLEX_DAYS + " days of <b>" + ORIGINAL_DATE + "</b>.<br>Setting original date — TC may reject it.");
        setDateValue(inp, ORIGINAL_DATE);
        callback(ORIGINAL_DATE, false);
        return;
      }

      navigateCalendarTo(candidateDate, function (ok) {
        if (!ok) {
          // Can't navigate calendar — fall back to typing
          setDateValue(inp, candidateDate);
          callback(candidateDate, dayOffset > 0);
          return;
        }

        setTimeout(function () {
          var status = checkDateInCalendar(candidateDate);
          if (status === "available") {
            DATE_FLEX_DAYS = dayOffset;
            state.flexDays = dayOffset;
            clickDateInCalendar(candidateDate);
            callback(candidateDate, dayOffset > 0);
          } else if (status === "disabled" || status === "not_visible") {
            // Try next day
            var nextDate = addDays(ORIGINAL_DATE, dayOffset + 1);
            tryDate(nextDate, dayOffset + 1);
          } else {
            // No calendar or unexpected state — just type it
            setDateValue(inp, candidateDate);
            callback(candidateDate, dayOffset > 0);
          }
        }, 300);
      });
    }

    tryDate(targetDate, DATE_FLEX_DAYS);
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
