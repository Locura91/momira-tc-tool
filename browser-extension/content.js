// Momira TC Rollover -- content script v9
// KEY FIX: runs in world: "MAIN" (the page's own JavaScript context).
// This means el.click() triggers PrimeFaces.ab() correctly because
// PrimeFaces exists in this world. Previous versions ran in Chrome's
// "isolated world" where PrimeFaces was undefined, so onclick handlers
// failed silently and the browser followed the <a href> to TC homepage.
(function () {
  "use strict";

  var STORAGE_KEY = "tcrollover_pending_date";

  // ── Save date immediately (document_start = before TC's router) ──────────
  var hash = location.hash || "";
  var earlyMatch = hash.match(/tcrollover=(\d{2}\.\d{2}\.\d{4})/);
  if (earlyMatch) {
    try { sessionStorage.setItem(STORAGE_KEY, earlyMatch[1]); } catch (e) {}
  }

  var pathname = location.pathname;
  var isIdea = pathname.indexOf("/idea/") > -1;
  var DATE = null;

  if (isIdea) {
    DATE = earlyMatch ? earlyMatch[1] : null;
    if (!DATE) { try { DATE = sessionStorage.getItem(STORAGE_KEY); } catch (e) {} }
  } else {
    try { DATE = sessionStorage.getItem(STORAGE_KEY); } catch (e) {}
  }

  if (!DATE) return;

  // ── Status overlay ───────────────────────────────────────────────────────
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;top:12px;right:12px;z-index:2147483647;" +
    "background:#111;color:#fff;font:11px/1.5 monospace;" +
    "padding:12px 16px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.5);" +
    "max-width:440px;min-width:260px;word-break:break-all;";

  function say(html) {
    box.innerHTML = "<b style='font-size:13px;font-family:sans-serif'>TC Rollover v9</b><br>" + html;
  }
  function attach() {
    if (document.body && !box.isConnected) document.body.appendChild(box);
  }

  document.addEventListener("DOMContentLoaded", function () {
    attach();
    say("Date: <b>" + DATE + "</b><br>" + pathname.substring(0, 70));
  });

  // ── Set value on PrimeFaces / React input ────────────────────────────────
  function setValue(el, val) {
    try {
      var desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value") ||
                 Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
      if (desc && desc.set) desc.set.call(el, val);
      else el.value = val;
    } catch (e) { el.value = val; }
    el.value = val;
    ["focus", "input", "change", "keyup", "blur"].forEach(function (n) {
      el.dispatchEvent(new Event(n, { bubbles: true }));
    });
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

  // ── Find departure date input ────────────────────────────────────────────
  function findDateInput() {
    var selectors = [
      "input[id*='departureDateMultidestination']",
      "input[name*='departureDateMultidestination']",
      "input[id*='departureDate']",
      "input[name*='departureDate']",
      "input[id*='startDate']",
      "input[name*='startDate']",
      "input[id*='travelDate']",
      "input[placeholder*='Departure' i]",
      "input[placeholder*='Start date' i]",
      "input[placeholder*='date' i]",
      "input[type='date']"
    ];
    for (var i = 0; i < selectors.length; i++) {
      var el = document.querySelector(selectors[i]);
      if (el) return el;
    }
    var containers = document.querySelectorAll(
      ".ui-dialog, [role='dialog'], .ui-panel, [class*='booking'], [class*='search-form']"
    );
    for (var c = 0; c < containers.length; c++) {
      var inp = containers[c].querySelector("input[type='text'], input[type='date'], input:not([type='hidden'])");
      if (inp) return inp;
    }
    return null;
  }

  function listInputs() {
    var inputs = document.querySelectorAll("input:not([type='hidden'])");
    var info = [];
    for (var i = 0; i < inputs.length && i < 10; i++) {
      var el = inputs[i];
      info.push((el.id || el.name || el.placeholder || el.type || "?").substring(0, 30));
    }
    return inputs.length + " inputs: " + info.join(" | ");
  }

  // ── Find Search / Start button ───────────────────────────────────────────
  var SEARCH_TEXTS = ["search", "buscar", "suchen", "start trip", "iniciar"];

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

  // ── Wait for DOM ─────────────────────────────────────────────────────────
  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  // ── NON-IDEA PAGE (TC navigated here after button click) ─────────────────
  if (!isIdea) {
    onReady(function () {
      attach();
      say("Post-navigation page<br>" + pathname.substring(0, 70) +
          "<br>Looking for date field...");
      setTimeout(step2, 2000);
    });
    return;
  }

  // ── IDEA PAGE: Step 1 — click "Book directly without changes" ────────────
  onReady(function () {
    attach();
    say("Idea page. Looking for Book button...");
    var s1tries = 0;
    var s1iv = setInterval(function () {
      attach();
      var btn = findBookButton();
      if (btn) {
        clearInterval(s1iv);
        var text = (btn.textContent || "").trim().substring(0, 50);
        var href = (btn.href || btn.getAttribute("href") || "none").substring(0, 80);
        var id = btn.id || "(no id)";
        say("CLICKING:<br>" +
            "<small>id=" + id + "<br>text=" + text + "<br>href=" + href + "</small>");
        // In world: "MAIN", el.click() runs in the page's JS context.
        // PrimeFaces.ab() is available, onclick handler works correctly,
        // the widget opens on the same page without navigation.
        btn.click();
        setTimeout(function () {
          say("Clicked (page-world). URL: " + location.pathname.substring(0, 70) +
              "<br>Waiting for widget...");
          setTimeout(step2, 2500);
        }, 600);
      } else if (++s1tries > 100) {
        clearInterval(s1iv);
        say("Book button NOT found on this page.");
      }
    }, 200);
  });

  // ── Step 2 — find and fill date ──────────────────────────────────────────
  function step2() {
    attach();
    say("Looking for date input...<br><small>" + listInputs() + "</small>");
    var s2tries = 0;
    var s2iv = setInterval(function () {
      attach();
      var inp = findDateInput();
      if (inp) {
        clearInterval(s2iv);
        var info = inp.tagName + "#" + (inp.id || inp.name || "?");
        say("Found: " + info + "<br>Setting <b>" + DATE + "</b>...");
        inp.focus(); inp.click();
        setTimeout(function () {
          setValue(inp, DATE);
          say("Date = <b>" + DATE + "</b>. Clicking Search...");
          setTimeout(step3, 800);
        }, 400);
      } else if (++s2tries > 60) {
        clearInterval(s2iv);
        say("Date field NOT found.<br><small>" + listInputs() + "</small><br>" +
            "Enter <b>" + DATE + "</b> manually.");
      }
    }, 300);
  }

  // ── Step 3 — click Search ────────────────────────────────────────────────
  function step3() {
    attach();
    var tries = 0;
    var iv = setInterval(function () {
      attach();
      var btn = findSearchButton();
      if (btn) {
        clearInterval(iv);
        btn.click();
        say("Search clicked! TC loading...");
        try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
      } else if (++tries > 20) {
        clearInterval(iv);
        say("Date = <b>" + DATE + "</b>. Click Search manually.");
      }
    }, 300);
  }

})();
