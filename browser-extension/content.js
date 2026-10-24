// Momira TC Rollover — content script.
//
// Runs on every momira.travel page but only ACTS when the URL carries a
// rollover instruction in its hash, e.g.
//     https://momira.travel/en/idea/56712448/some-slug#tcrollover=24.02.2027
// The Momira TC Tool appends that hash when you click "Start rollover", so
// the date travels with the page and this script can pick it up.
//
// Because this runs INSIDE the momira.travel page, in your own browser, it is
// already past Cloudflare and already logged in — the two walls that make
// server-side automation impossible. It drives exactly the clicks you would:
//   1. "Book directly without changes" / "Reserva directamente sin cambios"
//   2. fill the departure date
//   3. "Search" / "Start trip"  → lands on the trip summary to REVIEW.
//
// It deliberately STOPS at the summary/review page. It never clicks a final
// confirm/pay button — nothing is booked without you reviewing it first.
(function () {
  "use strict";

  var m = (location.hash || "").match(/tcrollover=([0-9]{2}\.[0-9]{2}\.[0-9]{4})/);
  if (!m) return;                       // not a rollover navigation — do nothing
  var DATE = m[1];

  // ---- visible status overlay so you can see what it's doing ----------------
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;top:12px;right:12px;z-index:2147483647;" +
    "background:#111;color:#fff;font:13px/1.45 system-ui,Segoe UI,sans-serif;" +
    "padding:11px 14px;border-radius:9px;box-shadow:0 6px 20px rgba(0,0,0,.35);" +
    "max-width:320px;";
  function say(html) { box.innerHTML = "🔁 <b>TC Rollover</b><br>" + html; }
  function attach() {
    if (document.body && !box.isConnected) document.body.appendChild(box);
  }
  say("Target date: <b>" + DATE + "</b><br>Starting…");
  attach();

  // Native value setter — works for plain, PrimeFaces and React-controlled
  // inputs (React ignores a plain .value= assignment; this bypasses that).
  function setValue(el, val) {
    try {
      var proto = Object.getPrototypeOf(el);
      var desc = Object.getOwnPropertyDescriptor(proto, "value") ||
                 Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
      if (desc && desc.set) desc.set.call(el, val); else el.value = val;
    } catch (e) { el.value = val; }
    ["input", "change", "keyup", "blur"].forEach(function (t) {
      el.dispatchEvent(new Event(t, { bubbles: true }));
    });
  }

  function dateInput() {
    return document.querySelector('input[name*="departureDateMultidestination"]') ||
           document.querySelector('input[name*="startDate"]') ||
           document.querySelector('input[name*="departureDate"]') ||
           document.querySelector('input[placeholder*="date" i]');
  }

  function buttonByText(needles) {
    var els = [].slice.call(
      document.querySelectorAll('button, a, input[type="submit"], span[role="button"]')
    );
    return els.find(function (b) {
      var t = (b.textContent || b.value || "").trim().toLowerCase();
      return needles.some(function (n) { return t.indexOf(n) > -1; });
    });
  }

  var BOOK = ["book directly without changes", "reserva directamente sin cambios",
              "book directly", "reservar directamente"];
  var GO   = ["start trip", "iniciar viaje", "search", "buscar", "empezar"];

  function fillAndGo() {
    var inp = dateInput();
    if (!inp) return false;
    attach();
    setValue(inp, DATE);
    say("Date set to <b>" + DATE + "</b>. Looking for the Start/Search button…");
    var tries = 0;
    var iv = setInterval(function () {
      var go = buttonByText(GO);
      if (go && !go.disabled) {
        clearInterval(iv);
        say("Starting the trip… you'll land on the summary to review. ✅");
        go.click();
        setTimeout(function () { if (box.isConnected) box.remove(); }, 7000);
      } else if (++tries > 40) {
        clearInterval(iv);
        say("Date is filled, but I couldn't find an enabled <b>Start/Search</b> button.<br>" +
            "Please click it yourself to continue.");
      }
    }, 250);
    return true;
  }

  // If the date modal is already open, fill it. Otherwise click "Book directly
  // without changes" first, then wait for the date field to appear.
  if (dateInput()) {
    fillAndGo();
  } else {
    var tries = 0;
    var iv0 = setInterval(function () {
      attach();
      var book = buttonByText(BOOK);
      if (book) {
        clearInterval(iv0);
        say("Opening “Book directly without changes”…");
        book.click();
        var t2 = 0;
        var iv1 = setInterval(function () {
          if (fillAndGo() || ++t2 > 60) clearInterval(iv1);
        }, 200);
      } else if (++tries > 60) {
        clearInterval(iv0);
        say("Couldn't find “Book directly without changes” on this page.<br>" +
            "Make sure this is the package's idea page, then reload.");
      }
    }, 200);
  }
})();
