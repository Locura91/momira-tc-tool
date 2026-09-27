"""Regression test for a real product-owner bug report (2026-09-27), verbatim: "but now i canot
add the supplement manually, it is blocked. it worked already fine."

Root cause: the same day's earlier fix (see test_2026_09_27_supplement_per_pax_toggle_and_
rounding.py) made the ClosedTour Supplements table's Single/Double/Triple/Quadruple columns
disabled (calculated from Price + Per Pax, not directly typeable). A disabled st.column_config.
NumberColumn with no `default` blocks Streamlit's data_editor from letting a human add a brand
new row at all when num_rows="dynamic" (the default for editable_table) - the new row has no way
to get a value into a disabled cell, so the whole "add row" affordance stops working, not just
editing of the disabled cells themselves. This silently broke adding a NEW supplement entirely
while leaving editing of EXISTING rows unaffected, which is exactly the "it worked already fine"
(for existing data) / "it is blocked" (for a new row) split in the report.

Fix: give each disabled NumberColumn a `default=0` so a newly added row's disabled cells have
something to start from - Save (render_closedtour_supplements's own _save callback) recalculates
all four from Price + Per Pax immediately anyway, so the placeholder default is never actually
published. The same bug shape (disabled column, no default, dynamic rows) also existed on the
three "Itinerary destinations" tables' auto-index "#" column - fixed alongside this one since it's
the identical, zero-risk one-line change, even though it wasn't the reported symptom.
"""
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(os.path.join(_REPO_ROOT, path), "r", encoding="utf-8") as f:
        return f.read()


def test_supplement_occupancy_columns_have_a_default_alongside_disabled():
    src = _read("ui_components.py")
    for col in ("Single", "Double", "Triple", "Quadruple"):
        assert f'"{col}": st.column_config.NumberColumn(disabled=True, default=0,' in src, (
            f"{col} column must have both disabled=True AND default=0 - disabled with no "
            f"default blocks adding a new row entirely in Streamlit's data_editor"
        )


def test_itinerary_destination_index_column_also_has_a_default():
    for path in ("app.py", "flows/multi_tour.py"):
        src = _read(path)
        assert 'st.column_config.NumberColumn(disabled=True, default=0)' in src, (
            f"{path}: the destinations table's disabled '#' index column needs a default too, "
            f"same reasoning as the supplements fix"
        )
        # The old, default-less form must be gone everywhere it's been fixed.
        assert 'column_config={"#": st.column_config.NumberColumn(disabled=True)}' not in src


def test_no_remaining_default_less_disabled_numbercolumn_in_a_dynamic_supplements_table():
    # Belt-and-braces: the specific table this bug was reported on must not regress back to a
    # disabled column with no default.
    src = _read("ui_components.py")
    assert 'st.column_config.NumberColumn(disabled=True, help="Calculated from Price + Per Pax")' not in src
