// Momira TC Rollover -- content script v13
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
  var MAX_SCAN_DATES = 14; // hard ceiling on the cheapest-of-N scan; the tool asks for 7
  var SCAN_MAX_AGE_MS = 2400000; // 40 min: a 7-date scan is ~8 searches, far longer than one

  // ── state persistence (sessionStorage survives TC's internal navigations) ────────────────
  function loadState() {
    try { return JSON.parse(sessionStorage.getItem(SS_KEY) || "null"); } catch (e) { return null; }
  }
  function saveState(s) { try { sessionStorage.setItem(SS_KEY, JSON.stringify(s)); } catch (e) {} }
  function clearState() { try { sessionStorage.removeItem(SS_KEY); } catch (e) {} }

  // ── seed from the URL on first arrival, else resume stored state ─────────────────────────
  // The date is accepted from EITHER ?tcr=DD.MM.YYYY or #tcr=DD.MM.YYYY. The hash was the
  // original channel, but an unrecognised fragment can send a client-side router to its
  // home route, so the query form is read too and is what the tool now prefers to send.
  var state = loadState();
  try {
    var m = (location.search || "").match(/[?&]tcr=([^&]+)/) ||
            (location.hash || "").match(/[#&]tcr=([^&]+)/);
    if (m) {
      var seedDate = decodeURIComponent(m[1]);
      // ?tcrscan=N asks for the CHEAPEST-OF-N scan (operator, 2026-11-23): run the whole
      // search N times on N consecutive days, remember each price, then re-run the cheapest
      // one and stop there for the human to accept. N absent or 1 = the original single-date
      // behaviour, unchanged.
      var sm = (location.search || "").match(/[?&]tcrscan=(\d+)/) ||
               (location.hash || "").match(/[#&]tcrscan=(\d+)/);
      var scanCount = sm ? Math.max(1, Math.min(parseInt(sm[1]), MAX_SCAN_DATES)) : 1;
      var SCAN_STEP_DAYS = 3; // +3 days between scan dates to cover 18 days, not 6
      var dates = [];
      for (var di = 0; di < scanCount; di++) dates.push(addDays(seedDate, di * SCAN_STEP_DAYS));
      state = {
        active: true,
        date: dates[0], originalDate: dates[0], flexDays: 0,
        phase: "click_book", ts: Date.now(),
        scan: scanCount > 1,
        dates: dates,
        idx: 0,
        results: [],
        finalRun: false,
        // Where to return to before each subsequent date. Captured here because this is the
        // one moment we are provably standing on the package page.
        ideaUrl: location.origin + location.pathname
      };
      saveState(state);
      // strip so a refresh doesn't re-seed and TC's own navigations don't carry it forward
      try {
        var cleanSearch = (location.search || "")
          .replace(/([?&])tcr=[^&]*&?/, "$1")
          .replace(/([?&])tcrscan=[^&]*&?/, "$1")
          .replace(/[?&]$/, "");
        history.replaceState(null, "", location.pathname + cleanSearch);
      } catch (e) {}
    }
  } catch (e) {}

  if (!state || !state.active) return;
  var ageLimit = state.scan ? SCAN_MAX_AGE_MS : MAX_AGE_MS;
  if (!state.ts || Date.now() - state.ts > ageLimit) { clearState(); return; }

  // ── did we actually land on a package page? ──────────────────────────────────────────────
  // CONFIRMED REAL SYMPTOM (operator, 2026-11-23): "it falls back to the main page of Travel
  // Compositor instead of opening the URL and opening the package". momira.travel redirects a
  // URL whose slug it cannot resolve to its own homepage. Until now the state machine kept
  // running there, hunting for a "Book directly without changes" button that a homepage never
  // has, and after 20 silent seconds reported only "not found" - which reads like the
  // extension is broken rather than the URL being wrong. Detect the homepage explicitly and
  // say so, so the operator knows to use the slug-free URL the tool now offers.
  function looksLikeIdeaPage() {
    return /\/idea\//.test(location.pathname) || /\/(booking|book|trip|search)/.test(location.pathname);
  }

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

  // ── price reading on the booking summary ─────────────────────────────────────────────────
  // CONFIRMED REAL LAYOUT (operator screenshot, 2026-11-23). The summary's left rail reads:
  //     Your trip summary
  //     1.924 €
  //     price per person
  //     (3.848 € Total price)
  //     Includes taxes and fees
  //     Idea id: 68693460
  // So: European number format (dot = thousands, comma = decimals), the € sign AFTER the
  // number, and TWO money figures - per person and total - which must not be confused. The
  // rollover screen in the tool shows "Current price / person", so per-person is what the
  // cheapest-of-N comparison uses; the total is captured too, purely as a cross-check
  // (total should be per-person x pax).
  function parseMoney(text) {
    if (!text) return null;
    // Grab the first number that looks like money, with optional European grouping.
    var m = String(text).match(/(\d[\d.\s ]*(?:,\d{1,2})?)\s*(?:€|EUR)|(?:€|EUR)\s*(\d[\d.\s ]*(?:,\d{1,2})?)/i);
    if (!m) return null;
    var raw = (m[1] || m[2] || "").replace(/[\s ]/g, "");
    if (!raw) return null;
    // European: "1.924" -> 1924, "1.924,56" -> 1924.56, "924,50" -> 924.50
    raw = raw.replace(/\./g, "").replace(",", ".");
    var v = parseFloat(raw);
    return isNaN(v) ? null : v;
  }

  // Smallest element that actually contains the text, so we read "1.924 €" and not the whole
  // left rail (which would also swallow the total and the Idea id).
  function smallestElementContaining(re, root) {
    var all = (root || document).querySelectorAll("div,span,p,strong,b,h1,h2,h3,h4,li,td");
    var best = null;
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var t = (el.textContent || "").trim();
      if (!t || t.length > 400) continue;
      if (!re.test(t)) continue;
      if (!best || t.length < (best.textContent || "").trim().length) best = el;
    }
    return best;
  }

  function readSummaryPrices() {
    var out = { perPerson: null, total: null, currency: "EUR", source: "" };

    // Total: the figure explicitly labelled "Total price" / "Gesamtpreis".
    var totalEl = smallestElementContaining(/total\s*price|gesamtpreis|precio\s*total/i);
    if (totalEl) {
      out.total = parseMoney(totalEl.textContent);
      if (out.total !== null) out.source = "labelled total";
    }

    // Per person: the figure labelled "price per person". The label and the number are
    // siblings in the observed layout, so when the label's own element has no money in it,
    // look at its parent.
    var ppEl = smallestElementContaining(/price\s*per\s*person|pro\s*person|preis\s*pro\s*person/i);
    if (ppEl) {
      out.perPerson = parseMoney(ppEl.textContent);
      if (out.perPerson === null && ppEl.parentElement) {
        // Parent holds "1.924 € / price per person / (3.848 € Total price)" - take the FIRST
        // money token, which is the per-person one that sits above the label.
        out.perPerson = parseMoney(ppEl.parentElement.textContent);
      }
      if (out.perPerson !== null && !out.source) out.source = "labelled per person";
    }

    // A per-person figure that accidentally latched onto the total is worse than none.
    if (out.perPerson !== null && out.total !== null && out.perPerson === out.total) {
      var container = ppEl && ppEl.parentElement ? ppEl.parentElement : null;
      if (container) {
        var moneys = (container.textContent.match(/\d[\d.\s ]*(?:,\d{1,2})?\s*€/g) || [])
          .map(parseMoney).filter(function (v) { return v !== null; });
        if (moneys.length >= 2) out.perPerson = Math.min.apply(null, moneys);
      }
    }

    if (out.perPerson === null && out.total === null) out.source = "not found";
    return out;
  }

  // Debug dump: every money-looking element with a selector hint, so an unexpected layout can
  // be mapped from a real run instead of guessed at.
  function recordPrices() {
    var all = document.querySelectorAll("div,span,p,strong,b,h1,h2,h3,h4,li,td");
    var out = [];
    for (var i = 0; i < all.length && out.length < 40; i++) {
      var el = all[i];
      var t = (el.textContent || "").trim();
      if (!t || t.length > 120) continue;
      if (!/(?:€|EUR)/i.test(t)) continue;
      if (el.children && el.children.length > 2) continue;
      var cls = (el.className && typeof el.className === "string")
        ? "." + el.className.trim().split(/\s+/).join(".") : "";
      out.push(t.substring(0, 60) + "  →  " + el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + cls.substring(0, 70));
    }
    try { console.log("[TC Rollover] money-looking elements:\n" + out.join("\n")); } catch (e) {}
    return out.join("\n");
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
      case "read_price":  return phaseReadPrice();
      case "summary":     return phaseSummary();
      default: return;
    }
  }

  function phaseClickBook() {
    if (!looksLikeIdeaPage()) {
      clearState();
      say("⚠️ This is <b>not the package page</b> — momira.travel redirected to " +
          "<code>" + location.pathname + "</code>.<br><br>" +
          "That means the URL the tool opened could not be resolved, almost always because " +
          "the slug built from the package title does not match this package.<br><br>" +
          "Go back to the tool, open <b>Manual fallback</b> and use the " +
          "<b>slug-free URL</b> there.<br><br>Your date: <b>" + DATE + "</b>");
      return;
    }
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
      say(scanProgressPrefix() + "Starting search for <b>" + DATE + "</b>…");
      // In a cheapest-of-N scan every search but the final one lands on read_price, which
      // records the price and moves to the next date. The final run (the winner being
      // re-quoted) lands on the normal summary phase so the human can accept it.
      var next = (state.scan && !state.finalRun) ? "read_price" : "summary";
      setPhase(next);                // persist BEFORE clicking: Search navigates; re-injection resumes
      safeClick(btn);
      setTimeout(run, 1500);         // in case Search renders the summary in-page instead of navigating
    }, function () {
      say("Search button not found. Click <b>Start trip / Search</b> by hand.");
    }, 40, 300);
  }

  // ── cheapest-of-N scan ───────────────────────────────────────────────────────────────────
  function scanProgressPrefix() {
    if (!state.scan) return "";
    if (state.finalRun) return "<b>Final:</b> re-quoting the cheapest date.<br>";
    return "<b>Date " + (state.idx + 1) + " of " + state.dates.length + "</b><br>";
  }

  function resultsTable() {
    if (!state.results || !state.results.length) return "";
    var cheapest = cheapestResult();
    var rows = state.results.map(function (r) {
      var isWin = cheapest && r.date === cheapest.date;
      var price = (r.perPerson !== null && r.perPerson !== undefined)
        ? r.perPerson.toLocaleString("de-DE", { minimumFractionDigits: 0 }) + " €"
        : "<i>no price</i>";
      return (isWin ? "<b>" : "") + r.date + " — " + price + (isWin ? " ✅ cheapest</b>" : "");
    });
    return "<div style='margin-top:8px;line-height:1.6'>" + rows.join("<br>") + "</div>";
  }

  function cheapestResult() {
    var best = null;
    for (var i = 0; i < (state.results || []).length; i++) {
      var r = state.results[i];
      if (r.perPerson === null || r.perPerson === undefined) continue;
      if (!best || r.perPerson < best.perPerson) best = r;
    }
    return best;
  }

  function goToIdeaPageForNextDate() {
    // Back to the package page to start the next date's search from scratch. A full
    // navigation (not history.back()) because the booking flow's own back behaviour is
    // unreliable and may restore a cached summary rather than a fresh form.
    var url = state.ideaUrl;
    if (!url) {
      clearState();
      say("Lost track of the package page, so the scan can't continue.<br>" + resultsTable());
      return;
    }
    setPhase("click_book");
    setTimeout(function () { location.href = url; }, 400);
  }

  function phaseReadPrice() {
    say(scanProgressPrefix() + "Reading the price for <b>" + DATE + "</b>…");
    var tries = 0;
    // The price is rendered with the summary, but the summary itself may still be loading,
    // so poll until a figure appears rather than reading once and recording a blank.
    var iv = setInterval(function () {
      attach();
      var p = readSummaryPrices();
      var done = (p.perPerson !== null || p.total !== null);
      if (!done && ++tries < 50) return;   // ~20s
      clearInterval(iv);

      if (!done) recordPrices();           // log candidates so an odd layout can be mapped
      state.results = state.results || [];
      state.results.push({
        date: DATE,
        perPerson: p.perPerson,
        total: p.total,
        source: p.source
      });

      var shown = p.perPerson !== null ? p.perPerson + " € p.P." : "no price found";
      say(scanProgressPrefix() + "<b>" + DATE + "</b>: " + shown + resultsTable());

      state.idx = (state.idx || 0) + 1;
      if (state.idx < state.dates.length) {
        DATE = state.dates[state.idx];
        state.date = DATE;
        state.originalDate = DATE;
        state.flexDays = 0;
        saveState(state);
        goToIdeaPageForNextDate();
        return;
      }

      // All dates scanned — re-quote the cheapest one and stop there for the human.
      var best = cheapestResult();
      if (!best) {
        clearState();
        say("Scanned all " + state.dates.length + " dates but could not read a price on any " +
            "of them.<br>" + resultsTable() +
            "<br>Open the browser console for the captured page structure.");
        return;
      }
      state.finalRun = true;
      DATE = best.date;
      state.date = best.date;
      state.originalDate = best.date;
      state.flexDays = 0;
      saveState(state);
      say("✅ Cheapest is <b>" + best.date + "</b> at <b>" + best.perPerson + " € p.P.</b>" +
          resultsTable() + "<br>Re-quoting that date now…");
      goToIdeaPageForNextDate();
    }, 400);
  }

  function phaseSummary() {
    say("Reached the summary. Looking for <b>Save &amp; overwrite</b>…");
    recordButtons(); // log controls for selector mapping
    // Snapshot the scan's findings before clearState() wipes them, so the final overlay can
    // still show which date won and what the alternatives cost.
    var scanSummary = state.scan ? resultsTable() : "";
    var wasScan = state.scan;
    waitFor(findSaveOverwriteButton, function (btn) {
      clearState(); // automation is done — the human decides from here
      btn.style.outline = "4px solid #22c55e";
      btn.style.outlineOffset = "3px";
      try { btn.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (e) {}
      say((wasScan ? "✅ <b>Cheapest of " + scanSummary.split("<br>").length + " dates</b> — " : "✅ Ready — ") +
          "date is <b>" + DATE + "</b>." + scanSummary +
          "<br>Review the trip, then click the highlighted " +
          "<b>Save &amp; overwrite the existing package</b> button to finish.<br>" +
          "<b>The AI will never click “Book the trip”.</b>");
    }, function () {
      clearState();
      say("Summary reached, but couldn’t spot <b>Save &amp; overwrite</b> by name.<br>" +
          scanSummary +
          "<small>Controls here: " + recordButtons(true) + "</small><br>" +
          "Click <b>Save and overwrite the existing package</b> by hand — never “Book the trip”.");
    }, 80, 400);
  }

  onReady(run);
})();
