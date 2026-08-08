"""
translation_tool.py — the Translation Sync tool, as it lives inside the
Momira Travel Platform.

Originally a standalone Streamlit app (momira-translation-sync/
streamlit_app.py). Merged in here as a single render_translation_tool()
function so the platform can offer it alongside the contract Upload & Update
tool. The sync engines themselves (translator.py, state_store.py,
sync_*.py, travelcompositor_api.py) were imported unchanged - only the UI
layer was rewritten, so any fix made to a sync engine upstream can still be
dropped in without touching this file.

WHAT CHANGED vs the standalone app, and why:
  * No st.set_page_config()/st.title() - the platform owns the page shell;
    a second set_page_config() call would raise at runtime.
  * The missing-secrets check no longer calls st.stop() at import time. In a
    standalone app that just ended that app; inside the platform it would
    have killed the WHOLE platform - including the Upload & Update tool,
    which doesn't need the translation keys at all. It's now a scoped error
    shown inside this tool only.
  * Settings moved out of st.sidebar into the main area, matching the
    step-by-step wizard style every other tool in the platform uses.
  * All session-state keys are tr_-prefixed so nothing collides with the
    ClosedTour/Ticket/Transfer/Transport/Hotel flows in app.py.
  * The language-mode label is now generated from the actual list length
    (it said "All 30 target languages" while the list had already been cut
    to 19).

NOTE ON THE TWO API CLIENTS: this tool talks to Travel Compositor through
travelcompositor_api.TravelCompositorAPI, while the Upload & Update tool
uses api_client.TravelCompositorAPI. Two different classes that happen to
share a name - imported here under an explicit alias so that's obvious.
They're kept separate deliberately: the sync engines were written against
their own client and re-pointing them is a refactor with real regression
risk for zero user-visible gain.
"""
import os
import streamlit as st

from travelcompositor_api import TravelCompositorAPI as TranslationTCAPI
from translator import get_translator, required_api_key_env_var
from state_store import StateStore

from sync_holiday_package import sync_holiday_package, sync_all_holiday_packages
from sync_ticket import (
    sync_ticket,
    sync_ticket_from_data,
    sync_all_options_for_ticket_from_data,
    fetch_all_tickets,
)
from sync_transfer import (
    sync_transfer,
    sync_transfer_from_data,
    fetch_all_transfers,
)
from sync_transport import (
    sync_transport,
    sync_transport_from_data,
    sync_all_options_for_transport_from_data,
    fetch_all_transports,
)
from sync_hotel import sync_hotel, fetch_all_hotels
from sync_closed_tour import sync_closed_tour


# Reduced from 30 to 19 target languages per the product owner: removed
# Albanian (SQ), Arabic (AR), Azerbaijani (AZ), Georgian (KA), Japanese (JA),
# Croatian (HR), Malay (MS), Serbian (SR), Thai (TH), Uzbek (UZ) and
# Bulgarian (BG). Applies to every entity type, since they all share this
# one list. Persian/Farsi was already absent before that change.
DEFAULT_TARGET_LANGUAGES = [
    "FR", "SL", "PL", "DE", "SK", "HU", "NL", "ES", "TR",
    "RU", "NO", "SV", "RO", "CS", "EL", "FI",
    "PT", "DA", "IT",
]

# Entity types this tool can translate. Note these are the things ALREADY
# LIVE in Travel Compositor - which is why the list differs from the Upload
# & Update tool's product types: Holiday Packages are assembled inside
# Travel Compositor rather than uploaded from a supplier contract, so they
# can be translated but never uploaded by us.
ENTITY_TYPES = ["Holiday Packages", "Tickets", "Transfers", "Transports", "Hotels", "Closed Tours"]


@st.cache_data(ttl=300)
def _fetch_translation_suppliers():
    """Supplier list for the translation tool, cached for 5 minutes.

    Deliberately NOT filtered to 'Momira_' suppliers the way the Upload &
    Update tool filters them: we only ever CREATE products under our own
    supplier accounts, but we may well need to translate content that
    already exists under any supplier."""
    try:
        api = TranslationTCAPI()
        suppliers = api.get_all_suppliers()
        if not suppliers:
            return []
        suppliers_sorted = sorted(suppliers, key=lambda s: (s.get("commercialName") or "").lower())
        return [(s["id"], s.get("commercialName", f"Supplier {s['id']}")) for s in suppliers_sorted]
    except Exception as e:
        st.warning(f"Could not fetch suppliers: {e}")
        return []


def _supplier_picker(key_prefix):
    """Shared supplier chooser - dropdown when the list loads, manual entry as a fallback."""
    suppliers = _fetch_translation_suppliers()
    if suppliers:
        supplier_options = {name: sid for sid, name in suppliers}
        selected_name = st.selectbox("Select Supplier", options=list(supplier_options.keys()),
                                      key=f"{key_prefix}_supplier_select")
        supplier_id = str(supplier_options[selected_name])
        st.caption(f"Using supplier ID: {supplier_id}")
        return supplier_id
    supplier_id = st.text_input("Supplier ID (numeric)", value=os.getenv("TRAVELC_SUPPLIER_ID", ""),
                                 key=f"{key_prefix}_supplier_manual")
    if not supplier_id:
        st.warning("Please enter a supplier ID.")
    return supplier_id


def _scope_picker(entity_label, specific_label, example, key_prefix):
    """Shared 'all of them vs one specific one' chooser + optional limit.
    Returns (scope_is_all, specific_value, limit).

    `example` is shown as the field's PLACEHOLDER (greyed text inside the empty box) rather
    than as a help tooltip: Streamlit renders help= as a small "?" icon that has to be hovered
    to reveal, so the one piece of information a human actually needs here - what the code is
    supposed to look like - was hidden behind an extra interaction."""
    scope = st.radio(f"Which {entity_label}?", [f"All {entity_label}", specific_label], key=f"{key_prefix}_scope")
    is_all = scope.startswith("All")
    specific_value = None
    limit = None
    if is_all:
        limit_input = st.number_input(f"Limit to first N {entity_label} (0 = no limit)", min_value=0, value=5,
                                       key=f"{key_prefix}_limit")
        limit = limit_input or None
    else:
        specific_value = st.text_input(specific_label, placeholder=example, key=f"{key_prefix}_specific")
    return is_all, specific_value, limit


def render_translation_tool():
    """
    Translation Sync tool. Takes products that ALREADY exist in Travel
    Compositor and fills in their other-language content automatically,
    tracking what's been done so re-running is safe and cheap.
    """
    st.header("Translate — Step 2: What do you want to translate?")
    st.caption("This tool works on products that are already live in Travel Compositor. It reads their "
              "English content, translates it, and writes the translations back. To create or update a "
              "product from a supplier contract instead, switch to Upload & Update.")

    # --- Credentials check, scoped to this tool only ---
    missing = [
        k for k in (required_api_key_env_var(), "TRAVELC_USERNAME", "TRAVELC_PASSWORD")
        if not os.getenv(k)
    ]
    if missing:
        st.error(f"🚫 The Translation tool can't run - missing secret(s): **{', '.join(missing)}**. "
                 f"Add them in Streamlit Cloud Secrets or your local .env, then reload. "
                 f"(The Upload & Update tool is unaffected and still works.)")
        return

    entity_type = st.radio("Entity type", ENTITY_TYPES, key="tr_entity_type", horizontal=True)

    st.header("Translate — Step 3: Scope")

    supplier_id = None
    microsite_id = None
    package_id = ticket_code = transfer_id = transport_id = provider_code = closed_tour_code = None
    is_all = False
    limit = None

    if entity_type == "Holiday Packages":
        microsite_id = st.text_input("Microsite ID", value=os.getenv("TRAVELC_MICROSITE_ID", "momiratravel"),
                                      key="tr_microsite")
        is_all, package_id, limit = _scope_picker("packages", "One specific package ID",
                                                   "e.g. 59984696", "tr_hp")

    elif entity_type == "Tickets":
        supplier_id = _supplier_picker("tr_tk")
        is_all, ticket_code, limit = _scope_picker("tickets", "One specific ticket code",
                                                    "e.g. JAP-T1", "tr_tk")

    elif entity_type == "Transfers":
        supplier_id = _supplier_picker("tr_tf")
        is_all, transfer_id, limit = _scope_picker("transfers", "One specific transfer ID",
                                                    "e.g. TRANSFER-412566", "tr_tf")

    elif entity_type == "Transports":
        supplier_id = _supplier_picker("tr_tp")
        is_all, transport_id, limit = _scope_picker("transports", "One specific transport ID",
                                                     "e.g. TRANSPORT-412579", "tr_tp")

    elif entity_type == "Hotels":
        supplier_id = _supplier_picker("tr_hl")
        is_all, provider_code, limit = _scope_picker("hotels", "One specific provider code",
                                                      "e.g. CAI-H1", "tr_hl")

    else:  # Closed Tours
        supplier_id = _supplier_picker("tr_ct")
        closed_tour_code = st.text_input("Closed Tour Code", placeholder="e.g. TNR-03", key="tr_ct_code")
        st.caption("Travel Compositor has no bulk listing endpoint for Closed Tours, so these are done one "
                  "code at a time. A wrong code gives a clear 'not found' message rather than a raw API error.")

    st.header("Translate — Step 4: Languages & run")

    # The old "Test set (FR, DE) vs all languages" chooser was removed at the product
    # owner's request - the full language set is always what's wanted now, and offering a
    # two-language mode mainly created a way to think a product was done when 17 languages
    # were still missing. TEST_LANGUAGES no longer exists here; the standalone run_sync_*
    # CLI scripts keep their own copies for ad-hoc runs.
    target_languages = DEFAULT_TARGET_LANGUAGES
    force = st.checkbox("Force re-translate", value=False, key="tr_force",
                         help="Ignores the tracker and re-translates everything, even content already "
                              "done. Normally leave this off - the tracker is what makes re-running "
                              "cheap and safe.")

    st.write(f"**Target languages ({len(target_languages)}):** {', '.join(target_languages)}")
    st.warning("⚠️ Live mode — translations are written straight into Travel Compositor.")
    if force:
        st.warning("🔁 Force re-translate is ON — the tracker is ignored, so everything is translated again.")

    log_placeholder = st.empty()

    def log_message(msg):
        if "tr_log_lines" not in st.session_state:
            st.session_state.tr_log_lines = []
        st.session_state.tr_log_lines.append(msg)
        log_placeholder.text("\n".join(st.session_state.tr_log_lines[-200:]))

    if not st.button("🚀 Translate now", type="primary", key="tr_run"):
        return

    if entity_type != "Holiday Packages" and not supplier_id:
        st.error("Supplier ID is required.")
        return

    st.session_state.tr_log_lines = []
    log_placeholder.empty()

    api = TranslationTCAPI()
    translator = get_translator()
    store = StateStore()
    results = []

    with st.spinner("Working..."):
        # ---- Holiday Packages ----
        if entity_type == "Holiday Packages":
            if not is_all:
                if not package_id:
                    st.error("Enter a Holiday Package ID first.")
                    return
                results = [sync_holiday_package(api, translator, store, microsite_id, package_id,
                                                 target_languages, dry_run=False, force=force)]
            else:
                results = sync_all_holiday_packages(api, translator, store, microsite_id, target_languages,
                                                     dry_run=False, limit=limit, force=force)

        # ---- Tickets ----
        elif entity_type == "Tickets":
            if not is_all:
                if not ticket_code:
                    st.error("Enter a Ticket Code first.")
                    return
                main_result = sync_ticket(api, translator, store, supplier_id, ticket_code,
                                           target_languages, dry_run=False, force=force)
                option_results = sync_all_options_for_ticket_from_data(
                    api, translator, store, supplier_id, {"code": ticket_code}, target_languages,
                    dry_run=False, force=force
                ) if isinstance(main_result, dict) and main_result.get("status") != "fetch_failed" else []
                if isinstance(main_result, dict):
                    main_result["options"] = option_results
                    results = [main_result]
                else:
                    results = [main_result] + option_results
            else:
                log_message(f"📋 Fetching tickets for supplier {supplier_id}...")
                tickets = fetch_all_tickets(api, supplier_id, limit=limit)
                log_message(f"📋 Found {len(tickets)} ticket(s).")
                progress_placeholder = st.empty()

                for idx, t in enumerate(tickets):
                    code = t.get("code")
                    if not code:
                        log_message(f"⚠️ Skipping ticket {idx + 1}: no code field")
                        results.append({"status": "skipped", "reason": "no code field", "raw": t})
                        continue

                    progress_placeholder.write(f"🔄 Processing ticket {idx + 1}/{len(tickets)}: **{code}**")
                    log_message(f"🔄 Processing ticket {idx + 1}/{len(tickets)}: {code}")

                    main_result = sync_ticket_from_data(api, translator, store, supplier_id, t,
                                                         target_languages, dry_run=False, force=force)
                    log_message("   ✅ Skipped – already translated." if main_result.get("status") == "up_to_date"
                                else "   → Syncing main ticket...")
                    results.append(main_result)

                    log_message("   → Syncing options...")
                    option_results = sync_all_options_for_ticket_from_data(
                        api, translator, store, supplier_id, t, target_languages, dry_run=False, force=force)
                    if isinstance(main_result, dict):
                        main_result["options"] = option_results
                    else:
                        results.extend(option_results)

                    if option_results:
                        up_to_date = sum(1 for r in option_results if r.get("status") == "up_to_date")
                        updated = sum(1 for r in option_results if r.get("status") == "updated")
                        skipped = sum(1 for r in option_results if r.get("status") == "skipped")
                        log_message(f"      Options: {len(option_results)} total, {up_to_date} up-to-date, "
                                    f"{updated} updated, {skipped} skipped")
                        for opt_res in option_results:
                            log_message(f"         - {opt_res.get('option_code', '?')}: {opt_res.get('status', 'unknown')}")

                    log_message(f"   ✅ Finished ticket {code}")
                progress_placeholder.empty()

        # ---- Transfers ----
        elif entity_type == "Transfers":
            if not is_all:
                if not transfer_id:
                    st.error("Enter a Transfer ID first.")
                    return
                results = [sync_transfer(api, translator, store, supplier_id, transfer_id,
                                          target_languages, dry_run=False, force=force)]
            else:
                log_message(f"📋 Fetching transfers for supplier {supplier_id}...")
                transfers = fetch_all_transfers(api, supplier_id, limit=limit)
                log_message(f"📋 Found {len(transfers)} transfer(s).")
                progress_placeholder = st.empty()

                for idx, t in enumerate(transfers):
                    this_id = t.get("id")
                    if not this_id:
                        log_message(f"⚠️ Skipping transfer {idx + 1}: no 'id' field")
                        results.append({"status": "skipped", "reason": "no id field", "raw": t})
                        continue

                    progress_placeholder.write(f"🔄 Processing transfer {idx + 1}/{len(transfers)}: **{this_id}**")
                    log_message(f"🔄 Processing transfer {idx + 1}/{len(transfers)}: {this_id}")

                    result = sync_transfer_from_data(api, translator, store, supplier_id, t,
                                                      target_languages, dry_run=False, force=force)
                    log_message("   ✅ Skipped – already translated." if result.get("status") == "up_to_date"
                                else "   → Syncing transfer...")
                    results.append(result)
                    log_message(f"   ✅ Finished transfer {this_id}")
                progress_placeholder.empty()

        # ---- Transports ----
        elif entity_type == "Transports":
            if not is_all:
                if not transport_id:
                    st.error("Enter a Transport ID first.")
                    return
                results = [sync_transport(api, translator, store, supplier_id, transport_id,
                                           target_languages, dry_run=False, force=force)]
            else:
                log_message(f"📋 Fetching transports for supplier {supplier_id}...")
                transports = fetch_all_transports(api, supplier_id, limit=limit)
                log_message(f"📋 Found {len(transports)} transport(s).")
                progress_placeholder = st.empty()

                for idx, t in enumerate(transports):
                    this_id = t.get("id")
                    if not this_id:
                        log_message(f"⚠️ Skipping transport {idx + 1}: no 'id' field")
                        results.append({"status": "skipped", "reason": "no id field", "raw": t})
                        continue

                    progress_placeholder.write(f"🔄 Processing transport {idx + 1}/{len(transports)}: **{this_id}**")
                    log_message(f"🔄 Processing transport {idx + 1}/{len(transports)}: {this_id}")

                    result = sync_transport_from_data(api, translator, store, supplier_id, t,
                                                       target_languages, dry_run=False, force=force)
                    log_message("   ✅ Skipped – already translated." if result.get("status") == "up_to_date"
                                else "   → Syncing transport...")
                    results.append(result)

                    if t.get("optionCodes"):
                        log_message(f"   → Syncing options for {this_id}...")
                        option_results = sync_all_options_for_transport_from_data(
                            api, translator, store, supplier_id, t, target_languages, dry_run=False, force=force)
                        if isinstance(result, dict):
                            result["options"] = option_results
                        else:
                            results.extend(option_results)

                        if option_results:
                            up_to_date = sum(1 for r in option_results if r.get("status") == "up_to_date")
                            updated = sum(1 for r in option_results if r.get("status") == "updated")
                            skipped = sum(1 for r in option_results if r.get("status") == "skipped")
                            log_message(f"      Options: {len(option_results)} total, {up_to_date} up-to-date, "
                                        f"{updated} updated, {skipped} skipped")
                            for opt_res in option_results:
                                log_message(f"         - {opt_res.get('option_code', '?')}: {opt_res.get('status', 'unknown')}")
                    else:
                        log_message(f"   → No options for {this_id}")

                    log_message(f"   ✅ Finished transport {this_id}")
                progress_placeholder.empty()

        # ---- Hotels ----
        elif entity_type == "Hotels":
            if not is_all:
                if not provider_code:
                    st.error("Enter a Provider Code first.")
                    return
                log_message(f"📋 Fetching hotel {provider_code} for supplier {supplier_id}...")
                hotel = api.get_hotel(supplier_id, provider_code)
                if isinstance(hotel, dict) and "error" in hotel:
                    results = [{"status": "fetch_failed", "provider_code": provider_code, "detail": hotel}]
                else:
                    results = [sync_hotel(api, translator, store, supplier_id, hotel, target_languages,
                                           dry_run=False, force=force)]
            else:
                log_message(f"📋 Fetching hotels for supplier {supplier_id}...")
                hotels = fetch_all_hotels(api, supplier_id, limit=limit)
                log_message(f"📋 Found {len(hotels)} hotel(s).")
                progress_placeholder = st.empty()

                for idx, h in enumerate(hotels):
                    this_code = h.get("providerCode")
                    if not this_code:
                        log_message(f"⚠️ Skipping hotel {idx + 1}: no providerCode")
                        results.append({"status": "skipped", "reason": "no providerCode", "raw": h})
                        continue

                    progress_placeholder.write(f"🔄 Processing hotel {idx + 1}/{len(hotels)}: **{this_code}**")
                    log_message(f"🔄 Processing hotel {idx + 1}/{len(hotels)}: {this_code}")

                    full_hotel = api.get_hotel(supplier_id, this_code)
                    if isinstance(full_hotel, dict) and "error" in full_hotel:
                        log_message(f"   ❌ Failed to fetch full details for {this_code}")
                        results.append({"status": "fetch_failed", "provider_code": this_code, "detail": full_hotel})
                        continue

                    hotel_result = sync_hotel(api, translator, store, supplier_id, full_hotel,
                                               target_languages, dry_run=False, force=force)
                    main_status = hotel_result.get("main", {}).get("status", "unknown")
                    rooms_updated = sum(1 for r in hotel_result.get("rooms", []) if r.get("status") == "updated")
                    supps_updated = sum(1 for s in hotel_result.get("supplements", []) if s.get("status") == "updated")
                    log_message(f"   Main: {main_status}, Rooms updated: {rooms_updated}, "
                                f"Supplements updated: {supps_updated}")
                    results.append(hotel_result)
                    log_message(f"   ✅ Finished hotel {this_code}")
                progress_placeholder.empty()

        # ---- Closed Tours ----
        else:
            if not closed_tour_code:
                st.error("Enter a Closed Tour Code first.")
                return
            log_message(f"📋 Checking closed tour {closed_tour_code} for supplier {supplier_id}...")
            result = sync_closed_tour(api, translator, store, supplier_id, closed_tour_code,
                                       target_languages, dry_run=False, force=force)
            if result.get("status") == "not_found":
                st.error(f"❌ {result.get('reason', 'Closed tour not found.')}")
                log_message(f"   ❌ Not found: {closed_tour_code}")
            else:
                log_message(f"   → {result.get('status', 'unknown')}")
            results = [result]

    # ---- Summary ----
    by_status = {}

    def count(r):
        if isinstance(r, dict):
            if "main" in r:  # a hotel result nests main/rooms/supplements
                main = r["main"]
                if isinstance(main, dict):
                    by_status.setdefault(main.get("status", "unknown"), []).append(main)
                for room in r.get("rooms", []):
                    count(room)
                for supp in r.get("supplements", []):
                    count(supp)
            else:
                by_status.setdefault(r.get("status", "unknown"), []).append(r)
                if isinstance(r.get("options"), list):
                    for opt in r["options"]:
                        count(opt)
        else:
            by_status.setdefault("unknown", []).append(r)

    for r in results:
        count(r)

    st.subheader("Summary")
    for status, items in by_status.items():
        st.write(f"**{status}**: {len(items)}")
    with st.expander("Full result"):
        st.json(results)
