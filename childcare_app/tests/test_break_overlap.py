"""
tests/test_break_overlap.py
Unit tests for per-educator break overlap detection and roster optimisation
rules in auto_roster_engine.

Run:
    cd childcare_app && python3 tests/test_break_overlap.py
    # or:  pytest tests/test_break_overlap.py -v
"""
import sys, os, types

# ── Minimal stubs ─────────────────────────────────────────────────────────────
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
    # break-overlap helpers
    _overlaps,
    _check_break_impact,
    _find_alt_break_window,
    # roster optimisation helpers
    EMPLOYMENT_PRIORITY,
    CASUAL_MIN_SHIFT_MINUTES,
    CENTRE_OPEN,
    CENTRE_CLOSE,
    _eligible_staff,
    _pick_staff,
    _check_centre_coverage,
    CoverageWindow,
    SuggestedShift,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _empty_coverage(room_id="room-1"):
    """Coverage map with 2 staff per slot — ratio never a concern."""
    slots = [
        f"{h:02d}:{m:02d}:00"
        for h in range(6, 21)
        for m in (0, 15, 30, 45)
    ]
    return {room_id: {s: 2 for s in slots}}


def _make_staff(uid, etype, primary_room=None, name=None):
    return {
        "uid":             uid,
        "name":            name or uid,
        "employment_type": etype,
        "primary_room_id": primary_room,
        "allows_opt_out":  False,
    }


def _make_shift(uid, start, end, date="2026-01-01", room="room-1"):
    return SuggestedShift(
        user_id=uid, user_name=uid,
        room_id=room, room_name="Room",
        shift_date=date,
        start_time=start, end_time=end,
        shift_type="standard",
        break_opt_out_override="use_staff_default",
        source="available",
    )


def _window(start, end, req=1):
    return CoverageWindow(start=start, end=end, required_staff=req, peak_children=4)


# ─────────────────────────────────────────────────────────────────────────────
# _overlaps
# ─────────────────────────────────────────────────────────────────────────────

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
        a, b, c, d = "12:21:00", "13:01:00", "12:30:00", "12:40:00"
        assert _overlaps(a, b, c, d) == _overlaps(c, d, a, b)


# ─────────────────────────────────────────────────────────────────────────────
# _check_break_impact — educator overlap
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckBreakImpact:

    UID = "user-1"
    RID = "room-1"

    def _run(self, b_start, b_end, user_breaks, fixed=False):
        breaks_by_user = {self.UID: [(s, e, fixed) for s, e in user_breaks]}
        return _check_break_impact(
            b_start, b_end, self.RID, self.UID,
            _empty_coverage(self.RID), {}, breaks_by_user, 1, 4,
        )

    def test_bug_report_overlap_detected(self):
        """12:21–13:01 and 12:30–12:40 overlap and must be detected."""
        conflict, reason = self._run("12:30:00", "12:40:00",
                                     [("12:21:00", "13:01:00")])
        assert conflict == "breach", f"Expected 'breach', got '{conflict}': {reason}"

    def test_non_overlapping_ok(self):
        conflict, _ = self._run("12:30:00", "13:10:00",
                                [("11:00:00", "11:10:00")])
        assert conflict == "ok"

    def test_adjacent_breaks_ok(self):
        conflict, _ = self._run("11:10:00", "11:40:00",
                                [("11:00:00", "11:10:00")])
        assert conflict == "ok"

    def test_fixed_break_causes_fixed_conflict(self):
        conflict, reason = self._run("12:30:00", "12:40:00",
                                     [("12:21:00", "13:01:00")], fixed=True)
        assert conflict == "fixed_conflict", (
            f"Expected 'fixed_conflict', got '{conflict}': {reason}"
        )

    def test_non_fixed_overlap_is_breach(self):
        conflict, _ = self._run("12:30:00", "12:40:00",
                                [("12:21:00", "13:01:00")], fixed=False)
        assert conflict == "breach"

    def test_different_educator_no_interference(self):
        breaks_by_user = {"user-2": [("12:21:00", "13:01:00", False)]}
        conflict, _ = _check_break_impact(
            "12:30:00", "12:40:00", self.RID, self.UID,
            _empty_coverage(self.RID), {}, breaks_by_user, 1, 4,
        )
        assert conflict == "ok"

    def test_same_educator_multiple_breaks(self):
        existing = [("10:00:00", "10:10:00"), ("15:00:00", "15:30:00")]
        c_yes, _ = self._run("10:05:00", "10:35:00", existing)
        assert c_yes == "breach"
        c_no, _  = self._run("13:00:00", "13:40:00", existing)
        assert c_no == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# _find_alt_break_window
# ─────────────────────────────────────────────────────────────────────────────

class TestFindAltBreakWindow:

    UID = "user-1"
    RID = "room-1"

    def test_finds_clear_slot(self):
        breaks_by_user = {self.UID: [("12:00:00", "12:40:00", False)]}
        alt_s, alt_e, conflict = _find_alt_break_window(
            "08:00:00", "16:00:00", 10, self.RID, self.UID,
            _empty_coverage(self.RID), {}, breaks_by_user, 1, 4,
        )
        assert not conflict
        assert not _overlaps(alt_s, alt_e, "12:00:00", "12:40:00")

    def test_no_slot_when_fully_blocked(self):
        breaks_by_user = {self.UID: [("08:00:00", "16:01:00", True)]}
        _, _, still_conflict = _find_alt_break_window(
            "08:00:00", "16:00:00", 10, self.RID, self.UID,
            _empty_coverage(self.RID), {}, breaks_by_user, 1, 4,
        )
        assert still_conflict


# ─────────────────────────────────────────────────────────────────────────────
# Employment-type priority ordering
# ─────────────────────────────────────────────────────────────────────────────

class TestEmploymentPriority:

    ROOM = "room-1"

    def _eligible(self, staff_list):
        return _eligible_staff(staff_list, self.ROOM, "2026-01-01", 1, {}, {})

    def test_full_time_before_part_time(self):
        staff  = [_make_staff("pt-1", "part_time"), _make_staff("ft-1", "full_time")]
        uids   = [s["uid"] for s in self._eligible(staff)]
        assert uids.index("ft-1") < uids.index("pt-1"), f"Got {uids}"

    def test_part_time_before_casual(self):
        staff = [_make_staff("ca-1", "casual"), _make_staff("pt-1", "part_time")]
        uids  = [s["uid"] for s in self._eligible(staff)]
        assert uids.index("pt-1") < uids.index("ca-1"), f"Got {uids}"

    def test_full_time_before_casual(self):
        staff = [_make_staff("ca-1", "casual"), _make_staff("ft-1", "full_time")]
        uids  = [s["uid"] for s in self._eligible(staff)]
        assert uids.index("ft-1") < uids.index("ca-1")

    def test_all_three_in_order(self):
        staff = [
            _make_staff("ca-1", "casual"),
            _make_staff("ft-1", "full_time"),
            _make_staff("pt-1", "part_time"),
        ]
        uids = [s["uid"] for s in self._eligible(staff)]
        ft, pt, ca = uids.index("ft-1"), uids.index("pt-1"), uids.index("ca-1")
        assert ft < pt < ca, f"Expected ft<pt<ca, got positions {ft},{pt},{ca}"

    def test_primary_room_beats_employment_type(self):
        """Primary-room casual outranks non-primary full-time."""
        staff = [
            _make_staff("ft-other", "full_time",  primary_room="room-2"),
            _make_staff("ca-prim",  "casual",     primary_room=self.ROOM),
        ]
        uids = [s["uid"] for s in self._eligible(staff)]
        assert uids.index("ca-prim") < uids.index("ft-other")


# ─────────────────────────────────────────────────────────────────────────────
# Casual staff minimum shift length (3 hours)
# ─────────────────────────────────────────────────────────────────────────────

class TestCasualMinShift:

    ROOM = "room-1"
    DATE = "2026-01-01"

    def _pick(self, staff, window):
        return _pick_staff(staff, self.ROOM, self.DATE, window, {}, [])

    def test_casual_skipped_under_3_hours(self):
        result = self._pick(
            [_make_staff("ca-1", "casual")],
            _window("09:00:00", "11:00:00"),   # 120 min
        )
        assert result is None, "Casual must not be assigned < 3 hours"

    def test_casual_ok_at_exactly_3_hours(self):
        result = self._pick(
            [_make_staff("ca-1", "casual")],
            _window("09:00:00", "12:00:00"),   # 180 min
        )
        assert result is not None
        assert result["uid"] == "ca-1"

    def test_casual_ok_for_long_shift(self):
        result = self._pick(
            [_make_staff("ca-1", "casual")],
            _window("07:00:00", "14:00:00"),   # 420 min
        )
        assert result is not None

    def test_full_time_not_affected_by_casual_rule(self):
        result = self._pick(
            [_make_staff("ft-1", "full_time")],
            _window("09:00:00", "10:00:00"),   # 60 min
        )
        assert result is not None

    def test_part_time_not_affected_by_casual_rule(self):
        result = self._pick(
            [_make_staff("pt-1", "part_time")],
            _window("09:00:00", "10:30:00"),   # 90 min
        )
        assert result is not None

    def test_full_time_picked_over_casual_for_short_window(self):
        staff = sorted(
            [_make_staff("ca-1", "casual"), _make_staff("ft-1", "full_time")],
            key=lambda x: EMPLOYMENT_PRIORITY.get(x["employment_type"], 2),
        )
        result = self._pick(staff, _window("10:00:00", "11:30:00"))  # 90 min
        assert result is not None
        assert result["uid"] == "ft-1"


# ─────────────────────────────────────────────────────────────────────────────
# Centre-wide continuous coverage 07:15–18:00
# ─────────────────────────────────────────────────────────────────────────────

class TestCentreCoverage:

    DATE = "2026-01-05"

    def _shifts(self, times, room="room-1"):
        return [
            _make_shift(f"user-{i}", s, e, date=self.DATE, room=room)
            for i, (s, e) in enumerate(times)
        ]

    def test_full_day_no_warnings(self):
        warns = _check_centre_coverage(
            self._shifts([("07:15:00", "18:00:00")]), self.DATE
        )
        assert not warns, f"Expected no warnings, got: {warns}"

    def test_gap_in_middle_flagged(self):
        shifts = self._shifts([("07:15:00", "12:00:00"), ("13:00:00", "18:00:00")])
        warns  = _check_centre_coverage(shifts, self.DATE)
        assert len(warns) == 1
        assert "12:00" in warns[0] and "13:00" in warns[0]

    def test_gap_at_open_flagged(self):
        warns = _check_centre_coverage(
            self._shifts([("08:00:00", "18:00:00")]), self.DATE
        )
        assert any("07:15" in w for w in warns), f"Got: {warns}"

    def test_gap_at_close_flagged(self):
        warns = _check_centre_coverage(
            self._shifts([("07:15:00", "17:00:00")]), self.DATE
        )
        assert any("17:00" in w for w in warns), f"Got: {warns}"

    def test_overlapping_shifts_cover_gap(self):
        warns = _check_centre_coverage(
            self._shifts([("07:15:00", "13:30:00"), ("12:00:00", "18:00:00")]),
            self.DATE,
        )
        assert not warns, f"Overlapping shifts should have no gaps: {warns}"

    def test_multiple_rooms_combined(self):
        s1 = _make_shift("u0", "07:15:00", "13:00:00", date=self.DATE, room="room-1")
        s2 = _make_shift("u1", "13:00:00", "18:00:00", date=self.DATE, room="room-2")
        warns = _check_centre_coverage([s1, s2], self.DATE)
        assert not warns, f"Two rooms should fill the day: {warns}"

    def test_empty_shifts_produce_gap(self):
        warns = _check_centre_coverage([], self.DATE)
        assert len(warns) >= 1
        assert any("07:15" in w for w in warns)


# ─────────────────────────────────────────────────────────────────────────────
# Regression: break overlap check still works after all engine changes
# ─────────────────────────────────────────────────────────────────────────────

class TestBreakOverlapRegression:

    def test_bug_report_case_still_detected(self):
        breaks_by_user = {"user-1": [("12:21:00", "13:01:00", False)]}
        conflict, _ = _check_break_impact(
            "12:30:00", "12:40:00", "room-1", "user-1",
            _empty_coverage("room-1"), {}, breaks_by_user, 1, 4,
        )
        assert conflict == "breach"


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback

    classes = [
        TestOverlaps(),
        TestCheckBreakImpact(),
        TestFindAltBreakWindow(),
        TestEmploymentPriority(),
        TestCasualMinShift(),
        TestCentreCoverage(),
        TestBreakOverlapRegression(),
    ]
    passed = failed = 0
    for obj in classes:
        for name in sorted(m for m in dir(obj) if m.startswith("test_")):
            try:
                getattr(obj, name)()
                print(f"  [PASS] {type(obj).__name__}.{name}")
                passed += 1
            except Exception:
                print(f"  [FAIL] {type(obj).__name__}.{name}")
                traceback.print_exc()
                failed += 1
    print(f"\n{'='*55}")
    print(f"  {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
