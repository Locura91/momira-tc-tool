/**
 * MultiWander AI Planner — widget.js v2.1.0
 */
(function ($) {
    'use strict';

    // ── State ────────────────────────────────────────────────────────────────
    var EN = (mwAI.lang === 'en');

    var state = {
        promptIndex: 0,
        history: [],
        itinerary: '',
        busy: false,
        passengers: 2
    };

    var VAGUE_KEYWORDS = EN
        ? ['holiday', 'holidays', 'trip', 'travel', 'vacation', 'somewhere', 'dunno', 'not sure', 'anything']
        : ['wakacje', 'urlop', 'wyjazd', 'nie wiem', 'coś fajnego', 'gdzieś', 'podróż'];

    var THEME_PILLS = EN ? [
        { label: '🏙️ City Break',     value: 'City Break — short trip to a European city' },
        { label: '🏝️ Island Hopping', value: 'Island Hopping — jumping between islands' },
        { label: '🏖️ City & Beach',   value: 'City & Beach — city culture combined with the beach' },
        { label: '🚗 Rental Car',      value: 'Rental Car Holiday — freedom to explore by car' },
        { label: '🦁 Safari',          value: 'Safari — wildlife and nature' }
    ] : [
        { label: '🏙️ City Break',     value: 'City Break — krótki wyjazd do europejskiego miasta' },
        { label: '🏝️ Island Hopping', value: 'Island Hopping — skakanie między wyspami' },
        { label: '🏖️ City & Beach',   value: 'City & Beach — miasto i plaża' },
        { label: '🚗 Rental Car',      value: 'Rental Car Holiday — wynajem auta i wolna eksploracja' },
        { label: '🦁 Safari',          value: 'Safari — przyroda i dzikie zwierzęta' }
    ];

    var STR = {
        greeting:       EN ? 'Describe your dream holiday and I\'ll build your itinerary. 🌍'
                           : 'Opisz swoje wymarzone wakacje, a przygotuję plan. 🌍',
        pillPrompt:     EN ? 'What travel style interests you?'
                           : 'Jaki styl podróży Cię interesuje?',
        refineHint:     EN ? 'Want to change anything? This is your second and final comment.'
                           : 'Chcesz coś zmienić? Możesz doprecyzować — to Twój drugi i ostatni komentarz.',
        formIntro:      EN ? '✅ Great plan! Add your details and we\'ll email it to you.'
                           : '✅ Świetnie! Uzupełnij szczegóły, a wyślemy plan na Twój email.',
        labelDate:      EN ? 'Departure date *'      : 'Data wyjazdu *',
        labelAirport:   EN ? 'Departure airport *'   : 'Lotnisko wylotu *',
        labelFlights:   EN ? 'Flights included'      : 'Loty w cenie oferty',
        labelPax:       EN ? 'Number of travellers *': 'Liczba podróżnych *',
        labelBudget:    EN ? 'Budget'                : 'Budżet',
        labelEmail:     EN ? 'Email address *'       : 'Email *',
        labelNote:      EN ? 'Note to MultiWander'   : 'Uwaga do MultiWander',
        placeholderAirport: EN ? 'e.g. London (LHR), Manchester (MAN)…'
                               : 'np. Warszawa (WAW), Kraków (KRK)…',
        placeholderBudget:  EN ? 'e.g. €3,000 / person'
                               : 'np. 3 000 EUR / os.',
        placeholderNote:    EN ? 'e.g. prefer 4★ hotels'
                               : 'np. preferujemy hotele 4★',
        placeholderEmail:   EN ? 'your@email.com' : 'twoj@email.com',
        consent:        EN ? 'I agree to receive my itinerary draft from MultiWander and to be contacted with a personalised travel offer.'
                           : 'Wyrażam zgodę na przesłanie mi planu podróży przez MultiWander i kontakt z ofertą.',
        submitBtn:      EN ? 'Send itinerary ✈'  : 'Wyślij plan ✈',
        sending:        EN ? 'Sending…'           : 'Wysyłam…',
        successMsg:     EN ? '✅ Done! Your plan has been sent to '  : '✅ Gotowe! Plan wysłany na ',
        successSuffix:  EN ? '. We\'ll be in touch soon.'           : '. Wkrótce się odezwiemy.',
        resetBtn:       EN ? '↩ Start over'       : '↩ Zacznij od nowa',
        errDate:        EN ? 'Please select a departure date.'       : 'Wybierz datę wyjazdu.',
        errAirport:     EN ? 'Please enter your departure airport.'  : 'Podaj lotnisko wylotu.',
        errNet:         EN ? 'Connection error. Please try again.'   : 'Błąd sieci. Spróbuj ponownie.',
        errGeneric:     EN ? 'Sorry, an error occurred. Please try again.'
                           : 'Przepraszamy, wystąpił błąd. Spróbuj ponownie.',
        typingDots:     '',  // animated — no text needed
        optional:       EN ? '(optional)' : '(opcjonalnie)',
        sendBtnLabel:   EN ? 'Send' : 'Wyślij'
    };

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

        // Apply language strings to static DOM elements
        $input.attr('placeholder', EN
            ? 'e.g. 2 weeks in Egypt: pyramids and beach…'
            : 'np. 2 tygodnie w Egipcie: piramidy i plaża…');
        $sendBtn.text(STR.sendBtnLabel);
        $messages.find('.mw-msg.bot-msg').first().text(STR.greeting);

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
        var $wrap = $('<div class="mw-msg bot-msg" style="max-width:100%"><p style="margin:0 0 8px">' + escHtml(STR.pillPrompt) + '</p></div>');
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
                lang:         mwAI.lang,
                history:      JSON.stringify(state.history)
            },
            success: function (res) {
                $dot.remove();
                state.busy = false;
                $sendBtn.prop('disabled', false);

                if (!res.success) {
                    appendMsg(STR.errGeneric, 'bot');
                    return;
                }

                var reply = res.data.reply;
                state.itinerary += (state.itinerary ? '\n\n' : '') + reply;
                state.history.push({ role: 'user', content: userText });
                state.history.push({ role: 'assistant', content: reply });

                appendMsg(reply, 'bot');

                if (state.promptIndex === 1) {
                    appendMsg(STR.refineHint, 'bot');
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
                appendMsg(STR.errNet, 'bot');
            }
        });
    }

    // ── Submission form ──────────────────────────────────────────────────────
    function appendSubmitForm() {
        var today = new Date().toISOString().split('T')[0];

        var $form = $('\
<div class="mw-form-block">\
  <p class="mw-form-intro">' + escHtml(STR.formIntro) + '</p>\
  <label>' + escHtml(STR.labelDate) + '\
    <input type="date" id="mw-f-date" min="' + today + '" required />\
  </label>\
  <label>' + escHtml(STR.labelAirport) + '\
    <input type="text" id="mw-f-airport" placeholder="' + escHtml(STR.placeholderAirport) + '" maxlength="80" required />\
  </label>\
  <label>\
    <span class="mw-toggle-row">\
      <span class="mw-toggle-label">' + escHtml(STR.labelFlights) + '</span>\
      <label class="mw-toggle-switch">\
        <input type="checkbox" id="mw-f-flights" />\
        <span class="mw-slider"></span>\
      </label>\
    </span>\
  </label>\
  <label>' + escHtml(STR.labelPax) + '\
    <div class="mw-pax-row" id="mw-pax-row"></div>\
  </label>\
  <label>' + escHtml(STR.labelBudget) + ' <span class="mw-opt-label">' + escHtml(STR.optional) + '</span>\
    <input type="text" id="mw-f-budget" placeholder="' + escHtml(STR.placeholderBudget) + '" maxlength="60" />\
  </label>\
  <label>' + escHtml(STR.labelEmail) + '\
    <input type="email" id="mw-f-email" placeholder="' + escHtml(STR.placeholderEmail) + '" required />\
  </label>\
  <label>' + escHtml(STR.labelNote) + ' <span class="mw-opt-label">' + escHtml(STR.optional) + '</span> <span class="mw-counter" id="mw-counter">0/50</span>\
    <input type="text" id="mw-f-note" maxlength="50" placeholder="' + escHtml(STR.placeholderNote) + '" />\
  </label>\
  <label class="mw-consent-label">\
    <input type="checkbox" id="mw-f-consent" />\
    ' + escHtml(STR.consent) + '\
  </label>\
  <div id="mw-form-error" class="mw-form-error"></div>\
  <button id="mw-submit-btn" class="mw-submit-btn" disabled>' + escHtml(STR.submitBtn) + '</button>\
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
            if (!date)    { $errBox.text(STR.errDate);    return; }
            if (!airport) { $errBox.text(STR.errAirport); return; }

            $btn.prop('disabled', true).text(STR.sending);

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
                        appendMsg(STR.successMsg + '<strong>' + escHtml(email) + '</strong>' + STR.successSuffix, 'bot');
                        appendResetButton();
                    } else {
                        appendMsg(escHtml(res.data), 'bot');
                        $btn.prop('disabled', false).text(STR.submitBtn);
                    }
                },
                error: function () {
                    $btn.prop('disabled', false).text(STR.submitBtn);
                    $errBox.text(STR.errNet);
                }
            });
        });

        $messages.append($form);
        scrollDown();
    }

    // ── Reset ────────────────────────────────────────────────────────────────
    function appendResetButton() {
        var $btn = $('<button class="mw-reset-btn">' + escHtml(STR.resetBtn) + '</button>');
        $btn.on('click', function () {
            state = { promptIndex: 0, history: [], itinerary: '', busy: false, passengers: 2 };
            $messages.empty().append('<div class="mw-msg bot-msg">' + escHtml(STR.greeting) + '</div>');
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
