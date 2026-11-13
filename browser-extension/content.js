// Momira TC Rollover -- content script v8
// run_at: document_start -- saves date to sessionStorage BEFORE TC's own
// router reads the hash and potentially redirects to homepage.
//
// Click strategy: calls the button's onclick attribute string directly
// (new Function + call), which is the exact PrimeFaces JS TC registered.
// This avoids synthetic MouseEvent issues where PrimeFaces ignores the event.
(function () {
  "use strict";

  var STORAGE_KEY = "tcrollover_pending_date";

  // ── Save date immediately (document_start = no body yet) ─────────────────
  // This runs before TC's JavaScript, so the date is in sessionStorage even
  // if TC redirects the page away from the idea URL.
  var hash = location.hash || "";
  var earlyMatch = hash.match(/tcrollover=(\d{2}\.\d{2}\.\d{4})/);
  if (earlyMatch) {
    try { sessionStorage.setItem(STORAGE_KEY, earlyMatch[1]); } catch (e) {}
  }

  // ── Decide which date to use ──────────────────────────────────────────────
  var pathname = location.pathname;
  var isIdea = pathname.indexOf("/idea/") > -1;
  var DATE = null;

  if (isIdea) {
    if (earlyMatch) {
      DATE = earlyMatch[1];
    } else {
      try { DATE = sessionStorage.getItem(STORAGE_KEY); } catch (e) {}
    }
  } else {
    // Any other TC page (homepage, booking page, etc.) — read from storage
    try { DATE = sessionStorage.getItem(STORAGE_KEY); } catch (e) {}
  }

  if (!DATE) return;

  // ── Status overlay (body may not exist yet at document_start) ────────────
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;top:12px;right:12px;z-index:2147483647;" +
    "background:#111;color:#fff;font:11px/1.5 monospace;" +
    "padding:12px 16px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.5);" +
    "max-width:440px;min-width:260px;word-break:break-all;";

  function say(html) {
    box.innerHTML = "<b style='font-size:13px;font-family:sans-serif'>TC Rollover v8</b><br>" + html;
  }
  function attach() {
    if (document.body && !box.isConnected) document.body.appendChild(box);
  }

  // Attach as soon as body exists
  document.addEventListener("DOMContentLoaded", function () {
    attach();
    say("Date: <b>" + DATE + "</b><br>" + pathname.substring(0, 70));
  });

  // ── setValue for React / PrimeFaces inputs ───────────────────────────────
  function setValue(el, val) {
    try {
      var proto = Object.getPrototypeOf(el);
      var desc = Object.getOwnPropertyDescriptor(proto, "value") ||
                 Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
      if (desc && desc.set) desc.set.call(el, val);
      else el.value = val;
    } catch (e) { el.value = val; }
    el.value = val;
    ["focus", "input", "change", "keyup", "blur"].forEach(function (n) {
      el.dispatchEvent(new Event(n, { bubbles: true }));
    });
  }

  // ── Click a button: try onclick attribute first (PrimeFaces-safe) ────────
  function triggerButton(el) {
    var onclickAttr = el.getAttribute("onclick");
    if (onclickAttr) {
      try {
        // Execute TC's own onclick JavaScript — PrimeFaces.ab(...) or similar
        var fn = new Function("event", onclickAttr);
        var synEvt = new MouseEvent("click", { bubbles: true, cancelable: true });
        fn.call(el, synEvt);
        return "onclick-attr";
      } catch (e) { /* fall through */ }
    }
    // Fallback: dispatch event (no duplicate el.click())
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    el.dispatchEvent(new MouseEvent("mouseup",   { bubbles: true, cancelable: true }));
    el.dispatchEvent(new MouseEvent("click",     { bubbles: true, cancelable: true }));
    return "dispatchEvent";
  }

  // ── Find "Book directly without changes" ─────────────────────────────────
  var BOOK_TEXTS = [
    "book directly without changes",
    "reserva directamente sin cambios",
    "reservar directamente",
    "book directly"
  ];

  function findBookButton() {
    // ID-based (most reliable for PrimeFaces)
    var byId = document.querySelector("[id*='directBook']");
    if (byId) return byId;

    // Text match on interactive elements, shortest text = most specific
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
    // Inside PrimeFaces dialogs / booking widgets
    var containers = document.querySelectorAll(
      ".ui-dialog, [role='dialog'], .ui-panel, [class*='booking'], [class*='search-form']"
    );
    for (var c = 0; c < containers.length; c++) {
      var inp = containers[c].querySelector(
        "input[type='text'], input[type='date'], input:not([type='hidden'])"
      );
      if (inp) return inp;
    }
    return null;
  }

  function listInputs() {
    var inputs = document.querySelectorAll("input:not([type='hidden'])");
    var info = [];
    for (var i = 0; i < inputs.length && i < 10; i++) {
      var el = inputs[i];
      info.push((el.id || el.name || el.placeholder || el.type || "?").substring(0, 25));
    }
    return inputs.length + " inputs: " + info.join(" | ");
  }

  // ── Find Search button ───────────────────────────────────────────────────
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

  // ── Wait for DOM ready, then run the right flow ──────────────────────────
  function runWhenReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  // ── Non-idea page: arrived here after TC navigated from idea page ─────────
  if (!isIdea) {
    runWhenReady(function () {
      attach();
      say("Post-navigation page<br>" + pathname.substring(0, 70) +
          "<br>Looking for date field...<br><small>" + listInputs() + "</small>");
      setTimeout(step2, 2000);
    });
    return;
  }

  // ── Idea page: Step 1 — wait for button, click it ────────────────────────
  runWhenReady(function () {
    attach();
    say("Idea page — looking for Book button...");
    var s1tries = 0;
    var s1iv = setInterval(function () {
      attach();
      var btn = findBookButton();
      if (btn) {
        clearInterval(s1iv);
        var text = (btn.textContent || btn.value || "").trim().substring(0, 50);
        var href = (btn.href || btn.getAttribute("href") || "none").substring(0, 80);
        var id   = btn.id || "(no id)";
        say("Clicking button:<br>" +
            "<small>id=" + id + "<br>text=" + text + "<br>href=" + href + "</small>");
        var method = triggerButton(btn);
        setTimeout(function () {
          say("Triggered (" + method + ")<br>URL now: " + location.pathname.substring(0, 70) +
              "<br>Waiting for widget...");
          setTimeout(step2, 2500);
        }, 600);
      } else if (++s1tries > 100) {
        clearInterval(s1iv);
        var btns = [];
        document.querySelectorAll("a, button").forEach(function (el) {
          var t = (el.textContent || "").trim().substring(0, 40);
          if (t) btns.push(t);
        });
        say("Book button NOT found.<br><small>" + btns.slice(0, 10).join(" | ") + "</small>");
      }
    }, 200);
  });

  // ── Step 2 — find and fill date input ────────────────────────────────────
  function step2() {
    attach();
    say("Looking for date input...<br><small>" + listInputs() + "</small>");
    var s2tries = 0;
    var s2iv = setInterval(function () {
      attach();
      var inp = findDateInput();
      if (inp) {
        clearInterval(s2iv);
        var info = inp.tagName + "#" + (inp.id || inp.name || inp.placeholder || "?");
        say("Found: <small>" + info + "</small><br>Setting <b>" + DATE + "</b>...");
        inp.focus(); inp.click();
        setTimeout(function () {
          setValue(inp, DATE);
          say("Date set to <b>" + DATE + "</b>!<br>Clicking Search...");
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
        triggerButton(btn);
        say("Search clicked! TC loading...");
        try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
      } else if (++tries > 20) {
        clearInterval(iv);
        say("Date = <b>" + DATE + "</b>. Click Search manually.");
      }
    }, 300);
  }

})();
