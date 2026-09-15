# apply3.ps1 - 2026-09-16 _dmy_date_field StreamlitWidgetAlreadyInstantiatedError fix
# Anchors are ASCII-only on purpose: app.py's calendar emoji is double-encoded on disk (an older
# BOM-less patch script mangled it), so any anchor containing it can never match. Line endings are
# normalized before matching and restored on write, so CRLF checkouts work too.
# Dry-runs every patch first; writes NOTHING until every check passes.
$ErrorActionPreference = 'Stop'

$stampFiles = @(
    'ai_extractor.py',
    'api_client.py',
    'builder.py',
    'bulk_notes.py',
    'cancellation_bulk.py',
    'cancellation_bulk_transport.py',
    'cancellation_links.py',
    'date_format.py',
    'document_reader.py',
    'extraction_memory.py',
    'geocoding_client.py',
    'hotel_automap.py',
    'hotel_matcher.py',
    'image_dimensions.py',
    'masterdata_matcher.py',
    'masterdata_store.py',
    'numeric_helpers.py',
    'outreach_discovery.py',
    'outreach_email.py',
    'outreach_followups.py',
    'outreach_learned_suppliers.py',
    'outreach_memory.py',
    'outreach_scope.py',
    'outreach_tool.py',
    'package_rollover_tool.py',
    'platform_store.py',
    'price_audit.py',
    'price_refresh.py',
    'price_validity.py',
    'r2_client.py',
    'schemas.py',
    'state_store.py',
    'stop_sales_tool.py',
    'supplier_images.py',
    'supplier_migration.py',
    'text_normalize.py',
    'transfer_matcher.py',
    'transport_matcher.py',
    'travelcompositor_api.py',
    'trip_idea_tool.py',
    'trip_quote_client.py',
    'ui_components.py',
    'web_extractor.py',
    'weekly_review.py'
)

$oldStamp = '2026-09-15-hotel-meal-plan-supplement-duplicate-fix'
$newStamp = '2026-09-16-dmy-date-field-widget-instantiated-fix'

# ---- Already-applied guard ----
$appCheck = Get-Content -Raw -Encoding UTF8 -LiteralPath 'app.py'
if ($appCheck.Contains('pending_key = f"{key}__pending"')) {
    Write-Host 'This patch has already been applied - nothing to do.'
    exit 0
}

# ---- Phase 1: dry-run every check first ----
$errors = @()
foreach ($f in $stampFiles) {
    if (-not (Test-Path -LiteralPath $f)) { $errors += "$f`: file not found"; continue }
    $c = Get-Content -Raw -Encoding UTF8 -LiteralPath $f
    $n = ([regex]::Matches($c, [regex]::Escape($oldStamp))).Count
    if ($n -ne 1) { $errors += "$f`: found $n occurrence(s) of old stamp, expected 1" }
}

$anchorA = @'
    tcol, ccol = st.columns([5, 1])
    with tcol:
        typed = st.text_input(label, value=_disp(value_iso), key=key, placeholder=placeholder)
    iso_value = _iso(typed)
'@

$anchorBold = @'
            if picked and picked.strftime("%d/%m/%Y") != typed:
                st.session_state[key] = picked.strftime("%d/%m/%Y")
'@

$anchorBnew = @'
            if picked and picked.strftime("%d/%m/%Y") != typed:
                st.session_state[pending_key] = picked.strftime("%d/%m/%Y")
'@

$insertBlock = @'
    # CONFIRMED REAL BUG (2026-09-16, reported: "creating new tickets" -> StreamlitWidgetAlready
    # InstantiatedError on this exact field): the popover below used to write directly into
    # st.session_state[key] AFTER the text_input(key=key) widget above had already been
    # instantiated in this same run. Streamlit refuses that outright - a widget's own key can
    # only be set BEFORE that widget is created (its next rerun), never after, and this
    # function was doing it in the same script pass every time. Every existing call site
    # (14 of them, every product flow) hit this the moment a human used the calendar picker at
    # all, not something new to multi-ticket - it just happened to be reported there first.
    # FIX: the picked value is queued into a separate "<key>__pending" slot instead, and
    # consumed here, BEFORE the text_input widget for `key` exists yet, on the rerun that
    # follows - the standard safe pattern for updating an already-instantiated widget's value.
    pending_key = f"{key}__pending"
    if pending_key in st.session_state:
        st.session_state[key] = st.session_state.pop(pending_key)


'@

$appRaw = Get-Content -Raw -Encoding UTF8 -LiteralPath 'app.py'
$appHadCRLF = $appRaw.Contains("`r`n")
$appNorm = if ($appHadCRLF) { $appRaw -replace "`r`n", "`n" } else { $appRaw }

$nA = ([regex]::Matches($appNorm, [regex]::Escape($anchorA))).Count
if ($nA -ne 1) { $errors += "app.py anchor A (columns/text_input block): found $nA match(es), expected 1" }
$nB = ([regex]::Matches($appNorm, [regex]::Escape($anchorBold))).Count
if ($nB -ne 1) { $errors += "app.py anchor B (session_state write): found $nB match(es), expected 1" }
$nS = ([regex]::Matches($appNorm, [regex]::Escape($oldStamp))).Count
if ($nS -ne 1) { $errors += "app.py stamp: found $nS match(es), expected 1" }

if ($errors.Count -gt 0) {
    Write-Host 'ERROR: repo is not in the expected state. Nothing was changed.'
    $errors | ForEach-Object { Write-Host "  - $_" }
    Write-Host 'Send Claude exactly what is printed above.'
    exit 1
}

# ---- Phase 2: everything checked out - now write ----
foreach ($f in $stampFiles) {
    $c = Get-Content -Raw -Encoding UTF8 -LiteralPath $f
    $c2 = $c.Replace($oldStamp, $newStamp)
    [IO.File]::WriteAllText((Join-Path (Get-Location).Path $f), $c2, (New-Object System.Text.UTF8Encoding($false)))
}

$appNorm2 = $appNorm.Replace($anchorA, ($insertBlock + $anchorA)).Replace($anchorBold, $anchorBnew).Replace($oldStamp, $newStamp)

# ---- Repair the double-encoded calendar emoji left behind by an older BOM-less patch ----
# built from code points: the mojibake contains a curly quote that PowerShell would otherwise
# treat as a string delimiter, and keeping this script pure ASCII removes every encoding risk.
$mojibake = -join @([char]0x00F0, [char]0x0178, [char]0x201C, [char]0x2026)
$realEmoji = [char]::ConvertFromUtf32(0x1F4C5)
$emojiFixed = 0
if ($appNorm2.Contains($mojibake)) {
    $emojiFixed = ([regex]::Matches($appNorm2, [regex]::Escape($mojibake))).Count
    $appNorm2 = $appNorm2.Replace($mojibake, $realEmoji)
}

$appOut = if ($appHadCRLF) { $appNorm2 -replace "`n", "`r`n" } else { $appNorm2 }
[IO.File]::WriteAllText((Join-Path (Get-Location).Path 'app.py'), $appOut, (New-Object System.Text.UTF8Encoding($false)))

Write-Host 'Patched app.py (_dmy_date_field no longer writes into an already-instantiated widget key).'
if ($emojiFixed -gt 0) {
    Write-Host "Also repaired $emojiFixed corrupted calendar emoji in app.py (was showing as garbled text on the date-picker button)."
} else {
    Write-Host 'Calendar emoji was already correct - no repair needed.'
}
Write-Host "Build stamp set on $($stampFiles.Count + 1) file(s): 2026-09-16-dmy-date-field-widget-instantiated-fix"
Write-Host ''
Write-Host 'Done. The new test file (tests\test_2026_09_16_dmy_date_field_widget_already_instantiated.py) came from the same zip. Open GitHub Desktop and review before committing.'
