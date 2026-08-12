"""Tests for prune_old_mappings() in extraction_memory.py.

INVESTIGATION BEFORE BUILDING (2026-08-12): the "Automatic pruning of learned corrections"
issue proposed two deletion rules - age (last_seen older than max_age_days) and a count
threshold (delete anything below the confirmation count, since it's "never applied
anyway"). Reading record_corrections()/render_memory_panel()'s existing design showed the
count rule would break the module's core mechanism: a mapping below the confirmation
threshold is deliberately left in place so a SECOND document can confirm it into a real
rule - one correction is as likely to be a typo as a pattern, two is a pattern. Deleting on
count alone means a correction recorded once today gets wiped before a second contract ever
arrives to confirm it. These tests pin the decision that was actually shipped: pruning is
age-only, so a fresh single-count correction always survives regardless of its count, and a
stale mapping is removed regardless of whether it was ever confirmed.
"""
from datetime import datetime, timedelta, timezone

import extraction_memory as em


def _iso_days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _write_mapping(supplier_id, product_type, field, from_key, count, last_seen, first_seen=None):
    row = em._load(supplier_id, product_type)
    row.setdefault("fields", {})[field] = {
        from_key: {
            "from": from_key, "to": f"{from_key}-corrected", "count": count,
            "first_seen": first_seen or last_seen, "last_seen": last_seen, "examples": [],
        }
    }
    assert em._save(supplier_id, product_type, row)


def test_a_fresh_single_correction_survives_pruning_even_though_unconfirmed():
    """The exact scenario the issue's own count-based rule would have broken: one
    correction, made moments ago, count=1 (below _APPLY_AFTER). Must NOT be pruned even
    at an aggressive max_age_days, because it hasn't had a chance to go stale yet."""
    em.record_corrections("SUP-FRESH", "transfer", {"pickup": "Hotel"}, {"pickup": "Hotel Lobby"})

    removed = em.prune_old_mappings(max_age_days=1)

    assert removed == 0
    rows = em.list_learned("SUP-FRESH", "transfer")
    assert len(rows) == 1
    assert rows[0]["count"] == 1


def test_a_stale_mapping_is_removed_regardless_of_confirmation_count():
    """A mapping confirmed 5 times but not seen again in 200 days is exactly the "supplier
    changed their rate-sheet style" case the issue was written for - must be removed even
    though it's ACTIVE (count >= _APPLY_AFTER), because age, not confirmation count, is
    the pruning signal."""
    _write_mapping("SUP-STALE", "transfer", "vehicle", "car", count=5, last_seen=_iso_days_ago(200))

    removed = em.prune_old_mappings(max_age_days=90)

    assert removed == 1
    assert em.list_learned("SUP-STALE", "transfer") == []


def test_a_recently_confirmed_mapping_survives_regardless_of_age_of_first_use():
    """first_seen can be old while last_seen is recent (a rule the supplier keeps
    confirming over a long time) - must survive, since it's still in active use."""
    _write_mapping("SUP-LONGLIVED", "transfer", "vehicle", "car", count=10,
                   first_seen=_iso_days_ago(400), last_seen=_iso_days_ago(2))

    removed = em.prune_old_mappings(max_age_days=90)

    assert removed == 0
    assert len(em.list_learned("SUP-LONGLIVED", "transfer")) == 1


def test_prune_only_removes_the_stale_mapping_not_the_whole_field_or_supplier():
    """A field bucket with one stale and one fresh mapping must lose only the stale one -
    pruning must not collapse siblings that happen to share a field name."""
    row = em._load("SUP-MIXED", "transfer")
    row.setdefault("fields", {})["vehicle"] = {
        "car": {"from": "Car", "to": "Sedan", "count": 3,
               "first_seen": _iso_days_ago(200), "last_seen": _iso_days_ago(200), "examples": []},
        "van": {"from": "Van", "to": "Minivan", "count": 3,
               "first_seen": _iso_days_ago(1), "last_seen": _iso_days_ago(1), "examples": []},
    }
    em._save("SUP-MIXED", "transfer", row)

    removed = em.prune_old_mappings(max_age_days=90)

    assert removed == 1
    remaining = em.list_learned("SUP-MIXED", "transfer")
    assert len(remaining) == 1
    assert remaining[0]["from"] == "Van"


def test_prune_records_when_it_last_ran_and_how_many_it_removed():
    # NOTE: last_prune_info() is a single global row (not per-supplier), and this suite
    # shares one platform_store across the whole pytest session (see conftest.py), so an
    # earlier test in this file may already have run a prune - don't assert it starts at
    # None, just that THIS run's result is what gets recorded afterwards.
    _write_mapping("SUP-META", "transfer", "vehicle", "car", count=1, last_seen=_iso_days_ago(200))
    removed = em.prune_old_mappings(max_age_days=90)

    info = em.last_prune_info()
    assert info is not None
    assert info["removed"] == removed
    assert info["max_age_days"] == 90
    assert info["last_pruned_at"]  # a timestamp was stamped


def test_prune_with_nothing_stale_removes_nothing_and_still_records_a_run():
    em.record_corrections("SUP-NOOP", "transfer", {"pickup": "Hotel"}, {"pickup": "Hotel Lobby"})

    removed = em.prune_old_mappings(max_age_days=90)

    assert removed == 0
    assert em.last_prune_info()["removed"] == 0
