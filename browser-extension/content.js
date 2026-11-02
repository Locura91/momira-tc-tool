// Momira TC Rollover — content script.
// Runs on every momira.travel page.
// On the idea page: reads #tcrollover=DD.MM.YYYY, saves date to sessionStorage,
//   clicks "Book directly without changes", waits for the date modal OR the
//   redirect to travel-map.xhtml, then fills the date.
// On travel-map.xhtml: reads the saved date from sessionStorage and fills it in.
(function () {
  "use strict";

  var STORAGE_KEY = "tcrollover_pending_date";
  var isIdea     = /\/idea\//.test(location.pathname);
  var isMap      = location.pathname.indexOf("travel-map") > -1;

  // ── On the idea page: read hash and save date ────────────────────────────────────────
  var DATE = null;
  if (isIdea) {
    var m = (location.hash || "").match(/tcrollover=([0-9]{2}\.[0-9]{2}\.[0-9]{4})/);
    if (!m) return;
    DATE = m[1];
    try { sessionStorage.setItem(STORAGE_KEY, DATE); } catch(e) {}
  }

  // ── On travel-map: restore saved date ───────────────────────────────────────
  if (isMap) {
    try { DATE = sessionStorage.getItem(STORAGE_KEY); } catch(e) {}
    if (!DATE) return;
  }

  if (!DATE) return;

  // ---- visible status overlay ---------------------------------------------
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;top:12px;right:12px;z-index:2147483647;" +
    "background:#111;color:#fff;font:13px/1.45 system-ui,Segoe UI,sans-serif;" +
    "padding:11px 14px;border-radius:9px;box-shadow:0 6px 20px rgba(0,0,0,.35);" +
    "max-width:340px;";
  function say(html) { box.innerHTML = "🔁 <b>TC Rollover</b><br>" + html; }
  function attach() {
    if (document.body && !box.isConnected) document.body.appendChild(box);
  }
  say("Target date: <b>" + DATE + "</b><br>Starting…");
  attach();

  // Native value setter — works for PrimeFaces/React-controlled inputs
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

  function findDateInput(root) {
    root = root || document;
    return root.querySelector('input[name*="departureDateMultidestination"]') ||
           root.querySelector('input[name*="startDate"]') ||
           root.querySelector('input[name*="departureDate"]') ||
           root.querySelector('input[id*="departureDate"]') ||
           root.querySelector('input[id*="startDate"]') ||
           root.querySelector('input[placeholder*="date" i]');
  }

  function buttonByText(needles, root) {
    root = root || document;
    var els = [].slice.call(
      root.querySelectorAll('button, a, input[type="submit"], span[role="button"]')
    );
    return els.find(function (b) {
      var t = (b.textContent || b.value || "").trim().toLowerCase();
      return needles.some(function (n) { return t.indexOf(n) > -1; });
    });
  }

  var BOOK = ["book directly without changes", "reserva directamente sin cambios",
              "book directly", "reservar directamente"];
  // GO: only non-generic terms — no "search" to avoid page-level search buttons
  var GO   = ["start trip", "iniciar viaje", "empezar viaje", "empezar"];

  // ── travel-map flow: date picker is on this page ─────────────────────────────────
  if (isMap) {
    say("On travel map. Looking for the departure date field…");
    var mapTries = 0;
    var mapIv = setInterval(function () {
      attach();
      var inp = findDateInput();
      if (inp) {
        clearInterval(mapIv);
        setValue(inp, DATE);
        say("Date set to <b>" + DATE + "</b> ✅<br>Review the trip, then confirm when ready.");
        try { sessionStorage.removeItem(STORAGE_KEY); } catch(e) {}
      } else if (++mapTries > 60) {
        clearInterval(mapIv);
        say("Couldn't find the date field on this page.<br>Please enter <b>" + DATE + "</b> manually.");
      }
    }, 250);
    return;
  }

  // ── idea page flow ──────────────────────────────────────────────────────────────────────
  // Correct order: set the departure date FIRST (field exists on the page before clicking),
  // then click “Book directly without changes”. TC uses the date that's already in the field
  // when the button is clicked — waiting for a modal after the click doesn't work because TC
  // redirects straight to travel-map.xhtml without showing a date picker modal.
  var tries = 0;
  var iv0 = setInterval(function () {
    attach();
    var inp = document.querySelector('input[name*=”departureDateMultidestination”]');
    var book = buttonByText(BOOK);
    if (inp && book) {
      clearInterval(iv0);
      say(“Setting departure date to <b>” + DATE + “</b>…”);
      setValue(inp, DATE);
      // Short pause so TC can process the date change before the button click.
      setTimeout(function () {
        say(“Date set ✅ — clicking “Book directly without changes”…”);
        book.click();
        // The rest (confirming the trip) is done by the human.
      }, 300);
    } else if (++tries > 60) {
      clearInterval(iv0);
      say(“Couldn't find the date field or “Book directly without changes” on this page.<br>” +
          “Make sure you're on the package idea page, then reload.”);
    }
  }, 200);
})();
