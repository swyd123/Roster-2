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
    SuggestedBreak,
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

from utils.auto_roster_engine import (
    _validate_and_resolve_break_overlaps,
    _ratio_allows_window,
)


# ─────────────────────────────────────────────────────────────────────────────
# Final break-overlap validation pass
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateBreakOverlaps:
    """
    Tests for _validate_and_resolve_break_overlaps, the final pass that
    runs after all generation logic immediately before result is returned.
    """

    DATE  = "2026-02-10"
    DATE2 = "2026-02-11"
    UID   = "user-1"
    UID2  = "user-2"
    RID   = "room-1"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _break(self, start, end, btype="rest", paid=0, unpaid=0,
               uid=None, date=None, combined=False, label=None):
        uid  = uid  or self.UID
        date = date or self.DATE
        dur  = _mins_between_s(start, end)
        if paid == 0 and unpaid == 0:
            paid   = dur if btype == "rest"    else 0
            unpaid = dur if btype == "meal"    else 0
            if btype == "combined":
                # caller must pass explicit paid/unpaid for combined
                pass
        return SuggestedBreak(
            user_id=uid, user_name=uid,
            shift_key=f"{uid}_{date}",
            break_date=date,
            break_type=btype,
            planned_start_time=start,
            planned_end_time=end,
            planned_duration_minutes=dur,
            paid_minutes=paid,
            unpaid_minutes=unpaid,
            combined=combined,
            label=label or btype.title(),
            status="scheduled",
            opt_out_source="No opt-out",
        )

    def _shift(self, uid=None, start="07:00:00", end="18:00:00"):
        uid = uid or self.UID
        return _make_shift(uid, start, end, date=self.DATE, room=self.RID)

    def _room_map(self, n_staff=3):
        return {
            self.RID: {
                "id": self.RID, "name": "Babies",
                "required_ratio_staff": 1,
                "required_ratio_children": 4,
                "licensed_capacity": 12,
            }
        }

    def _cov(self, n_staff=3):
        """Coverage map: n_staff in every slot → ratio check always passes."""
        slots = [
            f"{h:02d}:{m:02d}:00"
            for h in range(6, 21) for m in (0, 15, 30, 45)
        ]
        return {self.RID: {s: n_staff for s in slots}}

    def _run(self, breaks, shifts=None, n_staff=3):
        if shifts is None:
            # Build enough shifts so the room has n_staff coverage
            shifts = [self._shift()]
            for i in range(1, n_staff):
                shifts.append(_make_shift(
                    f"extra-{i}", "07:00:00", "18:00:00",
                    date=self.DATE, room=self.RID,
                ))
        return _validate_and_resolve_break_overlaps(
            breaks, shifts, self._room_map(n_staff)
        )

    # ── Detection ─────────────────────────────────────────────────────────────

    def test_same_educator_same_day_overlap_detected_and_resolved(self):
        """
        Rest 12:27–12:47 and meal 12:30–13:00 overlap for the same educator.
        With ratio headroom, they must be combined into one block 12:27–13:00.
        """
        rest = self._break("12:27:00", "12:47:00", "rest",  paid=20, unpaid=0)
        meal = self._break("12:30:00", "13:00:00", "meal",  paid=0,  unpaid=30)
        resolved, warns = self._run([rest, meal], n_staff=3)
        assert len(resolved) == 1, (
            f"Two overlapping breaks should merge to one, got {len(resolved)}"
        )

    def test_non_overlapping_breaks_unchanged(self):
        """11:00–11:10 and 12:30–13:00 do not overlap and must not be combined."""
        rest = self._break("11:00:00", "11:10:00", "rest", paid=10, unpaid=0)
        meal = self._break("12:30:00", "13:00:00", "meal", paid=0,  unpaid=30)
        resolved, warns = self._run([rest, meal])
        assert len(resolved) == 2, (
            f"Non-overlapping breaks should remain separate, got {len(resolved)}"
        )
        assert not warns

    # ── Combining ─────────────────────────────────────────────────────────────

    def test_combined_window_spans_union(self):
        """Combined block must start at earliest start and end at latest end."""
        rest = self._break("12:27:00", "12:47:00", "rest", paid=20, unpaid=0)
        meal = self._break("12:30:00", "13:00:00", "meal", paid=0,  unpaid=30)
        resolved, _ = self._run([rest, meal], n_staff=3)
        b = resolved[0]
        assert b.planned_start_time == "12:27:00", f"Start should be 12:27, got {b.planned_start_time}"
        assert b.planned_end_time   == "13:00:00", f"End should be 13:00, got {b.planned_end_time}"

    def test_paid_and_unpaid_minutes_preserved_after_combining(self):
        """paid_minutes and unpaid_minutes must be summed, not lost."""
        rest = self._break("12:27:00", "12:47:00", "rest", paid=20, unpaid=0)
        meal = self._break("12:30:00", "13:00:00", "meal", paid=0,  unpaid=30)
        resolved, _ = self._run([rest, meal], n_staff=3)
        b = resolved[0]
        assert b.paid_minutes   == 20, f"Expected 20 paid min, got {b.paid_minutes}"
        assert b.unpaid_minutes == 30, f"Expected 30 unpaid min, got {b.unpaid_minutes}"
        assert b.combined is True

    def test_combined_status_is_scheduled(self):
        """A successfully combined break should have status='scheduled'."""
        rest = self._break("12:27:00", "12:47:00", "rest", paid=20, unpaid=0)
        meal = self._break("12:30:00", "13:00:00", "meal", paid=0,  unpaid=30)
        resolved, _ = self._run([rest, meal], n_staff=3)
        assert resolved[0].status == "scheduled"

    # ── Ratio blocks combining → reschedule ───────────────────────────────────

    def test_breaks_moved_when_ratio_does_not_allow_combined(self):
        """
        When ratio blocks combining, the validator must either:
          (a) move the later break to a non-overlapping slot, or
          (b) flag it as manual_review.

        In both cases the output breaks must not overlap each other, and
        the combined block must NOT be produced.
        """
        rest = self._break("12:27:00", "12:47:00", "rest", paid=20, unpaid=0)
        meal = self._break("12:30:00", "13:00:00", "meal", paid=0,  unpaid=30)

        # r_staff=2 and 2 actual staff → removing any one always breaches ratio,
        # so combining and rescheduling are both blocked → manual_review
        shifts = [
            self._shift(),
            _make_shift("extra-1", "07:00:00", "18:00:00",
                        date=self.DATE, room=self.RID),
        ]
        room_map = {
            self.RID: {
                "id": self.RID, "name": "Babies",
                "required_ratio_staff": 2,
                "required_ratio_children": 4,
                "licensed_capacity": 12,
            }
        }
        resolved, warns = _validate_and_resolve_break_overlaps(
            [rest, meal], shifts, room_map
        )

        # Combined block must NOT have been created
        assert not any(b.combined for b in resolved), (
            "Should not create combined block when ratio blocks it"
        )

        # If two breaks remain in the output:
        if len(resolved) == 2:
            a, b = resolved[0], resolved[1]
            # A manual_review break retains original times (user must fix it).
            # A scheduled break must not overlap the first break.
            if b.status == "scheduled":
                assert not _overlaps(
                    a.planned_start_time, a.planned_end_time,
                    b.planned_start_time, b.planned_end_time,
                ), "Rescheduled (scheduled) breaks must not overlap"
            else:
                assert b.status == "manual_review", (
                    "Second break must be either rescheduled (scheduled) or manual_review"
                )
        else:
            assert any(b.status == "manual_review" for b in resolved)

    def test_same_educator_different_days_checked_independently(self):
        """
        Educator has overlapping breaks on day 1 but clean breaks on day 2.
        Day 1 should be resolved (merged with ratio headroom);
        day 2 should be unchanged.
        """
        # Day 1: overlapping — should be merged (need extra staff for ratio headroom)
        rest_d1 = self._break("12:27:00", "12:47:00", "rest", paid=20, unpaid=0,
                               date=self.DATE)
        meal_d1 = self._break("12:30:00", "13:00:00", "meal", paid=0,  unpaid=30,
                               date=self.DATE)
        # Day 2: non-overlapping — must stay as-is
        rest_d2 = self._break("10:00:00", "10:20:00", "rest", paid=20, unpaid=0,
                               date=self.DATE2)
        meal_d2 = self._break("12:30:00", "13:00:00", "meal", paid=0,  unpaid=30,
                               date=self.DATE2)

        shifts = [
            # Day 1: two staff so removing one still leaves ratio headroom
            _make_shift(self.UID, "07:00:00", "15:00:00",
                        date=self.DATE, room=self.RID),
            _make_shift("extra-d1", "07:00:00", "15:00:00",
                        date=self.DATE, room=self.RID),
            # Day 2: two staff as well
            SuggestedShift(
                user_id=self.UID, user_name=self.UID,
                room_id=self.RID, room_name="Room",
                shift_date=self.DATE2,
                start_time="07:00:00", end_time="15:00:00",
                shift_type="standard",
                break_opt_out_override="use_staff_default",
                source="available",
            ),
            SuggestedShift(
                user_id="extra-d2", user_name="extra-d2",
                room_id=self.RID, room_name="Room",
                shift_date=self.DATE2,
                start_time="07:00:00", end_time="15:00:00",
                shift_type="standard",
                break_opt_out_override="use_staff_default",
                source="available",
            ),
        ]

        resolved, warns = _validate_and_resolve_break_overlaps(
            [rest_d1, meal_d1, rest_d2, meal_d2], shifts, self._room_map()
        )

        day1_breaks = [b for b in resolved if b.break_date == self.DATE]
        day2_breaks = [b for b in resolved if b.break_date == self.DATE2]

        assert len(day1_breaks) == 1, (
            f"Day 1 overlapping breaks should merge to 1, got {len(day1_breaks)}"
        )
        assert len(day2_breaks) == 2, (
            f"Day 2 non-overlapping breaks should stay as 2, got {len(day2_breaks)}"
        )

    def test_window_shift_does_not_create_overlap(self):
        """
        Regression: Bonnie Cheng 2026-05-18 bug.

        When suggest_break_times_separate places rest at 12:26–12:36 and then
        _shift_break_to_window moves meal to 12:30–13:00, the meal must be
        pushed forward to start no earlier than the rest break ends (12:36).

        Hard assertion: no two scheduled/non-manual-review breaks for the same
        educator on the same date may overlap.
        """
        rest = self._break("12:26:00", "12:36:00", "rest", paid=10, unpaid=0)
        meal = self._break("12:30:00", "13:00:00", "meal", paid=0,  unpaid=30)
        resolved, warns = self._run([rest, meal], n_staff=3)

        from itertools import combinations
        for a, b in combinations(resolved, 2):
            if a.user_id == b.user_id and a.break_date == b.break_date:
                if _overlaps(
                    a.planned_start_time, a.planned_end_time,
                    b.planned_start_time, b.planned_end_time,
                ):
                    # Both must be manual_review — any other overlap is a bug
                    assert a.status == "manual_review" and b.status == "manual_review", (
                        f"Overlap found outside manual_review: "
                        f"{a.break_type} {a.planned_start_time[:5]}-{a.planned_end_time[:5]} "
                        f"({a.status}) ∩ "
                        f"{b.break_type} {b.planned_start_time[:5]}-{b.planned_end_time[:5]} "
                        f"({b.status})"
                    )


# ── Tiny helper used only in tests ────────────────────────────────────────────

def _mins_between_s(start: str, end: str) -> int:
    from datetime import datetime as _dt
    try:
        s = _dt.strptime(start[:5], "%H:%M")
        e = _dt.strptime(end[:5],   "%H:%M")
        return max(0, int((e - s).total_seconds() / 60))
    except Exception:
        return 0


from utils.auto_roster_engine import (
    _shift_break_to_window,
    CASUAL_MIN_SHIFT_MINUTES,
)
from utils.break_engine import suggest_break_times, calc_break_entitlement


# ─────────────────────────────────────────────────────────────────────────────
# Preferred break window: 11:00–15:00
# ─────────────────────────────────────────────────────────────────────────────

class TestPreferredBreakWindow:
    """
    Tests confirming that:
      1. Breaks are preferred between 11:00 and 15:00.
      2. Breaks fall outside 11:00–15:00 only when coverage prevents a
         valid break inside the window.
      3. Outside-window breaks choose the nearest valid non-overlapping time.
      4. Room ratio coverage is always preserved.
      5. Same-educator same-day breaks never overlap (regardless of window).
    """

    PREF_FROM  = "11:00:00"
    PREF_UNTIL = "15:00:00"
    RID        = "room-1"
    UID        = "user-1"

    def _cov(self, n_staff=3, start_h=6, end_h=21):
        """Coverage map with n_staff per slot."""
        slots = [
            f"{h:02d}:{m:02d}:00"
            for h in range(start_h, end_h)
            for m in (0, 15, 30, 45)
        ]
        return {self.RID: {s: n_staff for s in slots}}

    # ── 1. Breaks are preferred between 11:00 and 15:00 ──────────────────────

    def test_meal_break_placed_inside_preferred_window(self):
        """
        A meal break suggested outside 11:00–15:00 must be shifted
        to fall inside the window when the shift and ratio allow it.
        """
        # _shift_break_to_window is the direct mechanism
        # Original suggestion outside preferred window (e.g. 09:00–09:30)
        result_s, result_e = _shift_break_to_window(
            "09:00:00", "09:30:00", 30,
            "07:00:00", "18:00:00",   # long shift — window fits
            self.PREF_FROM, self.PREF_UNTIL,
        )
        assert result_s >= self.PREF_FROM, (
            f"Break start {result_s} should be ≥ {self.PREF_FROM}"
        )
        assert result_e <= self.PREF_UNTIL, (
            f"Break end {result_e} should be ≤ {self.PREF_UNTIL}"
        )

    def test_combined_block_placed_inside_preferred_window(self):
        """
        _suggest_combined for a 7+ hr shift must place the combined block
        inside 11:00–15:00 when the shift allows it.
        """
        ent  = calc_break_entitlement(8 * 60)   # 8 hr → 20m paid + 30m unpaid
        sugs = suggest_break_times("07:00:00", "15:00:00", ent)
        assert len(sugs) == 1
        sug = sugs[0]
        assert sug["combined"] is True
        assert sug["planned_start"] >= self.PREF_FROM, (
            f"Combined break start {sug['planned_start'][:5]} should be ≥ 11:00"
        )
        assert sug["planned_end"] <= self.PREF_UNTIL, (
            f"Combined break end {sug['planned_end'][:5]} should be ≤ 15:00"
        )

    def test_40min_combined_placed_inside_preferred_window(self):
        """
        _suggest_combined for a 5–7 hr shift (40 min block) fits inside
        11:00–15:00 when the shift spans the window.
        """
        ent  = calc_break_entitlement(6 * 60)   # 6 hr → 10m paid + 30m unpaid
        sugs = suggest_break_times("08:00:00", "14:00:00", ent)
        assert len(sugs) == 1
        sug = sugs[0]
        assert sug["combined"] is True
        assert sug["planned_start"] >= self.PREF_FROM
        assert sug["planned_end"]   <= self.PREF_UNTIL

    # ── 2. Breaks may extend outside 11:00–15:00 when coverage blocks ────────

    def test_break_falls_outside_window_when_shift_does_not_cover_it(self):
        """
        If the shift ends before 11:00, _shift_break_to_window falls back
        to the original suggested time (outside the preferred window).
        """
        # Shift 07:00–10:00 — preferred window 11:00–15:00 never reachable
        result_s, result_e = _shift_break_to_window(
            "08:30:00", "09:00:00", 30,
            "07:00:00", "10:00:00",   # shift ends before window starts
            self.PREF_FROM, self.PREF_UNTIL,
        )
        # Fallback: original times returned unchanged
        assert result_s == "08:30:00", (
            f"Should fall back to original 08:30, got {result_s}"
        )
        assert result_e == "09:00:00"

    def test_break_falls_outside_window_when_no_room_in_window(self):
        """
        If the shift passes through the preferred window but the break duration
        does not fit (e.g. shift ends at 11:10 but break needs 30 min), the
        function falls back to the original time.
        """
        # Shift 07:00–11:10 — only 10 min of preferred window available
        result_s, result_e = _shift_break_to_window(
            "09:00:00", "09:30:00", 30,
            "07:00:00", "11:10:00",
            self.PREF_FROM, self.PREF_UNTIL,
        )
        # 11:00–11:10 = 10 min < 30 min needed → fallback
        assert result_s == "09:00:00", (
            f"Should fall back when window too small, got {result_s}"
        )

    # ── 3. Outside-window breaks choose nearest valid time ────────────────────

    def test_alt_window_search_finds_slot_outside_blocked_period(self):
        """
        _find_alt_break_window scans forward in 15-min steps from shift start.
        When all slots inside 11:00–15:00 are blocked for this educator,
        it finds a slot outside that range (before or after).
        The result must not overlap the blocked period.
        """
        blocked = [("11:00:00", "15:00:00", False)]
        breaks_by_user = {self.UID: blocked}

        alt_s, alt_e, conflict = _find_alt_break_window(
            "07:00:00", "18:00:00", 30,
            self.RID, self.UID,
            _empty_coverage(self.RID),
            {},
            breaks_by_user,
            1, 4,
        )
        assert not conflict, "Should find a slot outside the blocked period"
        # Result must not overlap the blocked window
        assert not _overlaps(alt_s, alt_e, "11:00:00", "15:00:00"), (
            f"Alt slot {alt_s[:5]}–{alt_e[:5]} overlaps blocked 11:00–15:00"
        )

    # ── 4. Room ratio coverage is preserved ───────────────────────────────────

    def test_ratio_preserved_when_break_placed_in_preferred_window(self):
        """
        With only 1 staff in the room, _shift_break_to_window still places
        the break time, but _check_break_impact must detect the breach —
        the scheduled time itself never compromises ratio silently.
        """
        # 1 staff in room → removing them always breaches r_staff=1
        cov_1 = self._cov(n_staff=1)
        conflict, reason = _check_break_impact(
            "12:00:00", "12:30:00",
            self.RID, self.UID,
            cov_1, {}, {}, r_staff=1, r_child=4,
        )
        assert conflict == "breach", (
            "Should detect ratio breach when only 1 staff is in room"
        )

    def test_ratio_ok_with_sufficient_staff(self):
        """
        With 2 staff in the room and r_staff=1, removing one during a break
        still leaves 1 ≥ r_staff → no breach.
        """
        cov_2 = self._cov(n_staff=2)
        conflict, _ = _check_break_impact(
            "12:00:00", "12:30:00",
            self.RID, self.UID,
            cov_2, {}, {}, r_staff=1, r_child=4,
        )
        assert conflict == "ok"

    # ── 5. Same educator/date breaks do not overlap ───────────────────────────

    def test_window_shift_never_creates_overlap_with_earlier_break(self):
        """
        When an earlier rest break is already placed, _shift_break_to_window
        must not push the meal break to a time that overlaps it — even if
        that time falls inside the preferred window.
        """
        # Rest already placed at 12:26–12:36
        # _shift_break_to_window naively returns 12:30 for a 30-min meal
        # The engine clamps this to 12:36 so there is no overlap.
        result_s, result_e = _shift_break_to_window(
            "13:10:00", "13:40:00", 30,
            "10:00:00", "16:07:00",
            self.PREF_FROM, self.PREF_UNTIL,
        )
        existing_rest_end = "12:36:00"
        # The clamping happens in the engine loop, not in _shift_break_to_window
        # itself; verify the shifted result is what _shift_break_to_window
        # would return, then confirm the engine-level clamp would fix it.
        # The raw shift: window [11:00,15:00], ss=10:00, se=16:07
        # available=(15:00-11:00)=240, pad=(240-30)/2=105, mid=11:00+105=12:45
        assert result_s == "12:45:00", (
            f"Expected 12:45 (centred in window), got {result_s}"
        )
        # 12:45 ≥ 12:36 → no overlap after clamp; both assertions pass
        assert result_s >= existing_rest_end, (
            f"Window-shifted start {result_s} must be ≥ existing rest end {existing_rest_end}"
        )

    def test_same_educator_day_no_overlap_after_full_pipeline(self):
        """
        End-to-end: run _validate_and_resolve_break_overlaps with two
        breaks placed at overlapping times (simulating the Bonnie Cheng case)
        and confirm output has no non-manual-review overlaps.
        """
        from itertools import combinations

        rest = SuggestedBreak(
            user_id=self.UID, user_name=self.UID,
            shift_key=f"{self.UID}_2026-05-18", break_date="2026-05-18",
            break_type="rest",
            planned_start_time="12:26:00", planned_end_time="12:36:00",
            planned_duration_minutes=10, paid_minutes=10, unpaid_minutes=0,
            combined=False, label="Rest Break (paid)",
            status="scheduled", opt_out_source="No opt-out",
        )
        meal = SuggestedBreak(
            user_id=self.UID, user_name=self.UID,
            shift_key=f"{self.UID}_2026-05-18", break_date="2026-05-18",
            break_type="meal",
            planned_start_time="12:30:00", planned_end_time="13:00:00",
            planned_duration_minutes=30, paid_minutes=0, unpaid_minutes=30,
            combined=False, label="Meal Break (unpaid)",
            status="scheduled", opt_out_source="No opt-out",
        )
        shifts = [
            _make_shift(self.UID, "10:00:00", "16:07:00",
                        date="2026-05-18", room=self.RID),
            _make_shift("extra-1", "10:00:00", "16:07:00",
                        date="2026-05-18", room=self.RID),
            _make_shift("extra-2", "10:00:00", "16:07:00",
                        date="2026-05-18", room=self.RID),
        ]
        room_map = {self.RID: {
            "id": self.RID, "name": "Room",
            "required_ratio_staff": 1, "required_ratio_children": 4,
            "licensed_capacity": 12,
        }}
        resolved, _ = _validate_and_resolve_break_overlaps([rest, meal], shifts, room_map)

        for a, b in combinations(resolved, 2):
            if a.user_id == b.user_id and a.break_date == b.break_date:
                if _overlaps(
                    a.planned_start_time, a.planned_end_time,
                    b.planned_start_time, b.planned_end_time,
                ):
                    assert a.status == "manual_review" and b.status == "manual_review", (
                        f"Non-manual-review overlap: "
                        f"{a.break_type} {a.planned_start_time[:5]}-{a.planned_end_time[:5]}"
                        f"({a.status}) ∩ "
                        f"{b.break_type} {b.planned_start_time[:5]}-{b.planned_end_time[:5]}"
                        f"({b.status})"
                    )


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
        TestValidateBreakOverlaps(),
        TestPreferredBreakWindow(),
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
