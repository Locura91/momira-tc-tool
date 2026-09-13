# apply.ps1 - applies the 2026-09-13 hotel-automap changes to this repo.
#
# Run it from inside the repo folder. It does the two things the zip alongside it cannot do alone:
#   1. patches app.py in place (app.py is far too big to ship whole through this route)
#   2. bumps the build stamp in every module, which is what clears the "Partial deploy" banner
#
# Strict on purpose: every patch must match exactly once, or NOTHING is written and it tells you
# which one failed. A half-applied patch is worse than none - the app would still start, and the
# damage would only surface later.

$ErrorActionPreference = 'Stop'
$repo = (Get-Location).Path
$appPath = Join-Path $repo 'app.py'

if (-not (Test-Path $appPath)) {
  Write-Host "ERROR: app.py not found in $repo" -ForegroundColor Red
  Write-Host "Run this from inside your momira-tc-tool folder." -ForegroundColor Red
  exit 1
}

$utf8 = New-Object System.Text.UTF8Encoding($false)
function Read-Text($p) { ([IO.File]::ReadAllText($p, $utf8)) -replace "`r`n", "`n" }
function Write-Text($p, $t) { [IO.File]::WriteAllText($p, $t, $utf8) }

$app = Read-Text $appPath
$originalApp = $app

# Running this twice is a normal thing to do when something looked like it went wrong. Say so
# plainly instead of failing with nine confusing "found 0 matches" lines.
if ($app.Contains('def render_hotel_automap_review():')) {
  Write-Host "This patch has already been applied to app.py - nothing to do." -ForegroundColor Yellow
  Write-Host "If the app still looks wrong, tell Claude rather than running this again." -ForegroundColor Yellow
  exit 0
}

$patches = @()
$patches += ,@(@'
from geocoding_client import geocode_search, geocode, parse_google_maps_url, build_place_query
import transfer_matcher
import supplier_migration
import masterdata_store
import masterdata_matcher
import price_validity
import transport_matcher
import platform_store
'@, @'
from geocoding_client import geocode_search, geocode, parse_google_maps_url, build_place_query
import transfer_matcher
import supplier_migration
import masterdata_store
import hotel_automap
import masterdata_matcher
import price_validity
import transport_matcher
import platform_store
'@, '1')
$patches += ,@(@'
                st.session_state.pop("hp_md_index_cache", None)
                st.rerun()
            else:
                st.error(f"❌ Sync failed: {sync_result['error']}")
        if st.button("Skip for now — I'll provide photos manually", key="hp_md_skip_no_index"):
            st.session_state.hp_masterdata_decided = True
            st.session_state.hp_masterdata_seed = None
            st.rerun()
        return

    synced_at = meta.get("synced_at")
'@, @'
                st.session_state.pop("hp_md_index_cache", None)
                st.rerun()
            else:
                st.error(f"❌ Sync failed: {sync_result['error']}")
        # Skipping here means creating a hotel without ever having CHECKED master data - the
        # local index isn't usable, so no search can run at all. That is the single most likely
        # way to end up with a duplicate property in Travel Compositor, so it needs a stated
        # reason rather than one click (product owner, 2026-09-13, choosing "block until a reason
        # is given" over a warning: "we must make sure"). The reason is stored and shown later in
        # the automap review screen, so a decision made in a hurry is still reviewable.
        st.markdown("---")
        st.warning(
            "⚠️ Master data can't be searched until the sync above has run, so this hotel would be "
            "created **without checking whether Travel Compositor already has it**. That's how a "
            "duplicate property appears on the Travel Compositor surface."
        )
        no_index_reason = st.text_input(
            "If you still want to continue, say why (required)",
            value="", key="hp_md_skip_reason_no_index",
            placeholder="e.g. brand-new property, confirmed with the supplier it isn't listed anywhere yet",
        )
        if st.button("Continue without checking master data", key="hp_md_skip_no_index",
                     disabled=not no_index_reason.strip()):
            st.session_state.hp_masterdata_decided = True
            st.session_state.hp_masterdata_seed = None
            st.session_state.hp_masterdata_skip_reason = no_index_reason.strip()
            st.rerun()
        return

    synced_at = meta.get("synced_at")
'@, '2')
$patches += ,@(@'
                        if st.button("Use this hotel", key=f"hp_md_pick_{i}"):
                            with st.spinner("Fetching this hotel's content from Travel Compositor..."):
                                datasheet = client.get_accommodation_datasheet(cand["id"])
                            if isinstance(datasheet, dict) and "error" not in datasheet:
                                st.session_state.hp_masterdata_seed = masterdata_matcher.datasheet_to_masterdata_seed(datasheet)
                                st.session_state.hp_masterdata_decided = True
                                st.rerun()
                            else:
                                st.error(f"❌ Couldn't fetch this hotel's content: "
                                          f"{datasheet.get('message') if isinstance(datasheet, dict) else datasheet}")

    if st.button("None of these — I'll provide photos manually", key="hp_md_skip"):
        st.session_state.hp_masterdata_decided = True
        st.session_state.hp_masterdata_seed = None
        st.rerun()


def _render_hotel_price_audit_section(data, primary):
    """Hotel Price Audit UI (product owner, 2026-09-12) - a deliberately TEMPORARY second check,
'@, @'
                        if st.button("Use this hotel", key=f"hp_md_pick_{i}"):
                            with st.spinner("Fetching this hotel's content from Travel Compositor..."):
                                datasheet = client.get_accommodation_datasheet(cand["id"])
                            if isinstance(datasheet, dict) and "error" not in datasheet:
                                _hp_seed = masterdata_matcher.datasheet_to_masterdata_seed(datasheet)
                                # The datasheet is the authority on its own ids, but fall back to
                                # the index row this candidate came from if the datasheet omits
                                # either - both carry id/giataId, and losing them here is what
                                # would leave a human with no way to complete the back-office
                                # automap (see hotel_automap.py's own docstring for why that step
                                # can't be done through the API at all).
                                _hp_seed["accommodation_id"] = _hp_seed.get("accommodation_id") or (
                                    str(cand["id"]).strip() if cand.get("id") else None)
                                _hp_seed["giata_id"] = _hp_seed.get("giata_id") or (
                                    str(cand["giataId"]).strip() if cand.get("giataId") else None)
                                _hp_seed["master_name"] = _hp_seed.get("name") or cand.get("name")
                                st.session_state.hp_masterdata_seed = _hp_seed
                                st.session_state.hp_masterdata_decided = True
                                st.session_state.hp_masterdata_skip_reason = None
                                st.rerun()
                            else:
                                st.error(f"❌ Couldn't fetch this hotel's content: "
                                          f"{datasheet.get('message') if isinstance(datasheet, dict) else datasheet}")

    # CONFIRMED PRODUCT-OWNER RULE (2026-09-13): moving past this step without picking a master
    # record must be a deliberate, stated decision, not one click - "we must make sure that
    # Automap with master is also set, so the hotel is not a duplicate". The strictness is graded
    # by what the human has actually been shown, because a blanket "always demand a reason" would
    # make people type filler text to get past a screen that had nothing to offer them:
    #
    #   * candidates found      -> reason REQUIRED. This is the real risk: the app showed matches
    #                              and a human decided none of them is this hotel. If that call is
    #                              wrong, a duplicate is created, and the reason is what makes the
    #                              call reviewable afterward.
    #   * searched, zero found  -> allowed, reason recorded automatically. Nothing was on offer to
    #                              reject, so there is no judgement call to explain.
    #   * never searched        -> reason REQUIRED. Same as the no-index case above: skipping
    #                              without looking is how duplicates happen.
    st.markdown("---")
    _hp_md_searched = candidates is not None
    _hp_md_had_candidates = bool(candidates)

    if _hp_md_had_candidates:
        st.warning(
            f"⚠️ {len(candidates)} possible match(es) are listed above. If one of them IS this "
            f"hotel, use it — that's what lets this contract be mapped to Travel Compositor's "
            f"existing property instead of appearing as a duplicate."
        )
        _hp_md_reason_needed = True
    elif not _hp_md_searched:
        st.warning(
            "⚠️ No master-data search has been run yet for this hotel. Creating it without "
            "checking is how a duplicate property appears on the Travel Compositor surface."
        )
        _hp_md_reason_needed = True
    else:
        st.info(
            "The master-data search found nothing matching this hotel, so there's nothing to map "
            "it to. Continuing is fine — this will be noted on the automap review screen so it "
            "can be double-checked in Travel Compositor later."
        )
        _hp_md_reason_needed = False

    if _hp_md_reason_needed:
        _hp_md_reason = st.text_input(
            "None of these is the right hotel? Say why (required)",
            value="", key="hp_md_skip_reason",
            placeholder="e.g. all candidates are in a different resort; this property opened this year",
        )
        _hp_md_can_skip = bool(_hp_md_reason.strip())
        _hp_md_stored_reason = _hp_md_reason.strip()
    else:
        _hp_md_can_skip = True
        _hp_md_stored_reason = "Master-data search ran and returned no candidates."

    if st.button("Continue without master data — I'll provide photos manually",
                 key="hp_md_skip", disabled=not _hp_md_can_skip):
        st.session_state.hp_masterdata_decided = True
        st.session_state.hp_masterdata_seed = None
        st.session_state.hp_masterdata_skip_reason = _hp_md_stored_reason
        st.rerun()


def render_hotel_automap_review():
    """"Hotels awaiting automap" - the follow-up checklist for the one step of hotel creation that
    Travel Compositor's API cannot perform (product owner, 2026-09-13: "we must make sure that
    Automap with master is also set, so the hotel is not a duplicate in the travel compositor
    surface").

    This screen exists because of a confirmed API limitation, not a missing feature on our side:
    neither `ContractHotelDetailedVO` (POST/PUT /hotel/{supplierId}) nor the read-only "Web content
    - Accommodations" section exposes the automap in any form - see hotel_automap.py's own
    docstring for the field-by-field check. So the app's job stops at handing a human the exact
    ids and making the outstanding work visible until someone says it's done.

    Nothing here writes to Travel Compositor. Ticking an entry off records that a HUMAN did the
    mapping in the back office; it cannot verify it, and deliberately doesn't pretend to."""
    st.header("🔗 Hotels awaiting automap")
    st.caption(
        "Travel Compositor's API can't set \"Automap with master\" - it's only available in the "
        "back office. These hotels were created here and still need that step, or they may show "
        "up as duplicate properties. Tick one off once you've done it in Travel Compositor."
    )

    pending = hotel_automap.list_pending()
    if not pending:
        st.success("✅ Nothing outstanding — every hotel created here has been mapped or checked.")
    else:
        st.warning(f"{len(pending)} hotel(s) still need attention.")

    for entry in pending:
        with st.container(border=True):
            cols = st.columns([4, 1])
            with cols[0]:
                recorded = entry.get("recorded_at")
                when = datetime.fromtimestamp(recorded).strftime("%Y-%m-%d %H:%M") if recorded else "—"
                st.markdown(f"**{entry.get('provider_code')}** — {entry.get('hotel_name') or '(no name)'}  \n"
                            f"Supplier {entry.get('supplier_id')} · published {when}")
                if entry.get("status") == hotel_automap.STATUS_LINKED:
                    st.markdown(
                        f"Map this to master accommodation **{entry.get('accommodation_id')}**"
                        + (f" · GIATA **{entry.get('giata_id')}**" if entry.get("giata_id") else "")
                        + (f"  \nMaster record: {entry.get('master_name')}" if entry.get("master_name") else "")
                    )
                else:
                    # No id to map to - so the useful thing to show is the judgement call that was
                    # made at the time, which is the thing most worth a second look.
                    st.markdown(
                        "⚠️ **No master record was linked when this was created.** Worth confirming "
                        "the property really isn't already in Travel Compositor's master data."
                        + (f"  \nReason given at the time: _{entry.get('skip_reason')}_"
                           if entry.get("skip_reason") else "")
                    )
            with cols[1]:
                if st.button("Mark as done", key=f"automap_done_{entry.get('supplier_id')}_{entry.get('provider_code')}"):
                    hotel_automap.mark_mapped(entry.get("supplier_id"), entry.get("provider_code"))
                    st.rerun()

    mapped = hotel_automap.list_mapped()
    if mapped:
        with st.expander(f"Already dealt with ({len(mapped)})"):
            st.caption("Kept as a record rather than deleted - if a duplicate ever does turn up, "
                       "this is what says whether that hotel was mapped, and when.")
            for entry in mapped:
                done = entry.get("mapped_at")
                when = datetime.fromtimestamp(done).strftime("%Y-%m-%d %H:%M") if done else "—"
                st.markdown(f"- **{entry.get('provider_code')}** — {entry.get('hotel_name') or ''} "
                            f"(marked done {when})")


def _render_hotel_price_audit_section(data, primary):
    """Hotel Price Audit UI (product owner, 2026-09-12) - a deliberately TEMPORARY second check,
'@, '3')
$patches += ,@(@'
        st.info(f"📚 Seeded from Travel Compositor master data: **{_hp_md_seed_used.get('name') or '(unnamed)'}** "
                f"— its description was folded into extraction below, and its "
                f"**{_hp_md_img_count} image(s) were already added** to Image URLs below (no manual "
                f"selection needed) — double-check they're right for this property before publishing.")

    if st.button("🔙 Start over with a different document", key="hp_cancel"):
        for key in HP_STATE_KEYS:
            st.session_state.pop(key, None)
'@, @'
        st.info(f"📚 Seeded from Travel Compositor master data: **{_hp_md_seed_used.get('name') or '(unnamed)'}** "
                f"— its description was folded into extraction below, and its "
                f"**{_hp_md_img_count} image(s) were already added** to Image URLs below (no manual "
                f"selection needed) — double-check they're right for this property before publishing.")
        # Flagged here as well as after publishing, because this is the last screen where someone
        # can still change their mind about which master record this is - and the mapping itself
        # can only be done by hand in Travel Compositor afterwards (see hotel_automap.py).
        # Reads hp_existing_snapshot from session state rather than the `existing_snapshot` local,
        # which isn't bound until further down this function.
        if _hp_md_seed_used.get("accommodation_id") and not st.session_state.get("hp_existing_snapshot"):
            st.caption(f"🔗 After publishing, this still needs **Automap with master** set by hand in "
                       f"Travel Compositor (accommodation id **{_hp_md_seed_used['accommodation_id']}**"
                       + (f", GIATA {_hp_md_seed_used['giata_id']}" if _hp_md_seed_used.get("giata_id") else "")
                       + ") — the API has no field for it. It'll be added to the automap checklist "
                         "automatically so it isn't forgotten.")

    if st.button("🔙 Start over with a different document", key="hp_cancel"):
        for key in HP_STATE_KEYS:
            st.session_state.pop(key, None)
'@, '4')
$patches += ,@(@'
                show_publish_error(f"publish hotel **{provider_code}**", hotel_response)
                return

            progress.success("✅ Phase 1 — hotel contract, rooms and meal plans published.")
            if len(_hp_phase1_attempts) > 1:
                with progress.expander("🔍 Raw request/response per attempt (debug)"):
                    for _i, _att in enumerate(_hp_phase1_attempts, start=1):
                        st.markdown(f"**Attempt {_i}** — rooms sent: `{_att['rooms_sent']}`")
'@, @'
                show_publish_error(f"publish hotel **{provider_code}**", hotel_response)
                return

            progress.success("✅ Phase 1 — hotel contract, rooms and meal plans published.")

            # AUTOMAP REMINDER (product owner, 2026-09-13: "we must make sure that Automap with
            # master is also set, so the hotel is not a duplicate in the travel compositor
            # surface"). Only for a genuinely NEW hotel - an existing one was already mapped (or
            # deliberately not) when it was first created, and re-raising it on every price update
            # would train people to ignore this notice, which is the one thing it cannot survive.
            #
            # This is a REMINDER rather than an action because the automap genuinely cannot be set
            # through the API: neither ContractHotelDetailedVO nor the read-only "Web content -
            # Accommodations" section exposes it (confirmed against the real Swagger - see
            # hotel_automap.py's own docstring for the full field-by-field check). So the honest
            # thing is to hand over the two ids and say plainly that a human has to finish it.
            if not existing_snapshot:
                _hp_md_seed_final = st.session_state.get("hp_masterdata_seed") or {}
                _hp_accommodation_id = _hp_md_seed_final.get("accommodation_id")
                _hp_giata_id = _hp_md_seed_final.get("giata_id")
                hotel_automap.record_pending(
                    supplier_id=supplier_id,
                    provider_code=provider_code,
                    hotel_name=phase1_payload.get("hotelname") or "",
                    accommodation_id=_hp_accommodation_id,
                    giata_id=_hp_giata_id,
                    master_name=_hp_md_seed_final.get("master_name") or _hp_md_seed_final.get("name"),
                    skip_reason=st.session_state.get("hp_masterdata_skip_reason"),
                )
                if _hp_accommodation_id:
                    progress.warning(
                        f"🔗 **One manual step left in Travel Compositor.** This hotel was seeded "
                        f"from master data, but Travel Compositor's API has no way to set "
                        f"**Automap with master** — it can only be done in the back office. Until "
                        f"it is, this contract can show up as a duplicate property.\n\n"
                        f"- Hotel: **{provider_code}** — {phase1_payload.get('hotelname') or ''}\n"
                        f"- Map it to accommodation id: **{_hp_accommodation_id}**"
                        + (f"\n- GIATA code: **{_hp_giata_id}**" if _hp_giata_id else "")
                        + (f"\n- Master record name: {_hp_md_seed_final.get('master_name') or _hp_md_seed_final.get('name')}"
                           if (_hp_md_seed_final.get("master_name") or _hp_md_seed_final.get("name")) else "")
                        + "\n\nIt's saved under **Hotels awaiting automap** so it isn't lost if you "
                          "can't do it right now."
                    )
                else:
                    progress.warning(
                        f"🔗 **Check this one in Travel Compositor.** No master record was linked "
                        f"to **{provider_code}**"
                        + (f" (reason given: _{st.session_state.get('hp_masterdata_skip_reason')}_)"
                           if st.session_state.get("hp_masterdata_skip_reason") else "")
                        + ", so there's nothing to automap it to. Worth confirming the property "
                          "really isn't already in Travel Compositor's master data, since that's "
                          "what creates a duplicate. Saved under **Hotels awaiting automap**."
                    )
            if len(_hp_phase1_attempts) > 1:
                with progress.expander("🔍 Raw request/response per attempt (debug)"):
                    for _i, _att in enumerate(_hp_phase1_attempts, start=1):
                        st.markdown(f"**Attempt {_i}** — rooms sent: `{_att['rooms_sent']}`")
'@, '5')
$patches += ,@(@'
# PROTOTYPE (2026-08-19): human enters a Holiday Package ID, tool proposes a replacement
# departure. Read-only (real GET calls, no PUT) - see package_rollover_tool.py's module
# docstring and the "package-auto-rollover-rules" project note.
TOOL_PACKAGEROLLOVER = "🔁 Package Rollover (prototype)"

# A Step 1 destination that is not a product type. It sits in the same list because that is
# where a person looks when they have something to record about a supplier, even though
# nothing is being uploaded.
'@, @'
# PROTOTYPE (2026-08-19): human enters a Holiday Package ID, tool proposes a replacement
# departure. Read-only (real GET calls, no PUT) - see package_rollover_tool.py's module
# docstring and the "package-auto-rollover-rules" project note.
TOOL_PACKAGEROLLOVER = "🔁 Package Rollover (prototype)"
# Follow-up checklist rather than a tool: hotels published from here that still need "Automap
# with master" set by hand in Travel Compositor's back office. Deliberately NOT given a permanent
# card on the home screen - it only appears when there is actually something on it (see the
# conditional block further down), because a checklist that shows "0 items" every day is one
# people stop reading. See hotel_automap.py for why this can't be automated away.
TOOL_HOTEL_AUTOMAP = "🔗 Hotels awaiting automap"

# A Step 1 destination that is not a product type. It sits in the same list because that is
# where a person looks when they have something to record about a supplier, even though
# nothing is being uploaded.
'@, '6')
$patches += ,@(@'
    # (AI Trip Idea, Package Rollover) collapse into one expandable section under the four
    # real tools above, instead of getting their own full-width cards - same expandable-menu
    # pattern as Step 1 of Upload & Update, so a prototype only takes up screen space once
    # someone actually opens it.
    st.write("")
    with st.expander("🧪 Prototypes — not part of the regular workflow yet"):
        st.caption("Early, not-yet-finished tools. Safe to try - see each one's own warning "
                  "for exactly what it does and doesn't do.")
'@, @'
    # (AI Trip Idea, Package Rollover) collapse into one expandable section under the four
    # real tools above, instead of getting their own full-width cards - same expandable-menu
    # pattern as Step 1 of Upload & Update, so a prototype only takes up screen space once
    # someone actually opens it.
    # Outstanding back-office automaps, shown ONLY when there are any. A hotel published without
    # its master mapping looks completely fine from inside this app - the duplicate only appears
    # on the Travel Compositor surface - so this is the one place the outstanding work can become
    # visible again to someone who isn't already looking for it.
    _automap_pending = hotel_automap.pending_count()
    if _automap_pending:
        st.write("")
        st.warning(f"🔗 **{_automap_pending} hotel(s) still need \"Automap with master\" set in "
                   f"Travel Compositor.** Until that's done they can show up as duplicate properties.")
        if st.button(f"Review {_automap_pending} hotel(s) awaiting automap",
                     key="tool_btn_automap", use_container_width=True):
            st.session_state.active_tool = TOOL_HOTEL_AUTOMAP
            st.rerun()

    st.write("")
    with st.expander("🧪 Prototypes — not part of the regular workflow yet"):
        st.caption("Early, not-yet-finished tools. Safe to try - see each one's own warning "
                  "for exactly what it does and doesn't do.")
'@, '7')
$patches += ,@(@'
# ---- Package Rollover prototype: hand straight off, it has no product-type step and uses
# its own Packages-API client (see package_rollover_tool.py's module docstring) ----
if st.session_state.active_tool == TOOL_PACKAGEROLLOVER:
    render_package_rollover_tool()
    st.stop()

# ======================================================================
# UPLOAD & UPDATE - Step 1: which product type?
'@, @'
# ---- Package Rollover prototype: hand straight off, it has no product-type step and uses
# its own Packages-API client (see package_rollover_tool.py's module docstring) ----
if st.session_state.active_tool == TOOL_PACKAGEROLLOVER:
    render_package_rollover_tool()
    st.stop()

# ---- Hotels awaiting automap: a follow-up checklist, not a product flow. Reads and writes only
# this app's own durable store - never Travel Compositor (the automap it tracks cannot be set
# through the API at all; see hotel_automap.py). ----
if st.session_state.active_tool == TOOL_HOTEL_AUTOMAP:
    render_hotel_automap_review()
    st.stop()

# ======================================================================
# UPLOAD & UPDATE - Step 1: which product type?
'@, '8')
$patches += ,@(@'
# Every review screen that has memory worth showing queues it via
# remember_memory_panel(); it is rendered here, once, at the bottom, so it never
# sits between the AI's answer and the buttons a person is trying to reach.
# ============================================================================
render_memory_panel_footer()
'@, @'
# Every review screen that has memory worth showing queues it via
# remember_memory_panel(); it is rendered here, once, at the bottom, so it never
# sits between the AI's answer and the buttons a person is trying to reach.
# ============================================================================
render_memory_panel_footer()
'@, '9')

# --- dry run: confirm every anchor matches exactly once BEFORE writing anything ---
$failed = @()
foreach ($p in $patches) {
  $find = $p[0] -replace "`r`n", "`n"
  $count = ([regex]::Matches($app, [regex]::Escape($find))).Count
  if ($count -ne 1) { $failed += "patch $($p[2]): found $count match(es), expected 1" }
}
if ($failed.Count -gt 0) {
  Write-Host "ERROR: app.py is not in the expected state. Nothing was changed." -ForegroundColor Red
  $failed | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
  Write-Host "Send Claude exactly what is printed above." -ForegroundColor Yellow
  exit 1
}

foreach ($p in $patches) {
  $find = $p[0] -replace "`r`n", "`n"
  $repl = $p[1] -replace "`r`n", "`n"
  $app = [regex]::Replace($app, [regex]::Escape($find), { param($m) $repl }, 1)
}
if ($app -eq $originalApp) {
  Write-Host "ERROR: app.py came out unchanged - aborting rather than guessing." -ForegroundColor Red
  exit 1
}
Write-Text $appPath $app
Write-Host "app.py patched ($($patches.Count) changes)." -ForegroundColor Green


# --- bump every build stamp so the "Partial deploy" banner clears ---
$stamp = '2026-09-13-hotel-automap-master-link'
$dq = [char]34
$bumped = 0
Get-ChildItem -Path $repo -Filter *.py -File | ForEach-Object {
  $t = Read-Text $_.FullName
  $key = if ($_.Name -eq 'app.py') { 'BUILD_VERSION' } else { 'MODULE_BUILD' }
  $pattern = '(?m)^' + $key + ' = ' + $dq + '[^' + $dq + ']*' + $dq
  $replacement = $key + ' = ' + $dq + $stamp + $dq
  $new = [regex]::Replace($t, $pattern, $replacement, 1)
  if ($new -ne $t) { Write-Text $_.FullName $new; $bumped++ }
}
Write-Host ('Build stamp set on ' + $bumped + ' file(s): ' + $stamp) -ForegroundColor Green

Write-Host ''
Write-Host 'Done. Open GitHub Desktop and review the changes before committing.' -ForegroundColor Cyan

