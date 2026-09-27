# Social kit — drop-in module for the Momira TC platform

A Holiday Package ID goes in; three captions and a finished JPG come out, at
the exact size Instagram, TikTok or Google Business Profile wants — written
and priced for whichever brand you pick.

This is a port of the tool built into the multiwander.com WordPress plugin,
now speaking for **both** brands that sell the same Travel Compositor
inventory:

| brand | language | currency | wordmark on the image |
|---|---|---|---|
| Momira Travel | English | € | MOMIRA TRAVEL |
| MultiWander | Polish | zł | MULTIWANDER |

Pick the brand in the UI and everything else follows automatically: Travel
Compositor is asked for the package in that brand's language (so the
destination names, themes and description come back already translated),
the caption is written in that language, the price is quoted in that
currency, and the wordmark, CTA and hashtags on the image change with it.

---

## Files

```
social_kit.py          all the logic: TC client, normaliser, brands, captions, renderer
social_kit_ui.py       the Streamlit screen (one function, no other coupling)
fonts/mw-bold.ttf      Carlito subset — every Polish diacritic, OFL, 110 KB
fonts/mw-regular.ttf
fonts/LICENSE.txt
```

Copy all of it into the project root, keeping `fonts/` next to `social_kit.py`.

## Requirements

`requests` and `Pillow`. Both are almost certainly already installed —
`image_dimensions.py` implies Pillow, and `api_client.py` implies requests. If
not:

```
pip install Pillow requests
```

## Wiring it in

One line where the wizard's product types are listed, and one call:

```python
import social_kit_ui

# …alongside "ClosedTour", "Ticket", "Transfer", "Transport", "Hotel":
if product_type == "Social kit":
    social_kit_ui.render_social_kit()
    st.stop()
```

It reads the same four secrets `app.py` already reads —
`TRAVELC_BASE_URL`, `TRAVELC_MICROSITE_ID`, `TRAVELC_USERNAME`,
`TRAVELC_PASSWORD` — so there is nothing new to configure. Both brands sell
through the same Travel Compositor microsite; only the language parameter
sent with each call changes.

All session state is prefixed `sk_`, so it cannot collide with the wizard's
own keys.

## The two brands

Everything brand-specific lives in one place — the `Brand` dataclass and the
`BRANDS` dictionary near the top of `social_kit.py`:

```python
BRANDS = {
    "momira":      Brand(key="momira", name="Momira Travel", lang="en",
                          tc_lang="EN", currency="EUR", site="momira.travel",
                          wordmark="MOMIRA TRAVEL", url="https://momira.travel/",
                          tags=(...)),
    "multiwander": Brand(key="multiwander", name="MultiWander", lang="pl",
                          tc_lang="PL", currency="PLN", site="multiwander.com",
                          wordmark="MULTIWANDER", url="https://multiwander.com/pakiety-podrozy/",
                          tags=(...)),
}
```

`tc_lang` is what actually changes the Travel Compositor response — request
the package in `"EN"` and the title, destinations, themes and description
all come back in English, no translation step needed on this side.
`currency` decides whether the price is shown as-is (Momira: euro, straight
from Travel Compositor) or converted (MultiWander: złoty, using the
EUR → PLN rate entered in the UI — see below).

**Adding a third brand is a dictionary entry, not a branch in the code.**
Every caption string, hashtag set and poster label is looked up through
`_t(brand, key)` against a `_COPY` dictionary keyed by language, so a new
brand in an already-supported language (say, a second English-speaking
brand) needs nothing added to `_COPY` at all — just a new `Brand(...)` entry.
A genuinely new language needs its `_MONTHS`, `_HOOKS` and `_COPY` entries
filled in alongside the existing `"pl"`/`"en"` ones.

### Currency conversion

Travel Compositor prices everything in euro internally. Momira Travel quotes
that figure directly. MultiWander quotes złoty, so the UI asks for an
EUR → PLN rate (defaulting to 4.30, editable each time) and multiplies —
**only when a rate is given**. Leave it at zero and MultiWander's post shows
the euro figure rather than a wrong conversion; a bad rate is worse than an
honest one in the wrong currency.

### Caching

The package is cached per **brand and ID together**
(`sk_cache["multiwander:63989764"]` and `sk_cache["momira:63989764"]` are two
different entries), because the same ID is genuinely a different payload in
each language — different destination names, different description, in
principle even different themes. Switching the photograph or the framing
never re-fetches; switching the brand does, once, then it's cached too.

## Why it does not use `api_client.py`

`api_client.py` exists to **write** products into Travel Compositor. The three
endpoints needed here only **read** a published holiday package:

```
GET /package/{micrositeId}/info/{id}
GET /package/{micrositeId}/{id}
GET /package/calendar/{micrositeId}/{id}
```

None of them overlap with anything the uploader does, so adding them there
would buy nothing and put the upload path at risk for a reporting feature.
`TCClient` in `social_kit.py` is about forty lines and handles the one thing
that catches everybody out: **the auth token comes back as a response header
called `auth-token`, and goes back out as a request header of the same name.**
Not `Authorization: Bearer`.

## Using it as a library

The UI is optional. The logic has no Streamlit import:

```python
import social_kit as sk

brand = sk.BRANDS["momira"]                    # or sk.BRANDS["multiwander"]
pack = sk.fetch(sk.TCClient(), "63989764", brand)

texts = sk.captions(pack, brand.url, brand, pln_rate=4.30)
print(texts["inspiracja"])

photo = sk.load_photo(pack.gallery[0])
image = sk.render(pack, "story", "photo", photo, focus="center", zoom=1.0,
                   brand=brand, pln_rate=4.30)
open("post.jpg", "wb").write(sk.to_jpeg(image))
```

Leaving `brand` out anywhere defaults to `sk.DEFAULT_BRAND` (MultiWander, to
match the original single-brand tool), so existing scripts written against
the first version of this module keep working unchanged.

That makes a scheduled job — a week of posts every Monday, for both brands —
a short script rather than a second implementation:

```python
for brand in sk.BRANDS.values():
    pack = sk.fetch(sk.TCClient(), package_id, brand)
    texts = sk.captions(pack, brand.url, brand, pln_rate=4.30)
    ...
```

## Two things worth knowing

**Photograph size.** Travel Compositor's images are about 900 × 720 pixels and
there is no larger original behind them (measured, not assumed). A 1080 × 1920
story cannot be filled without enlarging 2.6× and cutting away two thirds of
the width. So above a 1.45× enlargement the renderer stops cropping and shows
the picture whole over a blurred, darkened copy of itself — the treatment
Instagram uses for the same problem. Nothing is ever stretched.

**The font.** Carlito is bundled because drawing text server-side needs a real
font file and Carlito is OFL-licensed with full Polish coverage. It is not the
brand face. Drop `poppins-bold.ttf` and `poppins-regular.ttf` into `fonts/`
and they take over automatically — no setting, no code change.

## What the captions contain

Three, because they do different jobs:

| key | for | leads with |
|---|---|---|
| `inspiracja` | the feed | a line written for the trip's theme |
| `konkret` | people already shopping | the price |
| `gbp` | Google Business Profile | the facts, no hashtags, 1500 chars |

All three end with the same two things: the promise that the itinerary is
changeable (length, departure airport, hotels, order of stops) and the link.
That combination is what turns a scroll into an enquiry — a reader who thinks
the package is fixed either buys it or leaves, and usually leaves.

Every fact in them comes from the package, in the brand's own language and
currency. Nothing is invented, which is what makes the output safe to post
without reading it first.
