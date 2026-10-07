#!/usr/bin/env python3
"""
eh_find_ids.py — one-off helper: find the Travel Compositor supplier ID and ClosedTour codes
that eh_stop_sales_config.json needs.

WHY: GET /closedtour/{supplierId}/{code} returned 404 for supplier "MOMIRA_TH_EH". The
/suppliers endpoint returns ContractSupplierVO records whose identifier is a numeric 'id'
(api_client.get_suppliers' own docstring: "instead of requiring people to know/type numeric
supplier IDs by heart"), so the human-readable name is very likely not what the path wants.

This only ever READS. It lists suppliers, shows the ones whose name looks like Elephant Hills,
and for each candidate lists that supplier's ClosedTours with their real codes. Nothing is
written anywhere.

    python eh_find_ids.py
    python eh_find_ids.py --search elephant      # narrow the supplier name match
    python eh_find_ids.py --all                  # dump every supplier, if the search misses
"""
from __future__ import annotations

import argparse
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# api_client.TravelCompositorAPI, NOT travelcompositor_api.TravelCompositorAPI. There are two
# separate clients in this repo (see the NOTE at the top of translation_tool.py); app.py builds
# the api_client one and hands it to stop_sales_tool, so that is the client every stop-sale code
# path is written against. This helper uses the same one so that what it discovers here is
# guaranteed to be what the real run sees.
from api_client import TravelCompositorAPI


def _rows(result):
    """The API returns either a bare list or a dict wrapping one, depending on account/version."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("supplier", "closedTour", "closedtour", "content", "items", "results"):
            value = result.get(key)
            if isinstance(value, list):
                return value
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", default="elephant",
                    help="substring to look for in the supplier name (default: elephant)")
    ap.add_argument("--all", action="store_true", help="list every supplier instead of searching")
    args = ap.parse_args(argv)

    api = TravelCompositorAPI()

    print("Fetching the supplier list...")
    suppliers = _rows(api.get_all_suppliers())
    if not suppliers:
        print("No suppliers came back. Check TRAVELC_MICROSITE_ID in .env.")
        return 1
    print(f"{len(suppliers)} supplier(s) on this microsite.\n")

    needle = args.search.strip().lower()
    candidates = []
    for s in suppliers:
        if not isinstance(s, dict):
            continue
        name = " ".join(str(s.get(k) or "") for k in
                        ("commercialName", "legalName", "name", "code"))
        if args.all or (needle and needle in name.lower()):
            candidates.append(s)

    if not candidates:
        print(f"Nothing matched {args.search!r}. Re-run with --all to see every supplier, "
              f"or --search with a different word.")
        return 1

    for s in candidates:
        sid = s.get("id")
        label = s.get("commercialName") or s.get("legalName") or s.get("name") or "(unnamed)"
        print(f"SUPPLIER  id={sid}   {label}")
        if args.all:
            continue
        tours = _rows(api.get_closed_tours(str(sid)))
        if not tours:
            print("    (no closed tours, or the list call failed for this supplier)")
            continue
        for t in tours:
            if isinstance(t, dict):
                print(f"    tour_code={t.get('code')!r:<24} {t.get('name') or ''}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
