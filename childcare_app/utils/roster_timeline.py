# utils/roster_timeline.py
# Builds a colour-coded HTML roster timeline grid from engine output.
# Pure Python — no Streamlit, no database calls.
#
# GRID STRUCTURE
# ──────────────
#  Left fixed columns : Educator | Start | Finish
#  Centre columns     : one per 15-min slot, 07:15 → 18:30
#  Right columns      : room summary (name | children | ratio | req. staff)
#
# CELL CONTENT during each slot
#  • Shift active, no break, no movement : short room code (≤4 chars)
#  • Combined break                       : "B{dur}" e.g. "B40", "B50"
#  • Rest break only                      : "B20" or "B10"
#  • Meal break only                      : "B30"
#  • Temporary movement (cover)           : room code + "†" marker
#  • Outside shift                        : empty / grey

from __future__ import annotations
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

GRID_START = "07:15:00"
GRID_END   = "18:30:00"
SLOT_MINS  = 15

# Colour palette — room colours are assigned on first use from this list.
# Must contrast with white text.  Design: muted-but-distinct, not candy.
ROOM_PALETTE = [
    "#2d6a8f",   # steel blue
    "#3a7d44",   # forest green
    "#7b4f9e",   # plum
    "#b5541c",   # terracotta
    "#4a7c74",   # teal
    "#8f6d2d",   # ochre
    "#2d4f8f",   # navy
    "#6b3a3a",   # burgundy
]

BREAK_BG     = "#f0f0f0"   # light grey for break cells
BREAK_FG     = "#333333"
MOVE_BG      = "#fff3cd"   # amber tint for temporary-movement cells
MOVE_FG      = "#7a5c00"
EMPTY_BG     = "#f8f9fa"   # outside-shift cells
HEADER_BG    = "#0d1f35"   # app navy
HEADER_FG    = "#ffffff"
ROW_ALT_BG   = "#f6f8fa"   # alternating row stripe


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def build_timeline_html(
    date_str: str,
    shifts: list,        # list[SuggestedShift] for this day
    breaks: list,        # list[SuggestedBreak] for this day
    movements: list,     # list[SuggestedMovement] for this day
    rooms: list[dict],   # room dicts with id, name, licensed_capacity,
                         # required_ratio_staff, required_ratio_children
    intervals: list[dict] | None = None,  # actual_children per room per slot
) -> str:
    """
    Build a complete self-contained HTML roster timeline for one day.

    Returns an HTML string suitable for st.markdown(html, unsafe_allow_html=True).
    The table uses inline styles only — no external CSS — so it renders cleanly
    inside Streamlit's markdown renderer.
    """
    slots      = _build_slots()
    room_map   = {r["id"]: r for r in rooms}
    room_color = _assign_room_colors(rooms)

    # Build lookup structures
    # break_map[uid][slot_str] = break_label
    # break_status[uid][slot_str] = "scheduled" | "manual_review"
    break_map, break_status_map = _build_break_map(breaks, slots)

    # movement_map[slot_str] = {to_room_id: [SuggestedMovement, ...]}
    movement_map = _build_movement_map(movements, slots)

    # children_map[room_id][slot_str] = count
    children_map = _build_children_map(intervals or [], slots)

    # Sort educators: by room name then educator name
    sorted_shifts = sorted(shifts, key=lambda s: (s.room_name, s.user_name))

    # ── HTML header ───────────────────────────────────────────────────
    html_parts = [
        "<div style='overflow-x:auto;font-family:DM Sans,system-ui,sans-serif;"
        "font-size:11px;line-height:1.3;'>",
        f"<table style='border-collapse:collapse;min-width:100%;white-space:nowrap;'>",
    ]

    # ── Header row 1: date + hour labels ─────────────────────────────
    # Group slots by hour for a clean hour header
    html_parts.append("<thead>")
    html_parts.append(_build_hour_header(slots))
    html_parts.append(_build_slot_header(slots))
    html_parts.append("</thead>")

    # ── Body rows ─────────────────────────────────────────────────────
    html_parts.append("<tbody>")
    for i, shift in enumerate(sorted_shifts):
        row_bg = "#ffffff" if i % 2 == 0 else ROW_ALT_BG
        html_parts.append(
            _build_educator_row(
                shift, slots, room_map, room_color,
                break_map, break_status_map, movement_map, row_bg,
            )
        )
    html_parts.append("</tbody>")

    # ── Room summary footer rows ──────────────────────────────────────
    html_parts.append("<tfoot>")
    html_parts.append(
        _build_room_summary_rows(
            shifts, slots, rooms, room_color, children_map,
        )
    )
    html_parts.append("</tfoot>")

    html_parts.append("</table>")

    # ── Colour legend ─────────────────────────────────────────────────
    html_parts.append(_build_legend(rooms, room_color))

    html_parts.append("</div>")
    return "\n".join(html_parts)


def build_movement_notes_html(movements: list) -> str:
    """
    Build a simple HTML movement notes section for display below the grid.
    """
    if not movements:
        return ""

    rows = sorted(movements, key=lambda m: (m.move_date, m.start_time))
    parts = [
        "<div style='margin-top:16px;font-family:DM Sans,system-ui,sans-serif;"
        "font-size:12px;'>",
        "<strong style='color:#0d1f35;'>🔄 Temporary Educator Movements</strong>",
        "<table style='border-collapse:collapse;margin-top:8px;width:100%;'>",
        "<tr style='background:#0d1f35;color:#fff;'>",
        *[f"<th style='padding:4px 8px;text-align:left;'>{h}</th>"
          for h in ["Educator", "From", "→ To", "Start", "End", "Covering for"]],
        "</tr>",
    ]
    for i, mv in enumerate(rows):
        bg = "#ffffff" if i % 2 == 0 else ROW_ALT_BG
        parts += [
            f"<tr style='background:{bg};'>",
            f"<td style='padding:3px 8px;'>{mv.educator_name}</td>",
            f"<td style='padding:3px 8px;color:#666;'>{mv.from_room_name}</td>",
            f"<td style='padding:3px 8px;font-weight:600;color:{MOVE_FG};'>{mv.to_room_name}</td>",
            f"<td style='padding:3px 8px;'>{mv.start_time[:5]}</td>",
            f"<td style='padding:3px 8px;'>{mv.end_time[:5]}</td>",
            f"<td style='padding:3px 8px;'>{mv.covering_for_name}</td>",
            "</tr>",
        ]
    parts += ["</table>", "</div>"]
    return "\n".join(parts)


def get_day_summary(
    date_str: str,
    shifts: list,
    breaks: list,
    movements: list,
) -> dict:
    """Return summary counts for a single day."""
    return {
        "date":       date_str,
        "educators":  len(shifts),
        "breaks":     len(breaks),
        "movements":  len(movements),
        "manual_review": sum(1 for b in breaks if b.status == "manual_review"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE: slot helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_slots() -> list[str]:
    """Return list of HH:MM:SS slot strings from GRID_START to GRID_END."""
    slots = []
    t     = datetime.strptime(GRID_START, "%H:%M:%S")
    end   = datetime.strptime(GRID_END,   "%H:%M:%S")
    while t < end:
        slots.append(t.strftime("%H:%M:%S"))
        t += timedelta(minutes=SLOT_MINS)
    return slots


def _slot_label(slot: str) -> str:
    """'07:15:00' → '7:15'"""
    h, m = int(slot[:2]), int(slot[3:5])
    return f"{h}:{m:02d}"


def _slot_hour(slot: str) -> int:
    return int(slot[:2])


def _overlaps_slot(slot: str, start: str, end: str) -> bool:
    """Return True when slot_start ≤ slot < end (half-open interval)."""
    slot_end = (datetime.strptime(slot, "%H:%M:%S")
                + timedelta(minutes=SLOT_MINS)).strftime("%H:%M:%S")
    # slot is active if slot_start < end AND start < slot_end
    return start < slot_end and slot >= start and slot < end


def _is_in_shift(slot: str, shift_start: str, shift_end: str) -> bool:
    return shift_start <= slot < shift_end


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE: lookup builders
# ─────────────────────────────────────────────────────────────────────────────

def _assign_room_colors(rooms: list[dict]) -> dict[str, str]:
    color_map = {}
    for i, room in enumerate(rooms):
        color_map[room["id"]] = ROOM_PALETTE[i % len(ROOM_PALETTE)]
    return color_map


def _room_code(room: dict) -> str:
    """Short code: first 4 chars of room name, upper-cased."""
    return room.get("name", "?")[:4].upper()


def _break_label(brk) -> str:
    """
    Label to show in break cells.
    Combined: "B{total_dur}"  e.g. B40, B50
    Rest only: "B{paid_min}"  e.g. B20, B10
    Meal only: "B30"
    """
    dur = getattr(brk, "planned_duration_minutes", 0)
    return f"B{dur}"


def _build_break_map(
    breaks: list,
    slots: list[str],
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """
    Returns:
        break_label_map[uid][slot] = label string (e.g. "B40")
        break_status_map[uid][slot] = "scheduled" | "manual_review"
    """
    label_map:  dict[str, dict[str, str]] = {}
    status_map: dict[str, dict[str, str]] = {}

    for brk in breaks:
        uid   = brk.user_id
        bs    = brk.planned_start_time
        be    = brk.planned_end_time
        label = _break_label(brk)
        stat  = brk.status

        if uid not in label_map:
            label_map[uid]  = {}
            status_map[uid] = {}

        for slot in slots:
            if _is_in_shift(slot, bs, be):
                label_map[uid][slot]  = label
                status_map[uid][slot] = stat

    return label_map, status_map


def _build_movement_map(
    movements: list,
    slots: list[str],
) -> dict[str, dict[str, list]]:
    """
    movement_map[slot][to_room_id] = [SuggestedMovement, ...]
    Used to show covering educator in destination room.
    """
    mv_map: dict[str, dict[str, list]] = {}
    for mv in movements:
        for slot in slots:
            if _is_in_shift(slot, mv.start_time, mv.end_time):
                if slot not in mv_map:
                    mv_map[slot] = {}
                rid = mv.to_room_id
                mv_map[slot].setdefault(rid, []).append(mv)
    return mv_map


def _build_children_map(
    intervals: list[dict],
    slots: list[str],
) -> dict[str, dict[str, int]]:
    """
    children_map[room_id][slot] = actual_children count.
    Used for room summary footer.
    """
    result: dict[str, dict[str, int]] = {}
    for iv in intervals:
        rid   = iv.get("room_id", "")
        istart = iv.get("interval_start", "")
        count  = iv.get("actual_children") or iv.get("expected_children") or 0
        if rid and istart:
            result.setdefault(rid, {})[istart] = int(count)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE: HTML row builders
# ─────────────────────────────────────────────────────────────────────────────

_TH = "style='padding:3px 6px;border:1px solid #dde;text-align:center;"
_TD = "style='padding:3px 5px;border:1px solid #e2e8f0;"

def _th(content: str, extra: str = "", colspan: int = 1) -> str:
    col = f"colspan='{colspan}' " if colspan > 1 else ""
    return f"<th {col}{_TH}{extra}'>{content}</th>"

def _td(content: str, bg: str, fg: str = "#ffffff", extra: str = "") -> str:
    return (
        f"<td {_TD}background:{bg};color:{fg};text-align:center;{extra}'>"
        f"{content}</td>"
    )


def _build_hour_header(slots: list[str]) -> str:
    """Hour-level merged header row."""
    # Group consecutive slots by hour
    parts = [
        "<tr style='background:#0d1f35;color:#fff;font-weight:600;'>",
        _th("Educator", "text-align:left;min-width:110px;"),
        _th("Start",    "min-width:44px;"),
        _th("Finish",   "min-width:44px;"),
    ]

    # Emit hour headers spanning all slots in that hour
    current_hour = None
    span_count   = 0
    pending_parts = []

    for slot in slots:
        h = _slot_hour(slot)
        if h != current_hour:
            if current_hour is not None:
                pending_parts.append(
                    _th(f"{current_hour:02d}:00", "", colspan=span_count)
                )
            current_hour = h
            span_count   = 1
        else:
            span_count += 1
    if current_hour is not None:
        pending_parts.append(
            _th(f"{current_hour:02d}:00", "", colspan=span_count)
        )

    parts.extend(pending_parts)
    # Room summary header spanning the right-side columns (empty for now)
    parts.append(_th("Room summary", "min-width:120px;", colspan=4))
    parts.append("</tr>")
    return "".join(parts)


def _build_slot_header(slots: list[str]) -> str:
    """15-min slot sub-header."""
    parts = [
        "<tr style='background:#1a3350;color:#cce;font-size:9px;'>",
        _th("", "min-width:110px;"),
        _th("", "min-width:44px;"),
        _th("", "min-width:44px;"),
    ]
    for slot in slots:
        m = int(slot[3:5])
        label = f":{m:02d}" if m != 0 else slot[:5]
        parts.append(_th(label, "min-width:28px;max-width:36px;font-size:9px;padding:2px 2px;"))
    parts += [
        _th("Room", "font-size:9px;"),
        _th("👶", "font-size:9px;"),
        _th("Ratio", "font-size:9px;"),
        _th("Req", "font-size:9px;"),
    ]
    parts.append("</tr>")
    return "".join(parts)


def _build_educator_row(
    shift,
    slots: list[str],
    room_map: dict,
    room_color: dict[str, str],
    break_map: dict[str, dict[str, str]],
    break_status_map: dict[str, dict[str, str]],
    movement_map: dict[str, dict[str, list]],
    row_bg: str,
) -> str:
    uid       = shift.user_id
    rid       = shift.room_id
    room      = room_map.get(rid, {})
    room_clr  = room_color.get(rid, "#666")
    code      = _room_code(room)
    user_brks = break_map.get(uid, {})
    user_stat = break_status_map.get(uid, {})

    parts = [
        f"<tr style='background:{row_bg};'>",
        # Educator name cell
        f"<td style='padding:3px 8px;border:1px solid #e2e8f0;font-weight:600;"
        f"color:#0d1f35;white-space:nowrap;min-width:110px;'>{shift.user_name}</td>",
        # Start / Finish
        f"<td style='padding:3px 6px;border:1px solid #e2e8f0;text-align:center;"
        f"color:#374151;'>{shift.start_time[:5]}</td>",
        f"<td style='padding:3px 6px;border:1px solid #e2e8f0;text-align:center;"
        f"color:#374151;'>{shift.end_time[:5]}</td>",
    ]

    for slot in slots:
        in_shift = _is_in_shift(slot, shift.start_time, shift.end_time)

        if not in_shift:
            # Grey out-of-shift cell
            parts.append(
                f"<td style='background:{EMPTY_BG};border:1px solid #e2e8f0;"
                f"min-width:28px;max-width:36px;'></td>"
            )
            continue

        # Break?
        brk_label = user_brks.get(slot)
        if brk_label:
            stat = user_stat.get(slot, "scheduled")
            if stat == "manual_review":
                bg, fg = "#fef3c7", "#92400e"
            else:
                bg, fg = BREAK_BG, BREAK_FG
            parts.append(
                f"<td style='background:{bg};color:{fg};border:1px solid #e2e8f0;"
                f"text-align:center;min-width:28px;max-width:36px;"
                f"font-size:9px;font-weight:700;'>{brk_label}</td>"
            )
            continue

        # Temporary movement to another room?
        slot_movs = movement_map.get(slot, {})
        covering_mv = None
        for to_rid, mvs in slot_movs.items():
            for mv in mvs:
                if mv.educator_id == uid:
                    covering_mv = mv
                    break

        if covering_mv is not None:
            temp_room    = room_map.get(covering_mv.to_room_id, {})
            temp_code    = _room_code(temp_room)
            temp_clr     = room_color.get(covering_mv.to_room_id, "#aaa")
            # Amber border + temp room code
            temp_name = temp_room.get("name", "")
            parts.append(
                f"<td style='background:{MOVE_BG};color:{MOVE_FG};"
                f"border:2px solid {temp_clr};text-align:center;"
                f"min-width:28px;max-width:36px;font-size:9px;font-weight:700;"
                f"' title='Temporary cover in {temp_name}'>"
                f"{temp_code}†</td>"
            )
            continue

        # Normal shift cell — show room code with room colour
        parts.append(
            f"<td style='background:{room_clr};color:#fff;"
            f"border:1px solid rgba(255,255,255,0.25);text-align:center;"
            f"min-width:28px;max-width:36px;font-size:9px;font-weight:600;'>"
            f"{code}</td>"
        )

    # Room summary columns (right side — blank per educator row)
    for _ in range(4):
        parts.append(f"<td style='border:1px solid #e2e8f0;background:{row_bg};'></td>")

    parts.append("</tr>")
    return "".join(parts)


def _build_room_summary_rows(
    shifts: list,
    slots: list[str],
    rooms: list[dict],
    room_color: dict[str, str],
    children_map: dict[str, dict[str, int]],
) -> str:
    """
    Build summary rows below the grid — one per room.
    Each row shows peak children, ratio, required staff.
    Also shows a staff-count bar across the timeline.
    """
    # Staff per room per slot
    staff_counts: dict[str, dict[str, int]] = {}
    for shift in shifts:
        rid = shift.room_id
        for slot in slots:
            if _is_in_shift(slot, shift.start_time, shift.end_time):
                staff_counts.setdefault(rid, {})
                staff_counts[rid][slot] = staff_counts[rid].get(slot, 0) + 1

    html = []

    # Separator row
    n_cols = 3 + len(slots) + 4
    html.append(
        f"<tr><td colspan='{n_cols}' style='background:#e2e8f0;"
        f"height:4px;padding:0;border:none;'></td></tr>"
    )

    for room in rooms:
        rid   = room["id"]
        rname = room["name"]
        clr   = room_color.get(rid, "#888")
        r_s   = room.get("required_ratio_staff",    1)
        r_c   = room.get("required_ratio_children", 4)
        cap   = room.get("licensed_capacity", 0)

        # Peak children from attendance intervals
        room_ivs = children_map.get(rid, {})
        peak_children = max(room_ivs.values(), default=0)
        import math
        req_staff = math.ceil(peak_children / r_c) * r_s if peak_children > 0 else r_s

        html.append("<tr style='background:#f0f4f8;'>")
        # Educator col: room name
        html.append(
            f"<td style='padding:3px 8px;border:1px solid #e2e8f0;font-weight:600;"
            f"color:{clr};min-width:110px;white-space:nowrap;'>{rname}</td>"
        )
        # Start/Finish: show ratio formula
        html.append(
            f"<td colspan='2' style='padding:3px 6px;border:1px solid #e2e8f0;"
            f"text-align:center;font-size:10px;color:#555;'>"
            f"1:{r_c}</td>"
        )

        # Timeline slots: show staff count
        for slot in slots:
            count      = staff_counts.get(rid, {}).get(slot, 0)
            child_cnt  = room_ivs.get(slot, 0)
            # Colour by ratio status
            if count == 0:
                bg, fg = "#f8f9fa", "#ccc"
            else:
                import math as _math
                needed = _math.ceil(child_cnt / r_c) * r_s if child_cnt > 0 else r_s
                if count >= needed:
                    bg, fg = "#dcfce7", "#14532d"   # green = ok
                else:
                    bg, fg = "#fee2e2", "#991b1b"   # red = under ratio
            label = str(count) if count else ""
            html.append(
                f"<td style='background:{bg};color:{fg};border:1px solid #e2e8f0;"
                f"text-align:center;font-size:9px;font-weight:700;"
                f"min-width:28px;max-width:36px;'>{label}</td>"
            )

        # Right summary columns
        html.append(
            f"<td style='padding:3px 6px;border:1px solid #e2e8f0;font-weight:600;"
            f"color:{clr};white-space:nowrap;font-size:10px;'>{rname}</td>"
        )
        html.append(
            f"<td style='padding:3px 6px;border:1px solid #e2e8f0;text-align:center;"
            f"font-size:10px;color:#374151;'>{peak_children}</td>"
        )
        html.append(
            f"<td style='padding:3px 6px;border:1px solid #e2e8f0;text-align:center;"
            f"font-size:10px;color:#374151;'>1:{r_c}</td>"
        )
        html.append(
            f"<td style='padding:3px 6px;border:1px solid #e2e8f0;text-align:center;"
            f"font-weight:700;font-size:10px;"
            f"color:#1e3a55;'>{req_staff}</td>"
        )
        html.append("</tr>")

    return "".join(html)


def _build_legend(rooms: list[dict], room_color: dict[str, str]) -> str:
    parts = [
        "<div style='margin-top:10px;display:flex;flex-wrap:wrap;gap:8px;"
        "font-family:DM Sans,system-ui,sans-serif;font-size:11px;align-items:center;'>",
        "<strong style='color:#0d1f35;margin-right:4px;'>Legend:</strong>",
    ]
    for room in rooms:
        clr  = room_color.get(room["id"], "#888")
        code = _room_code(room)
        name = room["name"]
        parts.append(
            f"<span style='background:{clr};color:#fff;padding:2px 8px;"
            f"border-radius:4px;font-weight:600;' title='{name}'>{code} {name}</span>"
        )
    # Break + movement swatches
    parts.append(
        f"<span style='background:{BREAK_BG};color:{BREAK_FG};padding:2px 8px;"
        f"border-radius:4px;border:1px solid #ccc;'>B## Break</span>"
    )
    parts.append(
        f"<span style='background:{MOVE_BG};color:{MOVE_FG};padding:2px 8px;"
        f"border-radius:4px;border:1px solid #f0c060;'>CODE† Temp. cover</span>"
    )
    parts.append(
        f"<span style='background:#fef3c7;color:#92400e;padding:2px 8px;"
        f"border-radius:4px;border:1px solid #fcd34d;'>B## Manual review</span>"
    )
    parts.append("</div>")
    return "".join(parts)
