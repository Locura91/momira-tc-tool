# apply2.ps1 - 2026-09-15 hotel meal-plan/supplement duplicate-charge fix
# Dry-runs every patch first (checks each anchor matches exactly once); writes NOTHING until every check passes.
$ErrorActionPreference = 'Stop'

$stampFiles = @(
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

$oldStamp = '2026-09-13-hotel-automap-master-link'
$newStamp = '2026-09-15-hotel-meal-plan-supplement-duplicate-fix'

# ---- Already-applied guard ----
$aiCheck = Get-Content -Raw -LiteralPath 'ai_extractor.py'
if ($aiCheck.Contains('CONFIRMED REAL BUG (2026-09-15, HRG-H1/Steigenberger')) {
    Write-Host 'This patch has already been applied - nothing to do.'
    exit 0
}

# ---- Phase 1: dry-run every check first ----
$errors = @()
foreach ($f in $stampFiles) {
    if (-not (Test-Path -LiteralPath $f)) { $errors += "$f`: file not found"; continue }
    $c = Get-Content -Raw -LiteralPath $f
    $n = ([regex]::Matches($c, [regex]::Escape($oldStamp))).Count
    if ($n -ne 1) { $errors += "$f`: found $n occurrence(s) of old stamp, expected 1" }
}

$aiOldBlock = @'
"name" as short descriptive text so a human still sees it (e.g. "Early Booking Discount 10% (combinable with
Long Stay Discount only)"). This is descriptive only, for a human to read - nothing downstream enforces it.

=== RATES, SEASONS, and ROOM PRICES ===
'@
$appOldStamp = $oldStamp

$aiContent = Get-Content -Raw -LiteralPath 'ai_extractor.py'
$aiBlockMatches = ([regex]::Matches($aiContent, [regex]::Escape($aiOldBlock))).Count
if ($aiBlockMatches -ne 1) { $errors += "ai_extractor.py content anchor: found $aiBlockMatches match(es), expected 1" }
$aiStampMatches = ([regex]::Matches($aiContent, [regex]::Escape($oldStamp))).Count
if ($aiStampMatches -ne 1) { $errors += "ai_extractor.py stamp: found $aiStampMatches match(es), expected 1" }

$appContent = Get-Content -Raw -LiteralPath 'app.py'
$appStampMatches = ([regex]::Matches($appContent, [regex]::Escape($oldStamp))).Count
if ($appStampMatches -ne 1) { $errors += "app.py stamp: found $appStampMatches match(es), expected 1" }

if ($errors.Count -gt 0) {
    Write-Host 'ERROR: repo is not in the expected state. Nothing was changed.'
    $errors | ForEach-Object { Write-Host "  - $_" }
    Write-Host 'Send Claude exactly what is printed above.'
    exit 1
}

# ---- Phase 2: everything checked out - now write ----
foreach ($f in $stampFiles) {
    $c = Get-Content -Raw -LiteralPath $f
    $c2 = $c.Replace($oldStamp, $newStamp)
    [IO.File]::WriteAllText((Join-Path (Get-Location).Path $f), $c2, (New-Object System.Text.UTF8Encoding($false)))
}

$aiNewBlock = @'
"name" as short descriptive text so a human still sees it (e.g. "Early Booking Discount 10% (combinable with
Long Stay Discount only)"). This is descriptive only, for a human to read - nothing downstream enforces it.
CONFIRMED REAL BUG (2026-09-15, HRG-H1/Steigenberger Golf Resort El Gouna - caused a live guest overcharge):
some documents list a MEAL-PLAN UPGRADE charge (e.g. "Half board - 30", "Club Package - 50") under a table
literally headed "Supplements", even though it is the exact same cost as that meal plan's own entry under
MEAL PLANS above - not an independent extra charge. Extracting it a second time as a supplement is a real
double-charge, not a harmless duplicate: a hotel supplement is ALWAYS applied to the whole booking unless its
own travel_windows/meal_plans/room_names narrow it (see the Supplements editor's own warning - "a hotel
supplement is never optional"), so a supplement named "Half Board Supplement" with no dates and no meal_plans
filter charges EVERY booking that extra amount, including a guest who stayed on plain Bed & Breakfast and never
chose Half Board at all - on top of the correct Half-Board-upgrade cost already sitting in that meal plan's own
base_price/adult_prices/child_prices. RULE: if a document's Supplements/extra-charges section lists a per-
person charge for adopting or upgrading to a named meal plan (Half Board, Full Board, All Inclusive, or a
meal-inclusive package like "Club Package" here) AND that same plan is separately being extracted under MEAL
PLANS with a matching price, do NOT also create a supplement entry for it - the MEAL PLANS entry is the
correct and only place that cost belongs. Only extract a Supplements-section row as a genuine supplement when
it is a distinct charge that isn't just the cost of switching meal plan (a resort fee, a compulsory gala
dinner tied to specific dates, a genuinely separate add-on).

=== RATES, SEASONS, and ROOM PRICES ===
'@
$aiContent2 = $aiContent.Replace($aiOldBlock, $aiNewBlock).Replace($oldStamp, $newStamp)
[IO.File]::WriteAllText((Join-Path (Get-Location).Path 'ai_extractor.py'), $aiContent2, (New-Object System.Text.UTF8Encoding($false)))

$appContent2 = $appContent.Replace($oldStamp, $newStamp)
[IO.File]::WriteAllText((Join-Path (Get-Location).Path 'app.py'), $appContent2, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Patched ai_extractor.py (new meal-plan/supplement duplicate-charge rule)."
Write-Host "Build stamp set on $($stampFiles.Count + 2) file(s): 2026-09-15-hotel-meal-plan-supplement-duplicate-fix"
Write-Host ''
Write-Host 'Done. Add the new test file (tests\test_2026_09_15_hotel_meal_plan_supplement_duplicate_bug.py) from the same zip, then open GitHub Desktop and review before committing.'
