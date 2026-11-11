<?php
/**
 * Plugin Name: MultiWander AI Planner
 * Description: Interactive AI Travel Planner for multi-destination trip itineraries.
 * Version: 2.0.0
 * Author: MultiWander
 */

if (!defined('ABSPATH')) exit;

class MultiWander_AI_Planner {

    public function __construct() {
        add_action('wp_enqueue_scripts', array($this, 'enqueue_assets'));
        add_action('wp_footer', array($this, 'render_widget'));
        add_action('wp_ajax_mw_ai_generate', array($this, 'handle_ai_request'));
        add_action('wp_ajax_nopriv_mw_ai_generate', array($this, 'handle_ai_request'));
        add_action('wp_ajax_mw_ai_send_itinerary', array($this, 'handle_send_itinerary'));
        add_action('wp_ajax_nopriv_mw_ai_send_itinerary', array($this, 'handle_send_itinerary'));
        add_action('admin_menu', array($this, 'register_admin_menu'));
        add_action('admin_init', array($this, 'register_settings'));
    }

    public function register_admin_menu() {
        add_options_page(
            'MultiWander AI Planner',
            'MultiWander AI',
            'manage_options',
            'mw-ai-planner',
            array($this, 'render_admin_page')
        );
    }

    public function register_settings() {
        register_setting('mw_ai_settings', 'mw_openai_api_key',   array('sanitize_callback' => 'sanitize_text_field'));
        register_setting('mw_ai_settings', 'mw_openai_model',      array('sanitize_callback' => 'sanitize_text_field', 'default' => 'gpt-4o-mini'));
        register_setting('mw_ai_settings', 'mw_openai_temperature', array('sanitize_callback' => 'floatval', 'default' => 0.7));
    }

    public function render_admin_page() {
        if (!current_user_can('manage_options')) return;
        $saved = get_settings_errors('mw_ai_settings');
        ?>
        <div class="wrap">
            <h1 style="display:flex;align-items:center;gap:10px;">
                <span style="font-size:24px;">✈️</span> MultiWander AI Planner — Ustawienia
            </h1>

            <?php settings_errors('mw_ai_settings'); ?>

            <form method="post" action="options.php">
                <?php settings_fields('mw_ai_settings'); ?>

                <table class="form-table" role="presentation">

                    <tr>
                        <th scope="row"><label for="mw_openai_api_key">Klucz API OpenAI</label></th>
                        <td>
                            <?php $key = get_option('mw_openai_api_key', ''); ?>
                            <input
                                type="password"
                                id="mw_openai_api_key"
                                name="mw_openai_api_key"
                                value="<?php echo esc_attr($key); ?>"
                                class="regular-text"
                                autocomplete="new-password"
                                placeholder="sk-..."
                            />
                            <p class="description">
                                Wklej swój klucz z <a href="https://platform.openai.com/api-keys" target="_blank">platform.openai.com/api-keys</a>.
                                Klucz jest przechowywany w bazie danych WordPress.
                            </p>
                            <?php if (!empty($key)): ?>
                                <p style="color:#3dbf8e;font-weight:600;">✔ Klucz jest zapisany.</p>
                            <?php endif; ?>
                        </td>
                    </tr>

                    <tr>
                        <th scope="row"><label for="mw_openai_model">Model OpenAI</label></th>
                        <td>
                            <?php $model = get_option('mw_openai_model', 'gpt-4o-mini'); ?>
                            <select id="mw_openai_model" name="mw_openai_model">
                                <option value="gpt-4o-mini"  <?php selected($model, 'gpt-4o-mini'); ?>>gpt-4o-mini (szybki, tani)</option>
                                <option value="gpt-4o"       <?php selected($model, 'gpt-4o'); ?>>gpt-4o (najlepsza jakość)</option>
                                <option value="gpt-3.5-turbo"<?php selected($model, 'gpt-3.5-turbo'); ?>>gpt-3.5-turbo (najszybszy)</option>
                            </select>
                        </td>
                    </tr>

                    <tr>
                        <th scope="row"><label for="mw_openai_temperature">Kreatywność (temperature)</label></th>
                        <td>
                            <?php $temp = get_option('mw_openai_temperature', 0.7); ?>
                            <input
                                type="range"
                                id="mw_openai_temperature"
                                name="mw_openai_temperature"
                                min="0" max="1" step="0.1"
                                value="<?php echo esc_attr($temp); ?>"
                                oninput="document.getElementById('mw_temp_val').textContent=this.value"
                                style="width:200px;vertical-align:middle;"
                            />
                            &nbsp;<strong id="mw_temp_val"><?php echo esc_html($temp); ?></strong>
                            <p class="description">0 = odpowiedzi deterministyczne, 1 = bardzo kreatywne. Zalecane: 0.7</p>
                        </td>
                    </tr>

                </table>

                <?php submit_button('Zapisz ustawienia'); ?>
            </form>
        </div>
        <?php
    }

    public function enqueue_assets() {
        wp_enqueue_style('mw-ai-style', plugin_dir_url(__FILE__) . 'assets/css/widget.css', array(), '2.0.0');
        wp_enqueue_script('mw-ai-script', plugin_dir_url(__FILE__) . 'assets/js/widget.js', array('jquery'), '2.0.0', true);

        wp_localize_script('mw-ai-script', 'mwAI', array(
            'ajax_url' => admin_url('admin-ajax.php'),
            'logo_url' => plugin_dir_url(__FILE__) . 'assets/logo.png',
            'nonce'    => wp_create_nonce('mw_ai_nonce')
        ));
    }

    public function render_widget() {
        ?>
        <div id="mw-ai-widget-container" class="mw-ai-collapsed">
            <button id="mw-ai-toggle-btn" aria-label="Otwórz planer podróży">
                <span class="mw-orb-label">Zaplanuj podróż ✈</span>
            </button>
            <div id="mw-ai-chat-window" role="dialog" aria-label="AI Travel Planner">
                <div class="mw-ai-header">
                    <img src="<?php echo esc_url(plugin_dir_url(__FILE__) . 'assets/logo.png'); ?>" alt="MultiWander" class="mw-ai-logo" />
                    <button id="mw-ai-close-btn" aria-label="Zamknij">&times;</button>
                </div>
                <div id="mw-ai-messages" aria-live="polite">
                    <div class="mw-msg bot-msg">Opisz swoje wymarzone wakacje, a przygotuję plan. 🌍</div>
                </div>
                <div id="mw-ai-input-area">
                    <input type="text" id="mw-ai-user-input" placeholder="np. 2 tygodnie w Egipcie: piramidy i plaża..." autocomplete="off" maxlength="400" />
                    <button id="mw-ai-send-btn">Wyślij</button>
                </div>
            </div>
        </div>
        <?php
    }

    public function handle_ai_request() {
        check_ajax_referer('mw_ai_nonce', 'nonce');

        $user_message  = sanitize_text_field($_POST['message'] ?? '');
        $prompt_index  = intval($_POST['prompt_index'] ?? 1);
        $history_raw   = $_POST['history'] ?? '[]';
        $history       = json_decode(stripslashes($history_raw), true);
        if (!is_array($history)) $history = array();

        $api_key     = get_option('mw_openai_api_key');
        $model       = get_option('mw_openai_model', 'gpt-4o-mini');
        $temperature = floatval(get_option('mw_openai_temperature', 0.7));

        if (empty($api_key)) {
            wp_send_json_error('Brak skonfigurowanego klucza API OpenAI. Przejdź do Ustawienia → MultiWander AI.');
        }

        // Build messages array for OpenAI
        $messages = array(
            array('role' => 'system', 'content' => $this->get_system_prompt($prompt_index))
        );

        // Append prior turns from frontend history
        foreach ($history as $turn) {
            $role    = ($turn['role'] === 'user') ? 'user' : 'assistant';
            $content = sanitize_textarea_field($turn['content'] ?? '');
            if (!empty($content)) {
                $messages[] = array('role' => $role, 'content' => $content);
            }
        }

        // Current user message
        $messages[] = array('role' => 'user', 'content' => $user_message);

        $response = wp_remote_post('https://api.openai.com/v1/chat/completions', array(
            'headers' => array(
                'Authorization' => 'Bearer ' . $api_key,
                'Content-Type'  => 'application/json'
            ),
            'body' => json_encode(array(
                'model'       => $model,
                'messages'    => $messages,
                'temperature' => $temperature
            )),
            'timeout' => 25
        ));

        if (is_wp_error($response)) {
            wp_send_json_error('Błąd połączenia z OpenAI.');
        }

        $body  = json_decode(wp_remote_retrieve_body($response), true);
        $reply = $body['choices'][0]['message']['content'] ?? 'Przepraszamy, wystąpił problem z wygenerowaniem planu.';

        wp_send_json_success(array('reply' => $reply));
    }

    private function get_system_prompt(int $prompt_index): string {
        $base = "Jesteś ekspertem ds. planowania podróży dla MultiWander (multiwander.com). Specjalizujesz się w łączonych podróżach multi-destination dla polskich klientów.

ZASADY BEZWZGLĘDNE:
1. MATEMATYKA CZASU POBYTU:
   - '2 tygodnie' = dokładnie 14 dni / 13 nocy.
   - '1 tydzień' = dokładnie 7 dni / 6 nocy.
   - 'X nocy' = X nocy / X+1 dni.
   - Zawsze przeliczaj i wypisuj dokładną liczbę dni i nocy.

2. MULTI-DESTINATION USP:
   - Nigdy nie proponuj jednego miejsca na cały wyjazd. Zawsze dziel na minimum 2 różne cele/regiony.
   - Egipt → Kair + Hurghada lub rejs po Nilu. Hiszpania → Barcelona/Madryt + Andaluzja lub Costa.
   - Grecja → Ateny + jedna wyspa. Turcja → Stambuł + riwiera lub Kapadocja.

3. FORMATOWANIE PLANU:
   - Grupuj po dniach: 'Day 1 Kair', 'Day 2 Kair', 'Day 3 Hurghada' itd.
   - Do każdego dnia MAKSYMALNIE 1 krótka linia z rekomendacją atrakcji/zwiedzania.
   - ZAKAZ: Żadnych restauracji, barów, kawiarni, jedzenia. Wyłącznie zwiedzanie i atrakcje.

4. VAGUE INPUT:
   - Jeśli użytkownik nie podał konkretnego miejsca, zapytaj o preferowany klimat lub zaproponuj style podróżowania.

Odpowiadaj po polsku. Bądź zwięzły.";

        if ($prompt_index === 2) {
            $base .= "\n\nTo jest druga wiadomość użytkownika — doprecyzowanie lub zmiana planu. Dostosuj plan na podstawie poprzedniej rozmowy. Zachowaj format dzienny.";
        }

        return $base;
    }

    public function handle_send_itinerary() {
        check_ajax_referer('mw_ai_nonce', 'nonce');

        $email      = sanitize_email($_POST['email'] ?? '');
        $start_date = sanitize_text_field($_POST['start_date'] ?? '');
        $airport    = sanitize_text_field($_POST['airport'] ?? '');
        $flights    = sanitize_text_field($_POST['flights'] ?? 'Nie');
        $passengers = intval($_POST['passengers'] ?? 2);
        $budget     = sanitize_text_field($_POST['budget'] ?? '');
        $note       = sanitize_text_field(substr($_POST['note'] ?? '', 0, 50));
        $itinerary  = wp_kses_post($_POST['itinerary'] ?? '');

        if (!is_email($email)) {
            wp_send_json_error('Podano niepoprawny adres email.');
        }

        if (empty($start_date) || empty($airport) || empty($itinerary)) {
            wp_send_json_error('Brakuje wymaganych danych.');
        }

        $logo_url    = plugin_dir_url(__FILE__) . 'assets/logo.png';
        $budget_str  = !empty($budget) ? esc_html($budget) : 'Nieokreślony';
        $note_line   = !empty($note)   ? '<li><strong>Uwaga:</strong> ' . esc_html($note) . '</li>' : '';

        $email_html = '
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 10px; overflow: hidden;">
            <div style="background: linear-gradient(135deg,#0d5c63,#0a7a6a); padding: 22px; text-align: center;">
                <img src="' . esc_url($logo_url) . '" alt="MultiWander" style="max-width: 150px; height: auto;" />
            </div>
            <div style="padding: 26px;">
                <h2 style="color: #0d5c63; text-align: center; margin-top: 0;">Twoja Propozycja Podróży Łączonej</h2>
                <p style="color:#444;">Dziękujemy za skorzystanie z AI Plannera <strong>MultiWander</strong>! Nasz zespół skontaktuje się z Tobą z gotową ofertą.</p>
                <hr style="border: none; border-top: 1px solid #eeeeee; margin: 18px 0;" />
                <h3 style="color: #0d5c63; margin-bottom: 10px;">Szczegóły zapytania</h3>
                <ul style="line-height: 2; padding-left: 18px; color: #333;">
                    <li><strong>Data wyjazdu:</strong> ' . esc_html($start_date) . '</li>
                    <li><strong>Lotnisko wylotu:</strong> ' . esc_html($airport) . '</li>
                    <li><strong>Loty w cenie:</strong> ' . esc_html($flights) . '</li>
                    <li><strong>Liczba podróżnych:</strong> ' . intval($passengers) . '</li>
                    <li><strong>Szacowany budżet:</strong> ' . $budget_str . '</li>
                    ' . $note_line . '
                </ul>
                <hr style="border: none; border-top: 1px solid #eeeeee; margin: 18px 0;" />
                <h3 style="color: #0d5c63; margin-bottom: 10px;">Zarys planu podróży</h3>
                <div style="background: #f4fafa; padding: 16px; border-radius: 8px; font-size: 14px; line-height: 1.75; color: #222; white-space: pre-wrap;">'
                    . nl2br(esc_html($itinerary)) .
                '</div>
                <br />
                <p style="font-size: 13px; color: #666;">Odezwiemy się wkrótce z gotową ofertą!</p>
                <p style="text-align: center; font-size: 12px; color: #aaa; margin-top: 20px;">© MultiWander — Travel Combinations Specialist</p>
            </div>
        </div>';

        $headers = array(
            'Content-Type: text/html; charset=UTF-8',
            'From: MultiWander <info@multiwander.com>',
            'Reply-To: info@multiwander.com'
        );

        $subject_client = 'Twój wygenerowany plan podróży — MultiWander';
        $subject_lead   = '[Nowy Lead AI] Plan dla ' . $email . ' | start: ' . $start_date . ' | ' . $passengers . ' os. | lot: ' . $flights . ' | ' . $airport;

        wp_mail($email, $subject_client, $email_html, $headers);
        wp_mail('info@multiwander.com', $subject_lead, $email_html, $headers);

        wp_send_json_success('Wiadomość została wysłana!');
    }
}

new MultiWander_AI_Planner();
