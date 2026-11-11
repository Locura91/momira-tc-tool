/**
 * MultiWander AI Planner — widget.js v2.0.0
 * 3-prompt state machine with vague-input theme pills and final submission form.
 */
(function ($) {
    'use strict';

    // ── State ────────────────────────────────────────────────────────────────
    var state = {
        promptIndex: 0,       // 0 = not started, 1 = first prompt, 2 = second, 3 = form
        history: [],          // [{role, content}] for multi-turn context
        itinerary: '',        // accumulated itinerary text for email
        busy: false
    };

    var VAGUE_KEYWORDS = ['wakacje', 'urlop', 'wyjazd', 'nie wiem', 'coś fajnego', 'gdzieś', 'podróż'];

    var THEME_PILLS = [
        { label: '🏙️ City Break',       value: 'City Break — krótki wyjazd do europejskiego miasta' },
        { label: '🏝️ Island Hopping',   value: 'Island Hopping — skakanie między wyspami' },
        { label: '🏖️ City & Beach',     value: 'City & Beach — miasto i plaża' },
        { label: '🚗 Rental Car',        value: 'Rental Car Holiday — wynajem auta i wolna eksploracja' },
        { label: '🦁 Safari',            value: 'Safari — przyroda i dzikie zwierzęta' }
    ];

    // ── DOM refs ─────────────────────────────────────────────────────────────
    var $container, $window, $messages, $inputArea, $input, $sendBtn;

    $(function () {
        $container = $('#mw-ai-widget-container');
        $window    = $('#mw-ai-chat-window');
        $messages  = $('#mw-ai-messages');
        $inputArea = $('#mw-ai-input-area');
        $input     = $('#mw-ai-user-input');
        $sendBtn   = $('#mw-ai-send-btn');

        // Toggle open/close
        $('#mw-ai-toggle-btn').on('click', function () {
            $container.toggleClass('mw-ai-collapsed mw-ai-open');
            if ($container.hasClass('mw-ai-open')) {
                $input.focus();
            }
        });

        $('#mw-ai-close-btn').on('click', function () {
            $container.removeClass('mw-ai-open').addClass('mw-ai-collapsed');
        });

        // Send on button click or Enter
        $sendBtn.on('click', handleSend);
        $input.on('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
            }
        });
    });

    // ── Core send handler ────────────────────────────────────────────────────
    function handleSend() {
        if (state.busy) return;
        var text = $.trim($input.val());
        if (!text) return;

        $input.val('');

        if (state.promptIndex >= 3) return; // form phase — no more text inputs

        state.promptIndex++;

        appendMsg(text, 'user');

        // Vague input detection on prompt 1
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
        // No destination clue: very short with no country/region hint
        return lower.length < 15;
    }

    // ── Theme pills (vague input) ────────────────────────────────────────────
    function appendThemePills() {
        var html = '<div class="mw-msg bot-msg">Świetnie! Jakim stylem podróżowania jesteś zainteresowany?</div>';
        html += '<div class="mw-pills">';
        THEME_PILLS.forEach(function (pill) {
            html += '<button class="mw-pill" data-value="' + escAttr(pill.value) + '">' + escHtml(pill.label) + '</button>';
        });
        html += '</div>';
        var $block = $(html);
        $messages.append($block);
        scrollDown();

        $block.find('.mw-pill').on('click', function () {
            var val = $(this).data('value');
            $block.find('.mw-pills').remove();
            appendMsg(val, 'user');
            state.history.push({ role: 'user', content: val });
            callAI(val);
        });
    }

    // ── AI call ──────────────────────────────────────────────────────────────
    function callAI(userText) {
        state.busy = true;
        $sendBtn.prop('disabled', true).text('...');

        var $typing = $('<div class="mw-msg bot-msg mw-typing">✦ Planuję Twoją podróż…</div>');
        $messages.append($typing);
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
                $typing.remove();
                state.busy = false;
                $sendBtn.prop('disabled', false).text('Wyślij');

                if (!res.success) {
                    appendMsg('Przepraszamy, wystąpił błąd. Spróbuj ponownie.', 'bot');
                    return;
                }

                var reply = res.data.reply;

                // Accumulate itinerary text across both AI turns
                state.itinerary += (state.itinerary ? '\n\n' : '') + reply;

                // Push to history for next turn
                state.history.push({ role: 'user',      content: userText });
                state.history.push({ role: 'assistant', content: reply });

                appendMsg(reply, 'bot');

                if (state.promptIndex === 1) {
                    // After first reply prompt for refinement
                    appendMsg('Chcesz coś zmienić? Możesz doprecyzować (tempo, region, długość) — to Twój drugi komentarz.', 'bot');
                } else if (state.promptIndex >= 2) {
                    // After second reply show final form
                    appendSubmitForm();
                    hideInputArea();
                }
            },
            error: function () {
                $typing.remove();
                state.busy = false;
                $sendBtn.prop('disabled', false).text('Wyślij');
                appendMsg('Błąd połączenia. Sprawdź internet i spróbuj ponownie.', 'bot');
            }
        });
    }

    // ── Final submission form ────────────────────────────────────────────────
    function appendSubmitForm() {
        var today = new Date().toISOString().split('T')[0];

        var formHtml = '\
<div class="mw-form-block">\
  <p class="mw-form-intro">Gotowy plan! Podaj szczegóły, a wyślemy go na Twój email.</p>\
  <label>Data rozpoczęcia *\
    <input type="date" id="mw-f-date" min="' + today + '" required />\
  </label>\
  <label>Liczba podróżnych *\
    <input type="number" id="mw-f-passengers" min="1" max="20" value="2" required />\
  </label>\
  <label>Szacowany budżet (opcjonalnie)\
    <input type="text" id="mw-f-budget" placeholder="np. 3000 EUR / os." maxlength="60" />\
  </label>\
  <label>Adres email *\
    <input type="email" id="mw-f-email" placeholder="twoj@email.com" required />\
  </label>\
  <label>Uwaga do MultiWander <span class="mw-counter" id="mw-counter">0/50</span>\
    <input type="text" id="mw-f-note" maxlength="50" placeholder="np. preferujemy hotele 4★" />\
  </label>\
  <label class="mw-consent-label">\
    <input type="checkbox" id="mw-f-consent" />\
    Wyrażam zgodę na przesłanie mi szkicu planu przez MultiWander i kontakt z spersonalizowaną ofertą.\
  </label>\
  <div id="mw-form-error" class="mw-form-error" aria-live="polite"></div>\
  <button id="mw-submit-btn" class="mw-submit-btn" disabled>Wyślij plan ✈</button>\
</div>';

        var $form = $(formHtml);
        $messages.append($form);
        scrollDown();

        // Live note counter
        $form.find('#mw-f-note').on('input', function () {
            $('#mw-counter').text($(this).val().length + '/50');
        });

        // Enable submit only when email valid + consent checked
        function checkReady() {
            var email   = $.trim($form.find('#mw-f-email').val());
            var consent = $form.find('#mw-f-consent').is(':checked');
            var valid   = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) && consent;
            $form.find('#mw-submit-btn').prop('disabled', !valid);
        }
        $form.find('#mw-f-email, #mw-f-consent').on('input change', checkReady);

        // Submit
        $form.find('#mw-submit-btn').on('click', function () {
            var $btn      = $(this);
            var $errBox   = $form.find('#mw-form-error');
            var date      = $form.find('#mw-f-date').val();
            var passengers = $form.find('#mw-f-passengers').val();
            var budget    = $form.find('#mw-f-budget').val();
            var email     = $.trim($form.find('#mw-f-email').val());
            var note      = $form.find('#mw-f-note').val();

            $errBox.text('');

            if (!date) { $errBox.text('Wybierz datę rozpoczęcia.'); return; }

            $btn.prop('disabled', true).text('Wysyłam…');

            $.ajax({
                url: mwAI.ajax_url,
                method: 'POST',
                data: {
                    action:     'mw_ai_send_itinerary',
                    nonce:      mwAI.nonce,
                    email:      email,
                    start_date: date,
                    passengers: passengers,
                    budget:     budget,
                    note:       note,
                    itinerary:  state.itinerary
                },
                success: function (res) {
                    $form.remove();
                    if (res.success) {
                        appendMsg('✅ Gotowe! Wysłaliśmy plan na <strong>' + escHtml(email) + '</strong>. Nasz zespół odezwie się wkrótce z ofertą.', 'bot');
                    } else {
                        appendMsg('Wystąpił błąd podczas wysyłania: ' + escHtml(res.data), 'bot');
                    }
                },
                error: function () {
                    $btn.prop('disabled', false).text('Wyślij plan ✈');
                    $errBox.text('Błąd sieci. Spróbuj ponownie.');
                }
            });
        });
    }

    // ── Helpers ──────────────────────────────────────────────────────────────
    function appendMsg(text, role) {
        var cls = role === 'user' ? 'user-msg' : 'bot-msg';
        // Allow basic formatting from AI (line breaks). Escape everything else.
        var safe = escHtml(text).replace(/\n/g, '<br>');
        $messages.append('<div class="mw-msg ' + cls + '">' + safe + '</div>');
        scrollDown();
    }

    function hideInputArea() {
        $inputArea.hide();
    }

    function scrollDown() {
        $messages.scrollTop($messages[0].scrollHeight);
    }

    function escHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function escAttr(str) {
        return escHtml(str);
    }

}(jQuery));
