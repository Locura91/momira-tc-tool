"""
sync_shared.py — code shared by every sync_*.py entity-type module in the translation-sync
engine (sync_transfer.py, sync_ticket.py, sync_transport.py, sync_closed_tour.py, and partly
sync_hotel.py).

CONSOLIDATED 2026-10-02 (weekly duplicate-code audit; product-owner request, verbatim:
"please start the weekly check ... with the focus on duplicates ... we need to check
completely"). A sliding-window duplicate scan found strip_html_and_compress,
compress_translatable_fields, build_updated_datasheets, get_existing_content_for_language, and
verify_and_filter_needed each hand-copied across 3-5 files and kept in lockstep by hand - exactly
the kind of duplication that already caused extra work twice this week: the 2026-09-30
field-fallback fix and the 2026-10-01 bullet-list-formatting fix each had to be applied to the
same logic in multiple files separately (13 call sites across 6 modules). This file holds one
copy of what's genuinely identical; each sync_*.py module now imports from here (or wraps these
with its own entity-specific field lists/content-lookup function) instead of redefining its own
copy.

NOT moved here, deliberately:
- sync_transport.py's OWN get_existing_content_for_language is intentionally different (checks
  datasheets/translations/remarks containers, not just datasheets - see its own docstring citing
  a confirmed real bug, TRANSPORT-408971) and is untouched by this consolidation.
- sync_hotel.py's per-sub-entity (hotel/room/supplement/offer) extract_translatable_fields_from_*,
  get_existing_*_content_for_language, and build_updated_* functions have genuinely different
  field shapes (hotel uses a "descriptions" array, not "datasheets") and are not part of this
  consolidation - only its byte-identical strip_html_and_compress/compress_translatable_fields
  move here, same as every other module.
- Every module keeps its OWN verify_and_filter_needed/get_existing_content_for_language/
  build_updated_datasheets function (same name, same external signature/defaults as before) -
  these are now thin wrappers delegating to the shared implementation below, so no caller or
  test anywhere in the codebase had to change how it calls these functions.
"""
from typing import Any, Callable, Dict, List

from state_store import StateStore

# CONSOLIDATED 2026-10-02 (same weekly duplicate-code audit pass as the rest of this file):
# byte-identical to the list translation_tool.py defined independently (and, separately,
# run_sync_tickets.py's own standalone copy) - all three applied the SAME "reduced from 30 to 19
# target languages" product-owner decision, hand-copied rather than shared. Both now import this
# one list instead of redefining it, so a future language-list change only needs to land once.
# Reduced from 30 to 19 target languages per the product owner: removed Albanian (SQ), Arabic
# (AR), Azerbaijani (AZ), Georgian (KA), Japanese (JA), Croatian (HR), Malay (MS), Serbian (SR),
# Thai (TH), Uzbek (UZ) and Bulgarian (BG). Applies to every entity type, since they all share
# this one list. Persian/Farsi was already absent before that change.
DEFAULT_TARGET_LANGUAGES = [
    "FR", "SL", "PL", "DE", "SK", "HU", "NL", "ES", "TR",
    "RU", "NO", "SV", "RO", "CS", "EL", "FI",
    "PT", "DA", "IT",
]


def strip_html_and_compress(text: str) -> str:
    """
    NO-OP passthrough. This used to strip every HTML tag out of a field before sending it to
    the translator, which silently destroyed any real formatting the source field had (bullet
    lists, bold, etc.) - confirmed as the cause of translated Closed Tour fields (and others)
    losing all formatting, coming back as flat <p> text instead of the original's <ul><li>/<b>
    structure. translator.py's SYSTEM_PROMPT already explicitly instructs the model to
    "preserve HTML tags ... EXACTLY as they appear, untouched, in the same position" - but that
    instruction is meaningless if the tags are stripped out before the model ever sees them.
    Every entity type shares this exact fix; kept as a no-op (rather than deleted outright) so
    every sync_*.py module's existing compress_translatable_fields call site needed no change.
    """
    return text


def compress_translatable_fields(fields: Dict[str, str]) -> Dict[str, str]:
    compressed = {}
    for key, value in fields.items():
        if isinstance(value, str):
            compressed[key] = strip_html_and_compress(value)
        else:
            compressed[key] = value
    return compressed


def build_updated_datasheets(
    original_datasheets: Dict[str, Any],
    translations_by_lang: Dict[str, Dict[str, str]],
    en_entry: Dict[str, Any],
    list_fields: tuple = (),
) -> Dict[str, Any]:
    """
    Merge translations back into a datasheets map. `list_fields` names which fields are stored
    as a list of lines rather than a single string (Ticket's includes/excludes, for example) -
    every other module passes none and gets the plain-string behavior every module used to have
    hand-copied.
    """
    new_datasheets = dict(original_datasheets)
    for lang, trans in translations_by_lang.items():
        # Start with a copy of the EN entry to ensure all fields exist
        base = dict(en_entry)
        for f, text in trans.items():
            if f in list_fields:
                base[f] = [line for line in text.split("\n") if line.strip()] if text.strip() else []
            else:
                base[f] = text
        # Preserve any fields that were in the original language entry
        if lang in original_datasheets:
            for k, v in original_datasheets[lang].items():
                if k not in base:
                    base[k] = v
        new_datasheets[lang] = base
    return new_datasheets


def get_existing_content_for_language(
    entry: Dict[str, Any],
    lang: str,
    text_fields: tuple,
    list_fields: tuple = (),
) -> Dict[str, str]:
    """
    Reads back whatever real per-language content already exists in entry["datasheets"][lang],
    for the entity's own text_fields (plain strings) and list_fields (lines joined with "\\n" -
    the inverse of build_updated_datasheets' list-field split). Entity types whose existing
    content ISN'T simply entry["datasheets"][lang] (sync_transport.py's main/option checker,
    sync_ticket.py's option checker, sync_closed_tour.py's option checker) keep their own
    dedicated function instead of this one.
    """
    datasheets = entry.get("datasheets", {})
    lang_entry = datasheets.get(lang, {})
    if not lang_entry:
        return {}
    fields = {}
    for f in text_fields:
        val = lang_entry.get(f)
        if isinstance(val, str) and val.strip():
            fields[f] = val
    for f in list_fields:
        val = lang_entry.get(f)
        if isinstance(val, list) and val:
            fields[f] = "\n".join(val)
    return fields


def verify_and_filter_needed(
    store: StateStore,
    entity_type: str,
    supplier_id: str,
    entity_id: str,
    source_hash: str,
    target_languages: List[str],
    current_entry: Dict[str, Any],
    source_fields: Dict[str, str],
    existing_content_fn: Callable[[Dict[str, Any], str], Dict[str, str]],
    option_code: str = "",
) -> List[str]:
    """
    Check state and verify existing content. Returns languages that need translation.

    `existing_content_fn` is mandatory here (unlike each module's own public
    verify_and_filter_needed wrapper, which keeps its own default) so this shared core never
    has to guess which "does this language already have real content" checker is correct for
    the entity/option being checked - every module's wrapper resolves that itself (most default
    to their own get_existing_content_for_language; sync_ticket.py's and sync_closed_tour.py's
    OPTION callers pass their dedicated option-shaped checker instead - see each module's own
    verify_and_filter_needed for why, especially sync_ticket.py's, which documents a real bug
    this exact mix-up caused for Ticket options before it was fixed there on 2026-09-ish).
    """
    state = store.get_state(entity_type, supplier_id, entity_id, option_code)
    if state is None or state["source_hash"] != source_hash:
        needed = list(target_languages)
    else:
        already_done = set(state["translated_languages"])
        needed = [lang for lang in target_languages if lang not in already_done]

    truly_needed = []
    languages_to_add_to_state = []

    for lang in needed:
        existing = existing_content_fn(current_entry, lang)
        if not existing:
            truly_needed.append(lang)
            continue

        is_identical = True
        for field, src_text in source_fields.items():
            if existing.get(field) != src_text:
                is_identical = False
                break

        if is_identical:
            truly_needed.append(lang)
        else:
            languages_to_add_to_state.append(lang)

    if languages_to_add_to_state:
        prior_state = store.get_state(entity_type, supplier_id, entity_id, option_code)
        prior_langs = prior_state["translated_languages"] if prior_state and prior_state["source_hash"] == source_hash else []
        all_langs = sorted(set(prior_langs) | set(languages_to_add_to_state))
        store.upsert_state(entity_type, supplier_id, entity_id, source_hash, all_langs, option_code=option_code)

    return truly_needed
