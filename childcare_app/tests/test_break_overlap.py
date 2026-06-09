"""
tests/test_break_overlap.py
Unit tests for per-educator break overlap detection in auto_roster_engine.
"""
import sys, os, types

# ── Minimal stubs so imports don't need real Streamlit or Supabase ────────────
for mod in ["streamlit", "supabase", "dotenv", "pandas"]:
    if mod not in sys.modules:
        m = types.ModuleType(mod)
        if mod == "streamlit":
            m.cache_resource = lambda f: f
            m.secrets        = {}
            m.session_state  = {}
        if mod == "dotenv":
            m.load_dotenv = lambda: None
        if mod == "pandas":
            m.DataFrame = list
        sys.modules[mod] = m

sc = types.ModuleType("utils.supabase_client")
sc.get_supabase_client = lambda: None
sc.get_organisation_id = lambda: "test-org"
sys.modules["utils.supabase_client"] = sc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auto_roster_engine import (
    _overlaps,
    _check_break_impact,
    _find_alt_break_window,
)


def _empty_coverage(room_id="room-1"):
    slots = [
        f"{h:02d}:{m:02d}:00"
        for h in range(6, 21)
        for m in (0, 15, 30, 45)
    ]
    return {room_id: {s: 2 for s in slots}}


class TestOverlaps:

    def test_clear_overlap(self):
        assert _overlaps("12:21:00", "13:01:00", "12:30:00", "12:40:00") is True

    def test_adjacent_no_overlap(self):
        assert _overlaps("12:00:00", "12:30:00", "12:30:00", "13:00:00") is False

    def test_before_no_overlap(self):
        assert _overlaps("10:00:00", "10:30:00", "11:00:00", "11:30:00") is False

    def test_after_no_overlap(self):
        assert _overlaps("14:00:00", "14:30:00", "12:00:00", "12:40:00") is False

    def test_contained(self):
        assert _overlaps("12:00:00", "13:00:00", "12:20:00", "12:40:00") is True

    def test_symmetric(self):
        a, b = "12:21:00", "13:01:00"
        c, d = "12:30:00", "12:40:00"
        assert _overlaps(a, b, c, d) == _overlaps(c, d, a, b)


class TestCheckBreakImpact:

    UID     = "user-1"
    RID     = "room-1"
    R_STAFF = 1
    R_CHILD = 4

    def _run(self, b_start, b_end, user_breaks, fixed=False):
        breaks_by_user = {self.UID: [(s, e, fixed) for s, e in user_breaks]}
        return _check_break_impact(
            b_start, b_end,
            self.RID, self.UID,
            _empty_coverage(self.RID),
            {},
            breaks_by_user,
            self.R_STAFF, self.R_CHILD,
        )

    def test_bug_report_overlap_detected(self):
        """12:21–13:01 and 12:30–12:40 overlap and must be detected."""
        conflict, reason = self._run(
            "12:30:00", "12:40:00",
            [("12:21:00", "13:01:00")],
        )
        assert conflict == "breach", f"Expected 'breach', got '{conflict}': {reason}"

    def test_non_overlapping_ok(self):
        """11:00–11:10 and 12:30–13:10 do not overlap."""
        conflict, _ = self._run(
            "12:30:00", "13:10:00",
            [("11:00:00", "11:10:00")],
        )
        assert conflict == "ok"

    def test_adjacent_breaks_ok(self):
        """11:00–11:10 and 11:10–11:40 are adjacent, not overlapping."""
        conflict, _ = self._run(
            "11:10:00", "11:40:00",
            [("11:00:00", "11:10:00")],
        )
        assert conflict == "ok"

    def test_fixed_break_causes_fixed_conflict(self):
        """Overlap with a fixed break returns 'fixed_conflict', not 'breach'."""
        conflict, reason = self._run(
            "12:30:00", "12:40:00",
            [("12:21:00", "13:01:00")],
            fixed=True,
        )
        assert conflict == "fixed_conflict", (
            f"Expected 'fixed_conflict', got '{conflict}': {reason}"
        )

    def test_non_fixed_overlap_is_breach(self):
        """Overlap with a non-fixed break returns 'breach'."""
        conflict, _ = self._run(
            "12:30:00", "12:40:00",
            [("12:21:00", "13:01:00")],
            fixed=False,
        )
        assert conflict == "breach"

    def test_different_educator_no_interference(self):
        """A break for another educator must not block this educator."""
        breaks_by_user = {"user-2": [("12:21:00", "13:01:00", False)]}
        conflict, _ = _check_break_impact(
            "12:30:00", "12:40:00",
            self.RID, self.UID,
            _empty_coverage(self.RID),
            {},
            breaks_by_user,
            self.R_STAFF, self.R_CHILD,
        )
        assert conflict == "ok"

    def test_same_educator_multiple_breaks(self):
        """New break overlapping one of two existing breaks is blocked."""
        existing = [("10:00:00", "10:10:00"), ("15:00:00", "15:30:00")]
        conflict_yes, _ = self._run("10:05:00", "10:35:00", existing)
        assert conflict_yes == "breach"
        conflict_no, _  = self._run("13:00:00", "13:40:00", existing)
        assert conflict_no == "ok"


class TestFindAltBreakWindow:

    UID     = "user-1"
    RID     = "room-1"
    R_STAFF = 1
    R_CHILD = 4

    def test_finds_clear_slot(self):
        breaks_by_user = {self.UID: [("12:00:00", "12:40:00", False)]}
        alt_s, alt_e, conflict = _find_alt_break_window(
            "08:00:00", "16:00:00", 10,
            self.RID, self.UID,
            _empty_coverage(self.RID),
            {},
            breaks_by_user,
            self.R_STAFF, self.R_CHILD,
        )
        assert not conflict
        assert not _overlaps(alt_s, alt_e, "12:00:00", "12:40:00")

    def test_no_slot_when_fully_blocked(self):
        breaks_by_user = {self.UID: [("08:00:00", "16:01:00", True)]}
        _, _, still_conflict = _find_alt_break_window(
            "08:00:00", "16:00:00", 10,
            self.RID, self.UID,
            _empty_coverage(self.RID),
            {},
            breaks_by_user,
            self.R_STAFF, self.R_CHILD,
        )
        assert still_conflict


if __name__ == "__main__":
    import traceback
    classes = [TestOverlaps(), TestCheckBreakImpact(), TestFindAltBreakWindow()]
    passed = failed = 0
    for obj in classes:
        for name in [m for m in dir(obj) if m.startswith("test_")]:
            try:
                getattr(obj, name)()
                print(f"  [PASS] {type(obj).__name__}.{name}")
                passed += 1
            except Exception:
                print(f"  [FAIL] {type(obj).__name__}.{name}")
                traceback.print_exc()
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
