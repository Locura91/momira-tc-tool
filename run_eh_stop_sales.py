#!/usr/bin/env python3
"""
run_eh_stop_sales.py — the daily Elephant Hills stop-sale check.

Reads each configured tour's availability calendar on ehtms.com, works out which dates inside the
agreed day-61..365 window are closed, and merges those into the matching ClosedTour's modalities
in Travel Compositor.

RUN IT DRY FIRST. `--dry-run` does the whole read and reports exactly what it would write without
touching Travel Compositor, and that is the default. Writing requires `--apply`, explicitly. This
asymmetry is deliberate: a scheduled job that writes by default is one config typo away from
blocking a year of sellable inventory on the wrong product.

    python run_eh_stop_sales.py                 # read + report, writes nothing
    python run_eh_stop_sales.py --apply         # read + write to Travel Compositor
    python run_eh_stop_sales.py --tour CNX-3    # just one tour
    python run_eh_stop_sales.py --headful       # watch the browser do it

MERGE, NEVER REPLACE — inherited, not reimplemented. Every write goes through
stop_sales_tool.apply_to_tour_option, the same audited path the email reader uses: it re-reads the
option's live stop sales, appends, and PUTs the whole option back. Dates blocked by an earlier
email, by a previous run of this script, or by a person working directly in Travel Compositor are
never dropped. An identical range already present is reported as a duplicate, not added twice, so
running this every day is a no-op on the days nothing changed.

NOTHING IS EVER REMOVED HERE. A date that has gone from FULL back to open on ehtms.com is NOT
unblocked by this script. Releasing inventory is a decision with revenue consequences and the
supplier's calendar going green is not, on its own, an instruction to start selling again — the
email reader's supervised release path exists for that. Re-opened dates are reported in the run
log as `now_open_again` so a human can act on them deliberately.

SCHEDULING. Point Windows Task Scheduler at this script once a day. It is safe to run more often;
an unchanged day costs one page load per tour and writes nothing.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Any, Dict, List, Optional

# The Travel Compositor client reads TRAVELC_USERNAME / TRAVELC_PASSWORD / TRAVELC_MICROSITE_ID
# from the environment (tc_http_base.py). Inside the Streamlit app those are already present;
# a standalone script has to load the .env itself, exactly as run_sync_tickets.py does. Without
# this the auth POST goes out with empty credentials and Travel Compositor answers 400
# "username: must not be empty", which reads like a credentials problem rather than a missing
# load_dotenv() call.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MODULE_BUILD = "2026-10-07-eh-website-stop-sale-reader"

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "eh_stop_sales_config.json")


# ======================================================================
# Config
# ======================================================================
def load_config(path: str) -> Dict[str, Any]:
    """The tour list: which ehtms.com URL belongs to which ClosedTour, and which modalities.

    Kept as a file rather than hardcoded because the URLs carry per-package tokens that the
    supplier can rotate, and because adding a fifth tour should not require editing code."""
    if not os.path.exists(path):
        raise SystemExit(
            f"No config at {path}.\n"
            f"Copy eh_stop_sales_config.example.json to eh_stop_sales_config.json and fill in "
            f"the four tours' TC codes and their ehtms.com URLs.")
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    tours = cfg.get("tours") or []
    if not tours:
        raise SystemExit(f"{path} has no 'tours' entries.")
    for i, t in enumerate(tours):
        for required in ("tour_code", "url"):
            if not t.get(required):
                raise SystemExit(f"{path}: tours[{i}] is missing '{required}'.")
    return cfg


def _modalities_for(tour_cfg: Dict[str, Any], options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Which of the tour's modalities this closure applies to.

    Default is ALL of them, because a date the supplier has closed is closed whichever modality
    we sell it under — the camp is full, not the English-speaking-guide variant of it. A tour can
    narrow this with an explicit "modality_codes" list when that genuinely is not true."""
    wanted = tour_cfg.get("modality_codes")
    if not wanted:
        return options
    wanted_set = {str(c).strip() for c in wanted}
    picked = [o for o in options if str(o.get("code") or "").strip() in wanted_set]
    missing = wanted_set - {str(o.get("code") or "").strip() for o in picked}
    if missing:
        print(f"    WARNING: configured modality code(s) {sorted(missing)} do not exist on this "
              f"tour - they were skipped, nothing was written for them")
    return picked


# ======================================================================
# The run
# ======================================================================
def check_tour(page, tour_cfg: Dict[str, Any], today: Optional[_dt.date]) -> Dict[str, Any]:
    """Read one tour's calendar. No Travel Compositor contact at all.

    The scan starts the day after this tour's own release period ends - 45 days on the CNX tours,
    60 on the HKT ones - because dates inside the release period are not ours to sell and a FULL
    day there is not a closure worth writing."""
    import eh_availability

    from_day = eh_availability.first_scan_day(tour_cfg.get("release_days"))
    reading = eh_availability.stop_sales_for_tour(page, tour_cfg["url"], today=today,
                                                  from_day=from_day)
    reading["tour_code"] = tour_cfg["tour_code"]
    reading["from_day"] = from_day
    reading["release_days"] = tour_cfg.get("release_days")
    return reading


def apply_tour(client, supplier_id: str, tour_cfg: Dict[str, Any],
               ranges: List[Dict[str, str]], dry_run: bool) -> List[Dict[str, Any]]:
    """Merge one tour's new ranges onto its modalities, via the email reader's audited path."""
    import stop_sales_tool as sst

    tour_code = tour_cfg["tour_code"]
    fetched = sst.fetch_closed_tour_options(client, supplier_id, tour_code)
    if "error" in fetched:
        return [{"status": "failed", "code": "(tour)",
                 "detail": f"could not fetch {tour_code}: {fetched['error']}"}]
    options = _modalities_for(tour_cfg, fetched.get("options") or [])
    if not options:
        return [{"status": "failed", "code": "(tour)",
                 "detail": f"{tour_code} has no matching modalities to write to"}]

    results = []
    for option in options:
        if option.get("_fetch_error"):
            results.append({"status": "failed", "code": option.get("code"),
                            "detail": "this modality could not be read from Travel Compositor, so "
                                      "merging into it would have overwritten unknown live data"})
            continue
        if dry_run:
            live = sst.existing_tour_stop_sales(option)
            import stop_sales_parser as ssp
            preview = ssp.merge_stop_sales(live, ranges)
            results.append({"status": "would-add" if preview["added"] else "unchanged",
                            "code": option.get("code"), "changed": preview["added"]})
        else:
            results.append(sst.apply_to_tour_option(client, supplier_id, tour_code, option, ranges))
    return results


def _fmt(ranges: List[Dict[str, str]]) -> str:
    if not ranges:
        return "none"
    return ", ".join(r["start"] if r["start"] == r["end"] else f"{r['start']}..{r['end']}"
                     for r in ranges)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily Elephant Hills stop-sale check.")
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    ap.add_argument("--apply", action="store_true",
                    help="actually write to Travel Compositor (default is a dry run)")
    ap.add_argument("--tour", action="append", default=None,
                    help="only this tour_code; repeatable")
    ap.add_argument("--headful", action="store_true", help="show the browser window")
    ap.add_argument("--today", default=None,
                    help="override today's date (YYYY-MM-DD), for testing the window")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    supplier_id = cfg.get("supplier_id") or "MOMIRA_TH_EH"
    tours = cfg["tours"]
    if args.tour:
        wanted = {t.strip() for t in args.tour}
        tours = [t for t in tours if t["tour_code"] in wanted]
        if not tours:
            raise SystemExit(f"No configured tour matches {sorted(wanted)}.")

    today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()
    dry_run = not args.apply

    import eh_availability
    print(f"Elephant Hills stop-sale check  [{MODULE_BUILD}]")
    print(f"  today: {today}   horizon: day 1-{eh_availability.SCAN_TO_DAY}; each tour is scanned "
          f"from the day after its own release period ends")
    print(f"  mode:  {'DRY RUN - nothing will be written' if dry_run else 'APPLY - will write to Travel Compositor'}")
    print()

    # Playwright's own import pulls in greenlet and pyee, either of which can fail for reasons
    # that have nothing to do with playwright being absent. Reporting "not installed" for all of
    # them sends people to re-run an install that already succeeded, so the real error is shown.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            f"Could not import Playwright: {type(exc).__name__}: {exc}\n\n"
            f"If it genuinely is not installed:\n"
            f"    pip install playwright\n"
            f"    python -m playwright install chromium\n"
            f"If 'python -m playwright install chromium' already worked, playwright IS installed "
            f"and the import above failed for another reason - the message names it.\n"
            f"Interpreter in use: {sys.executable}")

    readings: List[Dict[str, Any]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headful)
        try:
            page = browser.new_page()
            for tour_cfg in tours:
                print(f"  {tour_cfg['tour_code']}")
                try:
                    reading = check_tour(page, tour_cfg, today)
                except Exception as exc:
                    print(f"    FAILED to read the calendar: {exc}")
                    # A read failure is the one case where a picture is worth far more than the
                    # message: the page is a React app whose shape we cannot inspect after the
                    # fact, and the failure mode ("no calendar appeared") looks identical whether
                    # the click missed, the page redirected, or the supplier changed the markup.
                    shot = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        f"eh_debug_{tour_cfg['tour_code']}.png")
                    try:
                        page.screenshot(path=shot, full_page=True)
                        print(f"    a screenshot of what it was looking at: {shot}")
                        print(f"    page URL at the time: {page.url}")
                    except Exception:
                        pass
                    readings.append({"tour_code": tour_cfg["tour_code"], "error": str(exc)})
                    continue
                rel = reading.get("release_days")
                rel_label = (f"{rel}-day release" if rel is not None
                             else f"no release_days set, assuming {eh_availability.DEFAULT_RELEASE_DAYS}")
                print(f"    {rel_label}, so scanning day {reading['from_day']}-"
                      f"{eh_availability.SCAN_TO_DAY}")
                print(f"    to block:  {_fmt(reading['ranges'])}")
                if reading["skipped_near_term"]:
                    print(f"    seen but NOT written (inside the release period, days 1-"
                          f"{reading['from_day'] - 1}): {_fmt(reading['skipped_near_term'])}")
                readings.append(reading)
        finally:
            browser.close()

    to_write = [r for r in readings if not r.get("error") and r.get("ranges")]
    if not to_write:
        print("\nNothing to write.")
        return 0

    # api_client.TravelCompositorAPI, NOT travelcompositor_api.TravelCompositorAPI. This repo has
    # two separate clients (see the NOTE at the top of translation_tool.py); app.py builds the
    # api_client one and hands it to stop_sales_tool, so every function this script reuses from
    # there is written against that client. Using the other one is not a style preference - it is
    # a different object with a different method surface.
    client = None
    if not dry_run:
        from api_client import TravelCompositorAPI
        client = TravelCompositorAPI()

    print()
    exit_code = 0
    for reading in to_write:
        tour_cfg = next(t for t in tours if t["tour_code"] == reading["tour_code"])
        print(f"  {reading['tour_code']} -> {_fmt(reading['ranges'])}")
        if dry_run:
            # A dry run still needs the live record to say anything useful, so it reads Travel
            # Compositor; it just never PUTs. Without a client that read is impossible, so the
            # dry run creates one too.
            from api_client import TravelCompositorAPI
            client = client or TravelCompositorAPI()
        for res in apply_tour(client, supplier_id, tour_cfg, reading["ranges"], dry_run):
            status = res.get("status")
            detail = res.get("detail") or _fmt(res.get("changed") or [])
            print(f"    [{status}] {res.get('code')}: {detail}")
            if status == "failed":
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
