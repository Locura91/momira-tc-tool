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

  // ── On the idea page: read hash and save date ────────────────────────────
  var DATE = null;
  if (isIdea) {
    var m = (location.hash || "").match(/tcrollover=([0-9]{2}\.[0-9]{2}\.[0-9]{4})/);
    if (!m) return;
    DATE = m[1];
    try { sessionStorage.setItem(STORAGE_KEY, DATE); } catch(e) {}
  }

  // ── On travel-map: restore saved date ───────────────────────────────────
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

  // ── travel-map flow: date picker is on this page ─────────────────────────
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
        // Look for a Save / Confirm / Search button on the map page
        var saveNames = ["save", "guardar", "confirm", "confirmar", "update", "actualizar",
                         "start trip", "iniciar"];
        var saveTries = 0;
        var saveIv = setInterval(function () {
          var btn = buttonByText(saveNames);
          if (btn && !btn.disabled) {
            clearInterval(saveIv);
            say("Date set ✅ — click <b>Save / Confirm</b> yourself to finalise, or it may auto-save.");
          } else if (++saveTries > 30) {
            clearInterval(saveIv);
          }
        }, 300);
      } else if (++mapTries > 60) {
        clearInterval(mapIv);
        say("Couldn't find the date field on this page.<br>Please enter <b>" + DATE + "</b> manually.");
      }
    }, 250);
    return;
  }

  // ── idea page flow ────────────────────────────────────────────────────────
  // Click BOOK first; TC may open a modal with a date picker OR redirect to travel-map.
  var tries = 0;
  var iv0 = setInterval(function () {
    attach();
    var book = buttonByText(BOOK);
    if (book) {
      clearInterval(iv0);
      say("Clicking “Book directly without changes”…");
      book.click();
      // Wait briefly for a modal date field — if TC redirects instead, the
      // travel-map handler above will take over on the new page.
      var t2 = 0;
      var iv1 = setInterval(function () {
        var modal = document.querySelector('form[id*="mySeparateForm"]') ||
                    document.querySelector('div[id*="mySeparateForm"]') ||
                    document.querySelector('.ui-dialog:not([style*="display:none"]):not([style*="display: none"])');
        var inp = findDateInput(modal || undefined);
        if (inp) {
          clearInterval(iv1);
          setValue(inp, DATE);
          say("Date set to <b>" + DATE + "</b>. Looking for Start Trip…");
          var goTries = 0;
          var iv2 = setInterval(function () {
            var go = buttonByText(GO, modal || undefined) || buttonByText(GO);
            if (go && !go.disabled) {
              clearInterval(iv2);
              say("Starting the trip… you'll land on the summary to review. ✅");
              go.click();
              setTimeout(function () { if (box.isConnected) box.remove(); }, 7000);
            } else if (++goTries > 40) {
              clearInterval(iv2);
              say("Date filled ✅<br>Please click <b>Start Trip</b> yourself to continue.");
            }
          }, 250);
        } else if (++t2 > 30) {
          // No modal appeared in 6 s — TC likely redirected to travel-map already.
          // The travel-map handler will take over. Just stop this interval.
          clearInterval(iv1);
          say("Waiting for Travel Compositor to load the date page…");
        }
      }, 200);
    } else if (++tries > 60) {
      clearInterval(iv0);
      say("Couldn't find “Book directly without changes” on this page.<br>" +
          "Make sure you're on the package idea page, then reload.");
    }
  }, 200);
})();
