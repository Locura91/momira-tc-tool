/**
 * MultiWander AI Planner — widget.js v2.1.0
 */
(function ($) {
    'use strict';

    // ── State ────────────────────────────────────────────────────────────────
    var state = {
        promptIndex: 0,
        history: [],
        itinerary: '',
        busy: false,
        passengers: 2
    };

    var VAGUE_KEYWORDS = ['wakacje', 'urlop', 'wyjazd', 'nie wiem', 'coś fajnego', 'gdzieś', 'podróż', 'holidays', 'trip'];

    var THEME_PILLS = [
        { label: '🏙️ City Break',     value: 'City Break — krótki wyjazd do europejskiego miasta' },
        { label: '🏝️ Island Hopping', value: 'Island Hopping — skakanie między wyspami' },
        { label: '🏖️ City & Beach',   value: 'City & Beach — miasto i plaża' },
        { label: '🚗 Rental Car',      value: 'Rental Car Holiday — wynajem auta i wolna eksploracja' },
        { label: '🦁 Safari',          value: 'Safari — przyroda i dzikie zwierzęta' }
    ];

    var PAX_PRESETS = [1, 2, 3, 4];

    // ── DOM refs ─────────────────────────────────────────────────────────────
    var $container, $window, $messages, $inputArea, $input, $sendBtn;

    $(function () {
        $container = $('#mw-ai-widget-container');
        $window    = $('#mw-ai-chat-window');
        $messages  = $('#mw-ai-messages');
        $inputArea = $('#mw-ai-input-area');
        $input     = $('#mw-ai-user-input');
        $sendBtn   = $('#mw-ai-send-btn');

        $('#mw-ai-toggle-btn').on('click', openWidget);
        $('#mw-ai-close-btn').on('click', closeWidget);
        $sendBtn.on('click', handleSend);
        $input.on('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
        });
    });

    function openWidget() {
        $container.removeClass('mw-ai-collapsed').addClass('mw-ai-open');
        setTimeout(function () { $input.focus(); }, 60);
    }
    function closeWidget() {
        $container.removeClass('mw-ai-open').addClass('mw-ai-collapsed');
    }

    // ── Core send ────────────────────────────────────────────────────────────
    function handleSend() {
        if (state.busy || state.promptIndex >= 2) return;
        var text = $.trim($input.val());
        if (!text) return;
        $input.val('');

        state.promptIndex++;
        appendMsg(text, 'user');

        if (state.promptIndex === 1 && isVague(text)) {
            appendThemePills();
            return;
        }
        callAI(text);
    }

    function isVague(text) {
        var lower = text.toLowerCase();
        for (var i = 0; i < VAGUE_KEYWORDS.length; i++) {
            if (lower.indexOf(VAGUE_KEYWORDS[i]) !== -1 && lower.length < 30) return true;
        }
        return lower.length < 15;
    }

    // ── Theme pills ──────────────────────────────────────────────────────────
    function appendThemePills() {
        var $wrap = $('<div class="mw-msg bot-msg" style="max-width:100%"><p style="margin:0 0 8px">Jaki styl podróży Cię interesuje?</p></div>');
        var $pills = $('<div class="mw-pills"></div>');
        THEME_PILLS.forEach(function (p) {
            var $pill = $('<button class="mw-pill"></button>').text(p.label).data('value', p.value);
            $pill.on('click', function () {
                var val = $(this).data('value');
                $wrap.remove();
                appendMsg(val, 'user');
                state.history.push({ role: 'user', content: val });
                callAI(val);
            });
            $pills.append($pill);
        });
        $wrap.append($pills);
        $messages.append($wrap);
        scrollDown();
    }

    // ── AI call ──────────────────────────────────────────────────────────────
    function callAI(userText) {
        state.busy = true;
        $sendBtn.prop('disabled', true);

        var $dot = $('<div class="mw-msg bot-msg mw-typing"><span></span><span></span><span></span></div>');
        $messages.append($dot);
        scrollDown();

        $.ajax({
            url: mwAI.ajax_url,
            method: 'POST',
            data: {
                action:       'mw_ai_generate',
                nonce:        mwAI.nonce,
                message:      userText,
                prompt_index: state.promptIndex,
                history:      JSON.stringify(state.history)
            },
            success: function (res) {
                $dot.remove();
                state.busy = false;
                $sendBtn.prop('disabled', false);

                if (!res.success) {
                    appendMsg('Przepraszamy, wystąpił błąd. Spróbuj ponownie.', 'bot');
                    return;
                }

                var reply = res.data.reply;
                state.itinerary += (state.itinerary ? '\n\n' : '') + reply;
                state.history.push({ role: 'user', content: userText });
                state.history.push({ role: 'assistant', content: reply });

                appendMsg(reply, 'bot');

                if (state.promptIndex === 1) {
                    appendMsg('Chcesz coś zmienić? Możesz doprecyzować — to Twój drugi i ostatni komentarz.', 'bot');
                } else {
                    // prompt 2 done → show form
                    hideInputArea();
                    appendSubmitForm();
                }
            },
            error: function () {
                $dot.remove();
                state.busy = false;
                $sendBtn.prop('disabled', false);
                appendMsg('Błąd połączenia. Sprawdź internet i spróbuj ponownie.', 'bot');
            }
        });
    }

    // ── Submission form ──────────────────────────────────────────────────────
    function appendSubmitForm() {
        var today = new Date().toISOString().split('T')[0];

        var $form = $('\
<div class="mw-form-block">\
  <p class="mw-form-intro">✅ Świetnie! Uzupełnij szczegóły, a wyślemy plan na Twój email.</p>\
  <label>Data wyjazdu *\
    <input type="date" id="mw-f-date" min="' + today + '" required />\
  </label>\
  <label>Lotnisko wylotu *\
    <input type="text" id="mw-f-airport" placeholder="np. Warszawa (WAW), Kraków (KRK)…" maxlength="80" required />\
  </label>\
  <label>\
    <span class="mw-toggle-row">\
      <span class="mw-toggle-label">Loty w cenie oferty</span>\
      <label class="mw-toggle-switch">\
        <input type="checkbox" id="mw-f-flights" />\
        <span class="mw-slider"></span>\
      </label>\
    </span>\
  </label>\
  <label>Liczba podróżnych *\
    <div class="mw-pax-row" id="mw-pax-row"></div>\
  </label>\
  <label>Budżet <span class="mw-opt-label">(opcjonalnie)</span>\
    <input type="text" id="mw-f-budget" placeholder="np. 3 000 EUR / os." maxlength="60" />\
  </label>\
  <label>Email *\
    <input type="email" id="mw-f-email" placeholder="twoj@email.com" required />\
  </label>\
  <label>Uwaga do MultiWander <span class="mw-opt-label">(opcjonalnie)</span> <span class="mw-counter" id="mw-counter">0/50</span>\
    <input type="text" id="mw-f-note" maxlength="50" placeholder="np. preferujemy hotele 4★" />\
  </label>\
  <label class="mw-consent-label">\
    <input type="checkbox" id="mw-f-consent" />\
    Wyrażam zgodę na przesłanie mi planu podróży przez MultiWander i kontakt z ofertą.\
  </label>\
  <div id="mw-form-error" class="mw-form-error"></div>\
  <button id="mw-submit-btn" class="mw-submit-btn" disabled>Wyślij plan ✈</button>\
</div>');

        // Build passenger pills
        var $paxRow = $form.find('#mw-pax-row');
        PAX_PRESETS.forEach(function (n) {
            var label = n === 1 ? '1 os.' : n + ' os.';
            var $pill = $('<button class="mw-pax-pill" type="button"></button>').text(label).data('n', n);
            if (n === 2) { $pill.addClass('active'); state.passengers = 2; }
            $pill.on('click', function () {
                $paxRow.find('.mw-pax-pill').removeClass('active');
                $(this).addClass('active');
                state.passengers = $(this).data('n');
                $form.find('.mw-pax-custom').removeClass('visible').val('');
            });
            $paxRow.append($pill);
        });
        // "5+" custom
        var $more = $('<button class="mw-pax-pill" type="button">5+</button>');
        var $custom = $('<input type="number" class="mw-pax-custom" min="5" max="40" placeholder="5…" />');
        $more.on('click', function () {
            $paxRow.find('.mw-pax-pill').removeClass('active');
            $(this).addClass('active');
            $custom.addClass('visible').focus();
        });
        $custom.on('input', function () { state.passengers = parseInt($(this).val()) || 5; });
        $paxRow.append($more).append($custom);

        // Note counter
        $form.find('#mw-f-note').on('input', function () {
            $form.find('#mw-counter').text($(this).val().length + '/50');
        });

        // Enable submit gate
        function checkReady() {
            var email   = $.trim($form.find('#mw-f-email').val());
            var airport = $.trim($form.find('#mw-f-airport').val());
            var date    = $form.find('#mw-f-date').val();
            var consent = $form.find('#mw-f-consent').is(':checked');
            var valid   = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) && consent && airport && date;
            $form.find('#mw-submit-btn').prop('disabled', !valid);
        }
        $form.find('#mw-f-email, #mw-f-airport, #mw-f-date, #mw-f-consent').on('input change', checkReady);

        // Submit
        $form.find('#mw-submit-btn').on('click', function () {
            var $btn    = $(this);
            var $errBox = $form.find('#mw-form-error');
            var date    = $form.find('#mw-f-date').val();
            var airport = $.trim($form.find('#mw-f-airport').val());
            var flights = $form.find('#mw-f-flights').is(':checked') ? 'Tak' : 'Nie';
            var budget  = $.trim($form.find('#mw-f-budget').val());
            var email   = $.trim($form.find('#mw-f-email').val());
            var note    = $form.find('#mw-f-note').val();

            $errBox.text('');
            if (!date)    { $errBox.text('Wybierz datę wyjazdu.'); return; }
            if (!airport) { $errBox.text('Podaj lotnisko wylotu.'); return; }

            $btn.prop('disabled', true).text('Wysyłam…');

            $.ajax({
                url: mwAI.ajax_url,
                method: 'POST',
                data: {
                    action:      'mw_ai_send_itinerary',
                    nonce:       mwAI.nonce,
                    email:       email,
                    start_date:  date,
                    airport:     airport,
                    flights:     flights,
                    passengers:  state.passengers,
                    budget:      budget,
                    note:        note,
                    itinerary:   state.itinerary
                },
                success: function (res) {
                    $form.remove();
                    if (res.success) {
                        appendMsg('✅ Gotowe! Plan wysłany na <strong>' + escHtml(email) + '</strong>. Wkrótce się odezwiemy.', 'bot');
                        appendResetButton();
                    } else {
                        appendMsg('Błąd wysyłki: ' + escHtml(res.data), 'bot');
                        $btn.prop('disabled', false).text('Wyślij plan ✈');
                    }
                },
                error: function () {
                    $btn.prop('disabled', false).text('Wyślij plan ✈');
                    $errBox.text('Błąd sieci. Spróbuj ponownie.');
                }
            });
        });

        $messages.append($form);
        scrollDown();
    }

    // ── Reset ────────────────────────────────────────────────────────────────
    function appendResetButton() {
        var $btn = $('<button class="mw-reset-btn">↩ Zacznij od nowa</button>');
        $btn.on('click', function () {
            state = { promptIndex: 0, history: [], itinerary: '', busy: false, passengers: 2 };
            $messages.empty().append('<div class="mw-msg bot-msg">Opisz swoje wymarzone wakacje.</div>');
            showInputArea();
            $input.focus();
        });
        $messages.append($btn);
        scrollDown();
    }

    // ── Helpers ──────────────────────────────────────────────────────────────
    function appendMsg(html, role) {
        var cls  = role === 'user' ? 'user-msg' : 'bot-msg';
        var safe = (role === 'bot') ? html : escHtml(html).replace(/\n/g, '<br>');
        $messages.append('<div class="mw-msg ' + cls + '">' + safe + '</div>');
        scrollDown();
    }

    function hideInputArea() { $inputArea.hide(); }
    function showInputArea() { $inputArea.show(); }

    function scrollDown() {
        var el = $messages[0];
        el.scrollTop = el.scrollHeight;
    }

    function escHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

}(jQuery));
