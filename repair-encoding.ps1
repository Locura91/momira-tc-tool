# repair-encoding.ps1 - restores the 45 files whose non-ASCII characters were mangled by an
# earlier patch script that read them with the system ANSI codepage instead of UTF-8.
# The mangling is a pure, exactly-invertible transform. Every repaired file is checked against a
# SHA-256 checksum of the correct content BEFORE anything is written; if a single file fails,
# nothing is written at all. Running it twice is a safe no-op.
$ErrorActionPreference = 'Stop'

$expected = @{
    'ai_extractor.py' = 'E80337669306B9E8CDDD0C4DAB5477EAF0D7FBC732CA9021CE415450600F9371'
    'api_client.py' = '80144A8FBF8E75967AC50DE10D336115507B3A3562A1BC2612BAD72518D9A2C3'
    'app.py' = '0058687B8E8BBA2CB41C4F9843A7EC17E22CB7DEAC234884495D5650CE14E5A4'
    'builder.py' = '300AC6463A567BAE1877666DB8219D237E8A94B21A00AFBA296D0DC3665FB057'
    'bulk_notes.py' = '5DB0EDA50B6A6376D84F8823203ED6003D1422466D57E3EF96C119F2B20180B4'
    'cancellation_bulk.py' = '9906166C32FD59A8B9117EBA6C65908A26F01DA04BD9500905EACA5363F170DA'
    'cancellation_bulk_transport.py' = '1A50D13E5C11C3A11C31E2A4C9E285FD470C9C3F0F823B71E778DC66DB84A030'
    'cancellation_links.py' = '2799AF8AFCD346E88F8653614AC7AB1A93D456B50DD33F4C8430A3A73AF26CF8'
    'date_format.py' = '6EDD88B7E7DA4E518A77685E0C6298F2D14BD768292D72DB4B0EED0139EECC9E'
    'document_reader.py' = 'AB17C4FCC4AABB5B1ABBCB1090F96C6EE24CE304CB34BD15DC6EC6998F299F74'
    'extraction_memory.py' = 'DC4695BE3494C9AC412FFDC4FDC69C8328F47E8E247C332D606AB1A8EFECF17C'
    'geocoding_client.py' = 'C1C590F9402D4277BB1FCF08F11AAC01D7934A7976BA56DC324B10291C255EC5'
    'hotel_automap.py' = 'FFD6A0EC5529F4D56063D075898224978BE1A8432100B14E7D455F6E6F84476C'
    'hotel_matcher.py' = '066F6F07163D701309379E4518DC43705D8CA623CAF3E54CD69A0E81B8D64D0D'
    'image_dimensions.py' = 'C10A3F5E21430501CF992CF38AF403BA20B9FC7CC86B73BB5D857651FBA6AF37'
    'masterdata_matcher.py' = 'A26D91957404C4AAE19A93A0A41D6579E585CEFE2EC16FD6AFB9031B50B8BEA4'
    'masterdata_store.py' = 'C792FB01673A1E2EBC7E2AD47299209B80DB5D8E27CAAF89D2B820725AA07772'
    'numeric_helpers.py' = '4649F2FD003EB2B63C1525FDE6871A7899E986812146BA72C773F4FA560A6D8F'
    'outreach_discovery.py' = '68D48571C678AD748FF1BB92154A162EBDED0F3B00DB4F267B9938E2DB1D6B13'
    'outreach_email.py' = '818061689FE6B40DC40EBECE746E007ACC6BB7ED07254451B19AC9C4B8A9D5D1'
    'outreach_followups.py' = 'DB3A123FF0C339F3535E1D9B4D51F459BAB9BC22E46BAAEDAAF36635CC9E6E18'
    'outreach_learned_suppliers.py' = 'BC288A50533362049E991E140931253C9616808A89D445C9A7DE6AF7B702CAB6'
    'outreach_memory.py' = '7662C82E4CE06B714717E24DC35154BE164649F6620490B5B2DFB67D9D7CDC67'
    'outreach_scope.py' = '927C8EA54E4A2BEF54FF5E872610CFED91B974B47FAFCA422E50C99E53B420DE'
    'outreach_tool.py' = 'E6ABDBCA22E7905E8B331E05833D8B5015FD4739977FAD7C0E89303411C8A13D'
    'package_rollover_tool.py' = '44B3271E4A6B78741E59747275C126CA1185DB78D4192F44A6B587EC68A627DF'
    'platform_store.py' = '205AA40E511C4A5E7F670134E1DFA7F1BEF0D783205CC0A8A996E94352ED17F9'
    'price_audit.py' = '9991CBC09CA9C26FF788809426DA7C8A4B23B4E74E44583BB1AE93169A1E32F8'
    'price_refresh.py' = '59FB59DD3D1ED57DA2C4C35FB2F62CF26E91DE2B3A1FC37A9BF717FEF23421B2'
    'price_validity.py' = 'A8DCEA870AF04F0946DF8D04039303E16AA915F23B27B1F2CE54242AB9BAF5B1'
    'r2_client.py' = '49D152C811D290467657E4C32A71E1893B987332A3EB409C4B5B79D534413F95'
    'schemas.py' = '89175E507EE5B367B5E92A2011D9153683013641919A5FC2999BD8705175CC02'
    'state_store.py' = '478596CD69CFF41E62CDCC3E3459C2D05A521332A3483156131D3B42BE573295'
    'stop_sales_tool.py' = '26496B82C00DA92DED72DBE798FE5C68EB439758DD5FA2E4BD9ADC7996EA9FA9'
    'supplier_images.py' = 'A058B65DCCF89359AF4910222109F93D6E9B394D9C9F2A28A8218A882D05B059'
    'supplier_migration.py' = 'B3DF1AE9A1B396635E4CDE8ACC754CEA51A345426948BBEE382F2331707CDA6A'
    'text_normalize.py' = 'B86CF8F7B4F25AA653FE50A3CEBE26187727740AE9C763FFABE2E48879E57583'
    'transfer_matcher.py' = 'F7C935303AA3B52C6B51237397BAC5A128231F05181990A8D223349DA0A4B16A'
    'transport_matcher.py' = '0BC05D15FFB7A97084C3709AD43109DDB3E9B1226E1B51F099FE38851A9FB2FE'
    'travelcompositor_api.py' = '4B6F4F3E34A4CD14025E9C6D0AD64A25CE5D77DF0E317DCF5B0B4F2EE74C72B9'
    'trip_idea_tool.py' = 'B5580E0C61B86FADA5B7B6FBF838DB6CF3C4A0752C61DA16AD517419472BD51B'
    'trip_quote_client.py' = '6816649D2F19D1C83A98010D8624087437833EA971A43A39BF6FC16CC5513831'
    'ui_components.py' = '52B9C7358B4132424FCFA788B9AC8A15F9249113AF315E91557B90CE8310FBDF'
    'web_extractor.py' = '62693B86E6FB8DC5EA930D4CCABB0B734238F4811A59AD2216814CEFBE271783'
    'weekly_review.py' = '31F2BCB747C2BAC17B20041E63EE05BEE7C5727E4D9B17793D60A1C8A199599E'
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
try {
    $efb = New-Object System.Text.EncoderExceptionFallback
    $dfb = New-Object System.Text.DecoderExceptionFallback
    $enc1252 = [System.Text.Encoding]::GetEncoding(1252, $efb, $dfb)
} catch {
    try { [System.Text.Encoding]::RegisterProvider([System.Text.CodePagesEncodingProvider]::Instance) } catch { }
    $efb = New-Object System.Text.EncoderExceptionFallback
    $dfb = New-Object System.Text.DecoderExceptionFallback
    $enc1252 = [System.Text.Encoding]::GetEncoding(1252, $efb, $dfb)
}

$sha = [System.Security.Cryptography.SHA256]::Create()
function Get-Sha([string]$s) {
    $b = (New-Object System.Text.UTF8Encoding($false)).GetBytes($s)
    return ([BitConverter]::ToString($sha.ComputeHash($b))).Replace('-', '')
}

# The date-picker emoji was already restored to its correct form by the previous patch, so it is
# put back into its mangled form first, letting the whole file invert as one uniform transform.
$realEmoji = [char]::ConvertFromUtf32(0x1F4C5)
$moji = -join @([char]0x00F0, [char]0x0178, [char]0x201C, [char]0x2026)

$plan = @{}
$errors = @()
$alreadyOk = 0

foreach ($f in ($expected.Keys | Sort-Object)) {
    $path = Join-Path (Get-Location).Path $f
    if (-not (Test-Path -LiteralPath $path)) { $errors += "$f : file not found"; continue }
    $content = [IO.File]::ReadAllText($path, $utf8NoBom)
    if ((Get-Sha $content) -eq $expected[$f]) { $alreadyOk++; continue }
    $norm = $content.Replace($realEmoji, $moji)
    $fixed = $null
    try {
        $bytes = $enc1252.GetBytes($norm)
        $fixed = [System.Text.Encoding]::UTF8.GetString($bytes)
    } catch {
        $errors += "$f : holds characters outside the known corruption pattern - not touching it"
        continue
    }
    if ((Get-Sha $fixed) -eq $expected[$f]) { $plan[$f] = $fixed; continue }
    $fixed2 = $fixed + "`n"
    if ((Get-Sha $fixed2) -eq $expected[$f]) { $plan[$f] = $fixed2; continue }
    $errors += "$f : repaired content does not match its expected checksum"
}

if ($errors.Count -gt 0) {
    Write-Host 'ERROR: repo is not in the expected state. Nothing was changed.'
    $errors | ForEach-Object { Write-Host "  - $_" }
    Write-Host 'Send Claude exactly what is printed above.'
    exit 1
}

if ($plan.Count -eq 0) {
    Write-Host "All $alreadyOk file(s) already match the expected checksums - nothing to repair."
    exit 0
}

foreach ($f in $plan.Keys) {
    [IO.File]::WriteAllText((Join-Path (Get-Location).Path $f), $plan[$f], $utf8NoBom)
}

Write-Host "Repaired $($plan.Count) file(s). $alreadyOk file(s) were already correct."
Write-Host 'Each repaired file was verified against a SHA-256 checksum before anything was written.'
Write-Host ''
Write-Host 'Now open GitHub Desktop and review the diff before committing.'
