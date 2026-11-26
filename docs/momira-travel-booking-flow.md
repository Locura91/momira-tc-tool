# Momira Travel Booking Flow — CSS Selectors & UI Documentation

**URL**: `https://momira.travel/travel-map.xhtml?tripId=<id>`  
**Stack**: JSF / PrimeFaces, Travel Compositor (TC) engine  
**Date explored**: 2026-11-26

---

## 1. Page Flow (User Journey)

```
travel-map.xhtml          →  accommodationCityAvail.xhtml  →  ticketCityAvail.xhtml  →  summary.xhtml
(Select destinations)        (Select Accommodation)           (Select Activities)        (Trip Summary)
                                                                                           ↓
                                                                                       passengers.xhtml → payment
                                                                                       ⚠ NEVER PROCEED HERE
```

**Navigation tabs** (breadcrumb wizard):

| Step | Tab ID | CSS Class | Label |
|------|--------|-----------|-------|
| Destinations | — | `.destinations_step` | (map icon) |
| Accommodations | `#hotel_step` | `.hotel_step` | "Accommodations" |
| Activities | `#ticket_step` | `.ticket_step` | "Activities" |
| Summary | `#summary_step` | `.summary_step .summary-selection` | "Summary" |
| Passengers | — | `.passenger_step` (disabled) | "Passengers" |
| Payment | — | `.payment_step` (disabled) | "Payment" |

Each tab contains an `<a>` with class `c-breadcrumb__link`. Click the `<a>` to navigate.

---

## 2. Page 1: Select Destinations (`travel-map.xhtml`)

### Form Fields

| Field | Selector | Type | Notes |
|-------|----------|------|-------|
| **Departure date** | `#modal-init-compositor-all\:departureDateMultidestination\:input` | text (datepicker) | Format: `DD.MM.YYYY`. Class: `departureDateMultiTran`. jQuery datepicker. |
| **From (origin)** | `#addOrigin_input` | PrimeFaces autocomplete | Placeholder "From". Class: `ui-autocomplete-input` |
| **To (destination)** | `#adddestination_input` | PrimeFaces autocomplete | Placeholder "To". Widget: `PrimeFaces.widgets.widget_adddestination` |
| **Room/pax selector** | `#modal-init-compositor-all\:roomsMulti\:room-selector` | div container | Class: `dev-rooms` |
| **Adults ±** | `.dev-adult-minus` / `.dev-adult-count` / `.dev-adult-plus` | buttons + span | Inside `.dev-rooms` |
| **Children ±** | `.dev-child-minus` / `.dev-child-count` / `.dev-child-plus` | buttons + span | Inside `.dev-rooms` |
| **Flight class** | `#modal-init-compositor-all\:j_id_16j_f_1sp\:ClassOption` | `<select>` | Options: ECONOMY, PREMIUM_ECONOMY, BUSINESS |
| **Stopovers** | `#modal-init-compositor-all\:init-compositor-more-search-settings\:stopoversSearch` | — | — |
| **Route type** | `#routeType\:dev-ferry-route`, `#routeType\:dev-road-route` | radio-like | Ferry vs road |
| **Nights display** | `span.dev-trip-nights` | read-only span | Shows "-- Nights" or "3 Nights" |

### Destination Autocomplete Structure

The autocomplete dropdown uses a **TABLE** (not UL/LI):

```
#adddestination_panel
  TABLE.ui-autocomplete-items.ui-autocomplete-table
    TBODY
      TR.ui-autocomplete-item.ui-autocomplete-row
        [data-item-label="Cairo, Egypt"]
        [data-item-value="Destination::CAI"]
```

**Destination code format**: `Destination::<3-letter-code>` (e.g., `Destination::CAI`)

**To select a destination programmatically**:
```javascript
PrimeFaces.widgets.widget_adddestination.search('Cairo');
// Wait for panel → click TR with desired data-item-value
```

### Nights Selector (map popup)

After selecting a destination, a popup appears with:

| Element | Selector | Notes |
|---------|----------|-------|
| **Nights dropdown** | `select.tc-hotelNightsSelector` | Form name: `infowindowform:j_id_6i`. Options: 0–8+ nights |
| **Accommodation included** | checkbox near nights | Green toggle |

### Start Search Button

| Element | Selector | Notes |
|---------|----------|-------|
| **Start Search** | `#quoteTripLink` | `<a>` commandlink. Class: `c-button c-button--lg bg-clr--success sum-continuar__button`. **Often below viewport** — use `.click()` via JS. |

### Loading Indicator

| Element | Selector |
|---------|----------|
| Loader overlay | `.dev--c-loader` |
| Waiting text | `.dev-waiting-text` |

---

## 3. Page 2: Accommodations (`accommodationCityAvail.xhtml`)

**URL pattern**: `/accommodation/accommodationCityAvail.xhtml?tripId=<id>&availPosition=1`

### Sidebar — Trip Summary

| Element | Selector | Notes |
|---------|----------|-------|
| **Total price (per person)** | `b.summary__total-amount__price.clr--brand` | Shows e.g. "128 €" |
| **Total price container** | `.summary__total-amount` | Wrapper div |
| **Price shown (dev)** | `.dev-side-summary-priceShown` | Also shows "128 €" — **this is the selector the Chrome extension needs** |
| **Total price (absolute)** | `b.c-price__bottom` | Shows "(256 € Total price)" |
| **precioTotal div** | `#accommodationForm\:j_id_4x\:j_id_7m\:precioTotal` | Class: `precioTotal` |
| **Book Now** | `.c-button.bg-clr--success` in sidebar | ⚠ **NEVER CLICK** |

### View Modes

| Mode | Selector | Notes |
|------|----------|-------|
| Extended | tab with text "Extended" | Default, shows full cards |
| Compact | tab with text "Compact" | Smaller cards |
| Comparator | tab with text "Comparator" | Side-by-side |
| Map | `.c-button` with map icon | Map view |

### Hotel Cards

| Element | Selector | Notes |
|---------|----------|-------|
| **Card wrapper** | `div.c-card--ticket` or by ID pattern `mainForm:datascrollHorizontal:<index>:hotelextended:...` | — |
| **Hotel name** | `h4 a` inside card | — |
| **Hotel price** | `.c-extended__price .c-price` | Shows "Total price -198 €" |
| **Select button** | `#mainForm\:datascrollHorizontal\:<index>\:hotelextended\:selectHotelServiceHotelExtended` | PrimeFaces commandlink |
| **Change room** | link with text "Change room" | — |
| **Compare checkbox** | `.compare-checkbox-panel` | — |
| **Star rating** | inside card header | — |

### Filters (Left Sidebar)

| Filter | Notes |
|--------|-------|
| By day | date filter |
| Accommodation name | text search |
| Price per person | range slider (min/max) |
| Distance to center | dropdown |
| Recommended | checkbox |
| Free cancellation | checkbox |
| Hotel / B&B / Guest house | checkboxes |

---

## 4. Page 3: Activities (`ticketCityAvail.xhtml`)

**URL pattern**: `/ticket/ticketCityAvail.xhtml?tripId=<id>&availPosition=1`

### Activity Cards

| Element | Selector | Notes |
|---------|----------|-------|
| **Card wrapper** | `.c-card.c-card--ticket` or by ID `mainForm:datascroll:<index>:j_id_8q0:ticketCard` | Has `dev-ticket` class prefix |
| **Activity price** | `.c-card__price .c-price` | Shows "From 101 €" |
| **Price value** | `b.c-price__primary` | Shows "101 €" |
| **View options** | button in each card | Opens activity detail/options |
| **Order by** | dropdown top-right | "Our selection", price, etc. |

### Filters

| Filter | Notes |
|--------|-------|
| Name | text search |
| Price per person | range slider |

---

## 5. Page 4: Summary (`summary.xhtml`)

**URL pattern**: `/summary.xhtml?tripId=<id>`

### Price Elements (CRITICAL for Chrome extension)

| Element | Selector | Shows | Notes |
|---------|----------|-------|-------|
| **Per-person price** | `b.summary__total-amount__price.clr--brand` | "128 €" | Main visible price |
| **Price shown (dev)** | `.dev-side-summary-priceShown` | "128 €" | ✅ Best selector for automation |
| **Total absolute** | `b.c-price__bottom` | "(256 € Total price)" | Total for all pax |
| **precioTotal** | `.precioTotal` or `#side-summary-form\:j_id_4w\:j_id_7l\:precioTotal` | "128 € price per person" | — |

### Upsell Buttons

| Element | Text | Notes |
|---------|------|-------|
| Add Activity | "Add Activity" | Links to activities page |
| Add Transfers | "Add Transfers" | Links to transfers page |
| Add Rental Car | "Add Rental Car" | Links to rental car page |

### DANGER ZONE — Never Click

| Element | Selector | Reason |
|---------|----------|--------|
| **Book now** | sidebar `.c-button.bg-clr--success` | Creates real booking |
| **Confirm trip** | bottom of page, text "Confirm trip" | Proceeds to passengers/payment |

---

## 6. All `dev-` CSS Classes Found

### travel-map.xhtml
```
dev--c-loader, dev-accept-all, dev-adult-count, dev-adult-minus,
dev-adult-plus, dev-child-count, dev-child-minus, dev-child-plus,
dev-currency, dev-distribution-room, dev-distribution-selector-total-adults,
dev-go-cookies-details, dev-go-cookies-settings, dev-header-description,
dev-header-user, dev-header-user-name, dev-last-quotes,
dev-login-form-content, dev-microsite-currencies, dev-options-resume,
dev-PERSONALIZED, dev-reject-all, dev-rooms, dev-search-only-stopovers,
dev-travelcLoginPanel, dev-trip-dates, dev-trip-distribution,
dev-trip-nights, dev-waiting-text
```

### summary.xhtml
```
dev-side-summary-priceShown, dev-edit-destinations,
dev-accommodationFacilityFilter-wrapper
```

---

## 7. PrimeFaces Widget Naming Convention

Pattern: `PrimeFaces.widgets.widget_<elementName>`

Example: `PrimeFaces.widgets.widget_adddestination`

---

## 8. Answer: Can You Request Price from Another Search and Display on Another Website?

**Yes, this is feasible.** Here's how:

### Approach A: Playwright / Headless Browser Automation
1. Use Playwright to navigate `momira.travel/travel-map.xhtml`
2. Fill destination, dates, pax programmatically using the selectors above
3. Click "Start Search"
4. Wait for accommodation results to load
5. Read the price from `.dev-side-summary-priceShown` or `b.summary__total-amount__price`
6. Expose that price via your own API / embed on your website

### Approach B: TC API (if available)
- Travel Compositor may have a quoting API (`Book - Multi-engine` tag in Swagger)
- This would be faster and more reliable than browser automation
- Needs further investigation of the TC API schema

### Key Considerations
- **Rate limiting**: TC may block automated requests
- **Session/cookies**: The JSF ViewState requires maintaining session state
- **Authentication**: Requires a valid operator login
- **Currency**: Prices shown depend on the currency setting (€ Euro in sidebar)
- **Caching**: Prices are live quotes — they change with availability
