# utils/roster_timeline.py
# Colour-coded HTML roster timeline grid — pure Python, no Streamlit, no DB.
#
# GRID STRUCTURE
#  Left fixed columns : Educator | Start | Finish
#  Centre columns     : one 15-min slot, 07:15 → 18:30
#  Right columns      : room summary (name | children | ratio | req.staff)
#
# CELL CONTENT
#  Shift, no break, no cover : room code, room colour
#  Break slot                : B40 / B50 / B30 etc, grey (amber if manual_review)
#  Temporary movement        : destination room code + "†", amber background
#  Outside shift             : empty grey

from __future__ import annotations
from datetime import datetime, timedelta
from collections import defaultdict

GRID_START = "07:15:00"
GRID_END   = "18:30:00"
SLOT_MINS  = 15

ROOM_PALETTE = [
    "#2d6a8f", "#3a7d44", "#7b4f9e", "#b5541c",
    "#4a7c74", "#8f6d2d", "#2d4f8f", "#6b3a3a",
]

BREAK_BG  = "#f0f0f0"
BREAK_FG  = "#333333"
MOVE_BG   = "#fff3cd"
MOVE_FG   = "#7a5c00"
EMPTY_BG  = "#f8f9fa"
ROW_ALT   = "#f6f8fa"


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC
# ─────────────────────────────────────────────────────────────────────────────

def build_timeline_html(
    date_str: str,
    shifts: list,
    breaks: list,
    movements: list,
    rooms: list[dict],
    intervals: list[dict] | None = None,
) -> str:
    """
    Build complete self-contained HTML roster timeline for one day.
    Multiple shift segments for the same educator are merged into one row.
    Room transitions across the day are shown per-slot using room colour.
    """
    slots      = _build_slots()
    room_map   = {r["id"]: r for r in rooms}
    room_color = _assign_room_colors(rooms)

    # Merge segments → one display row per educator
    merged_rows   = _merge_educator_shifts(shifts)

    # Build lookup maps
    break_map, break_status_map = _build_break_map(breaks, slots)
    movement_map  = _build_movement_map(movements, slots)
    children_map  = _build_children_map(intervals or [], slots)

    # slot_room_map[uid][slot] = room_id — drives cell colour
    slot_room_map = _build_slot_room_map(shifts, slots)

    # Sort: by room name of primary segment, then educator name
    sorted_rows = sorted(
        merged_rows,
        key=lambda r: (r["primary_room_name"], r["user_name"]),
    )

    parts = [
        "<div style='overflow-x:auto;font-family:DM Sans,system-ui,sans-serif;"
        "font-size:11px;line-height:1.3;'>",
        "<table style='border-collapse:collapse;min-width:100%;white-space:nowrap;'>",
        "<thead>",
        _build_hour_header(slots),
        _build_slot_header(slots),
        "</thead>",
        "<tbody>",
    ]

    for i, row in enumerate(sorted_rows):
        bg = "#ffffff" if i % 2 == 0 else ROW_ALT
        parts.append(
            _build_merged_row(
                row, slots, room_map, room_color,
                slot_room_map.get(row["user_id"], {}),
                break_map, break_status_map, movement_map, bg,
            )
        )

    parts += ["</tbody>", "<tfoot>"]
    parts.append(
        _build_room_summary_rows(shifts, slots, rooms, room_color, children_map)
    )
    parts += ["</tfoot>", "</table>"]
    parts.append(_build_legend(rooms, room_color))
    parts.append("</div>")
    return "\n".join(parts)


def build_movement_notes_html(movements: list) -> str:
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
        bg = "#ffffff" if i % 2 == 0 else ROW_ALT
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


def get_day_summary(date_str, shifts, breaks, movements) -> dict:
    return {
        "date":          date_str,
        "educators":     len(_merge_educator_shifts(shifts)),
        "breaks":        len(breaks),
        "movements":     len(movements),
        "manual_review": sum(1 for b in breaks if b.status == "manual_review"),
    }


def build_weekly_summary_html(
    all_shifts: list,
    all_breaks: list,
    days: list,
    staff_profiles: dict,   # {uid: {name, employment_type}}
) -> str:
    """
    Weekly staff summary table.
    Counts merged shifts (one per educator per day), total hours, 10h days.
    """
    from collections import defaultdict
    import math

    by_uid: dict[str, list] = defaultdict(list)
    for s in all_shifts:
        by_uid[s.user_id].append(s)

    rows_html = []
    for uid, shifts in sorted(by_uid.items(), key=lambda x: x[1][0].user_name):
        merged_by_day: dict[str, dict] = {}
        for s in shifts:
            d = s.shift_date
            if d not in merged_by_day:
                merged_by_day[d] = {"start": s.start_time, "end": s.end_time}
            else:
                merged_by_day[d]["start"] = min(merged_by_day[d]["start"], s.start_time)
                merged_by_day[d]["end"]   = max(merged_by_day[d]["end"],   s.end_time)

        name       = shifts[0].user_name
        etype      = staff_profiles.get(uid, {}).get("employment_type", "—")
        n_days     = len(merged_by_day)
        total_hrs  = 0.0
        n_10h      = 0
        warnings   = []

        for d, seg in merged_by_day.items():
            dur = _mins_str(seg["start"], seg["end"]) / 60
            total_hrs += dur
            if dur >= 10.0:
                n_10h += 1

        if etype == "full_time" and n_days < 4:
            warnings.append("Full-time target not met due to availability.")

        warn_html = (
            f"<span style='color:#b45309;font-size:9px;'>⚠ {'; '.join(warnings)}</span>"
            if warnings else ""
        )
        rows_html.append(
            f"<tr>"
            f"<td style='padding:3px 8px;font-weight:600;'>{name}</td>"
            f"<td style='padding:3px 8px;'>{etype.replace('_',' ').title()}</td>"
            f"<td style='padding:3px 8px;text-align:center;'>{n_days}</td>"
            f"<td style='padding:3px 8px;text-align:center;'>{total_hrs:.1f}h</td>"
            f"<td style='padding:3px 8px;text-align:center;'>{n_10h}</td>"
            f"<td style='padding:3px 8px;'>{warn_html}</td>"
            f"</tr>"
        )

    if not rows_html:
        return ""

    return (
        "<div style='font-family:DM Sans,system-ui,sans-serif;font-size:11px;"
        "margin-top:12px;'>"
        "<strong style='color:#0d1f35;'>📋 Weekly Staff Summary</strong>"
        "<table style='border-collapse:collapse;margin-top:8px;width:100%;'>"
        "<tr style='background:#0d1f35;color:#fff;'>"
        + "".join(
            f"<th style='padding:4px 8px;text-align:left;'>{h}</th>"
            for h in ["Educator","Type","Days","Hours","10h days","Warnings"]
        )
        + "</tr>"
        + "".join(rows_html)
        + "</table></div>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MERGE LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def _merge_educator_shifts(shifts: list) -> list[dict]:
    """
    Collapse all same-educator same-day SuggestedShift segments into
    one display row per educator per day.

    Returns list of dicts:
        user_id, user_name, shift_date,
        start_time (earliest),  end_time (latest),
        primary_room_id, primary_room_name,
        segments (original SuggestedShift list, sorted by start)
    """
    groups: dict[tuple, list] = defaultdict(list)
    for s in shifts:
        groups[(s.user_id, s.shift_date)].append(s)

    rows = []
    for (uid, date_str), segs in groups.items():
        segs_sorted = sorted(segs, key=lambda s: s.start_time)
        start_time  = segs_sorted[0].start_time
        end_time    = max(s.end_time for s in segs_sorted)

        # Primary room = whichever segment covers the most time
        room_minutes: dict[str, int] = defaultdict(int)
        for s in segs_sorted:
            room_minutes[s.room_id] += _mins_str(s.start_time, s.end_time)
        primary_rid = max(room_minutes, key=lambda r: room_minutes[r])
        primary_name = segs_sorted[0].room_name  # fallback
        for s in segs_sorted:
            if s.room_id == primary_rid:
                primary_name = s.room_name
                break

        rows.append({
            "user_id":           uid,
            "user_name":         segs_sorted[0].user_name,
            "shift_date":        date_str,
            "start_time":        start_time,
            "end_time":          end_time,
            "primary_room_id":   primary_rid,
            "primary_room_name": primary_name,
            "break_opt_out_override": segs_sorted[0].break_opt_out_override,
            "segments":          segs_sorted,
        })

    return rows


def _build_slot_room_map(
    shifts: list,
    slots: list[str],
) -> dict[str, dict[str, str]]:
    """
    slot_room_map[uid][slot_str] = room_id
    When an educator has multiple segments, each slot is assigned to the
    room whose segment covers it.  Latest-placed segment wins ties.
    """
    result: dict[str, dict[str, str]] = {}
    for s in shifts:
        uid = s.user_id
        if uid not in result:
            result[uid] = {}
        for slot in slots:
            if _is_in_shift(slot, s.start_time, s.end_time):
                result[uid][slot] = s.room_id
    return result


# ─────────────────────────────────────────────────────────────────────────────
# HTML ROW BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

_TH = "style='padding:3px 6px;border:1px solid #dde;text-align:center;"
_TD = "style='padding:3px 5px;border:1px solid #e2e8f0;"


def _th(content: str, extra: str = "", colspan: int = 1) -> str:
    col = f"colspan='{colspan}' " if colspan > 1 else ""
    return f"<th {col}{_TH}{extra}'>{content}</th>"


def _build_hour_header(slots: list[str]) -> str:
    parts = [
        "<tr style='background:#0d1f35;color:#fff;font-weight:600;'>",
        _th("Educator", "text-align:left;min-width:120px;"),
        _th("Start",    "min-width:44px;"),
        _th("Finish",   "min-width:44px;"),
    ]
    current_hour, span_count, pending = None, 0, []
    for slot in slots:
        h = int(slot[:2])
        if h != current_hour:
            if current_hour is not None:
                pending.append(_th(f"{current_hour:02d}:00", "", colspan=span_count))
            current_hour, span_count = h, 1
        else:
            span_count += 1
    if current_hour is not None:
        pending.append(_th(f"{current_hour:02d}:00", "", colspan=span_count))
    parts.extend(pending)
    parts.append(_th("Room summary", "min-width:120px;", colspan=4))
    parts.append("</tr>")
    return "".join(parts)


def _build_slot_header(slots: list[str]) -> str:
    parts = [
        "<tr style='background:#1a3350;color:#cce;font-size:9px;'>",
        _th("", "min-width:120px;"),
        _th("", "min-width:44px;"),
        _th("", "min-width:44px;"),
    ]
    for slot in slots:
        m = int(slot[3:5])
        label = f":{m:02d}" if m != 0 else slot[:5]
        parts.append(_th(label, "min-width:28px;max-width:36px;font-size:9px;padding:2px 2px;"))
    parts += [
        _th("Room",  "font-size:9px;"),
        _th("👶",   "font-size:9px;"),
        _th("Ratio", "font-size:9px;"),
        _th("Req",   "font-size:9px;"),
    ]
    parts.append("</tr>")
    return "".join(parts)


def _build_merged_row(
    row: dict,
    slots: list[str],
    room_map: dict,
    room_color: dict[str, str],
    slot_rooms: dict[str, str],   # {slot: room_id} for this educator
    break_map: dict[str, dict[str, str]],
    break_status_map: dict[str, dict[str, str]],
    movement_map: dict[str, dict[str, list]],
    row_bg: str,
) -> str:
    uid        = row["user_id"]
    start_time = row["start_time"]
    end_time   = row["end_time"]
    user_brks  = break_map.get(uid, {})
    user_stat  = break_status_map.get(uid, {})

    parts = [
        f"<tr style='background:{row_bg};'>",
        f"<td style='padding:3px 8px;border:1px solid #e2e8f0;font-weight:600;"
        f"color:#0d1f35;white-space:nowrap;min-width:120px;'>{row['user_name']}</td>",
        f"<td style='padding:3px 6px;border:1px solid #e2e8f0;text-align:center;"
        f"color:#374151;'>{start_time[:5]}</td>",
        f"<td style='padding:3px 6px;border:1px solid #e2e8f0;text-align:center;"
        f"color:#374151;'>{end_time[:5]}</td>",
    ]

    for slot in slots:
        in_shift = _is_in_shift(slot, start_time, end_time)

        if not in_shift:
            parts.append(
                f"<td style='background:{EMPTY_BG};border:1px solid #e2e8f0;"
                f"min-width:28px;max-width:36px;'></td>"
            )
            continue

        # Break?
        brk_label = user_brks.get(slot)
        if brk_label:
            stat = user_stat.get(slot, "scheduled")
            bg, fg = ("#fef3c7", "#92400e") if stat == "manual_review" else (BREAK_BG, BREAK_FG)
            parts.append(
                f"<td style='background:{bg};color:{fg};border:1px solid #e2e8f0;"
                f"text-align:center;min-width:28px;max-width:36px;"
                f"font-size:9px;font-weight:700;'>{brk_label}</td>"
            )
            continue

        # Temporary movement (this educator is covering another room)?
        slot_movs = movement_map.get(slot, {})
        covering_mv = None
        for to_rid, mvs in slot_movs.items():
            for mv in mvs:
                if mv.educator_id == uid:
                    covering_mv = mv
                    break

        if covering_mv is not None:
            temp_room  = room_map.get(covering_mv.to_room_id, {})
            temp_code  = _room_code(temp_room)
            temp_clr   = room_color.get(covering_mv.to_room_id, "#aaa")
            temp_name  = temp_room.get("name", "")
            parts.append(
                f"<td style='background:{MOVE_BG};color:{MOVE_FG};"
                f"border:2px solid {temp_clr};text-align:center;"
                f"min-width:28px;max-width:36px;font-size:9px;font-weight:700;"
                f"' title='Temporary cover in {temp_name}'>"
                f"{temp_code}†</td>"
            )
            continue

        # Normal slot — look up which room this educator is in at this slot
        current_rid  = slot_rooms.get(slot, row["primary_room_id"])
        current_room = room_map.get(current_rid, {})
        current_clr  = room_color.get(current_rid, "#888")
        current_code = _room_code(current_room)

        parts.append(
            f"<td style='background:{current_clr};color:#fff;"
            f"border:1px solid rgba(255,255,255,0.25);text-align:center;"
            f"min-width:28px;max-width:36px;font-size:9px;font-weight:600;'>"
            f"{current_code}</td>"
        )

    # Room summary columns — blank per educator row
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
    import math

    staff_counts: dict[str, dict[str, int]] = {}
    for s in shifts:
        rid = s.room_id
        for slot in slots:
            if _is_in_shift(slot, s.start_time, s.end_time):
                staff_counts.setdefault(rid, {})
                staff_counts[rid][slot] = staff_counts[rid].get(slot, 0) + 1

    html = []
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

        room_ivs      = children_map.get(rid, {})
        peak_children = max(room_ivs.values(), default=0)
        req_staff     = math.ceil(peak_children / r_c) * r_s if peak_children > 0 else r_s

        html.append("<tr style='background:#f0f4f8;'>")
        html.append(
            f"<td style='padding:3px 8px;border:1px solid #e2e8f0;font-weight:600;"
            f"color:{clr};min-width:120px;white-space:nowrap;'>{rname}</td>"
        )
        html.append(
            f"<td colspan='2' style='padding:3px 6px;border:1px solid #e2e8f0;"
            f"text-align:center;font-size:10px;color:#555;'>1:{r_c}</td>"
        )

        for slot in slots:
            count     = staff_counts.get(rid, {}).get(slot, 0)
            child_cnt = room_ivs.get(slot, 0)
            if count == 0:
                bg, fg = "#f8f9fa", "#ccc"
            else:
                needed = math.ceil(child_cnt / r_c) * r_s if child_cnt > 0 else r_s
                bg, fg = ("#dcfce7", "#14532d") if count >= needed else ("#fee2e2", "#991b1b")
            html.append(
                f"<td style='background:{bg};color:{fg};border:1px solid #e2e8f0;"
                f"text-align:center;font-size:9px;font-weight:700;"
                f"min-width:28px;max-width:36px;'>{count if count else ''}</td>"
            )

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
            f"font-weight:700;font-size:10px;color:#1e3a55;'>{req_staff}</td>"
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


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _build_slots() -> list[str]:
    slots = []
    t     = datetime.strptime(GRID_START, "%H:%M:%S")
    end   = datetime.strptime(GRID_END,   "%H:%M:%S")
    while t < end:
        slots.append(t.strftime("%H:%M:%S"))
        t += timedelta(minutes=SLOT_MINS)
    return slots


def _is_in_shift(slot: str, start: str, end: str) -> bool:
    return start <= slot < end


def _assign_room_colors(rooms: list[dict]) -> dict[str, str]:
    return {room["id"]: ROOM_PALETTE[i % len(ROOM_PALETTE)]
            for i, room in enumerate(rooms)}


def _room_code(room: dict) -> str:
    return room.get("name", "?")[:4].upper()


def _build_break_map(breaks, slots):
    label_map:  dict[str, dict[str, str]] = {}
    status_map: dict[str, dict[str, str]] = {}
    for brk in breaks:
        uid   = brk.user_id
        bs    = brk.planned_start_time
        be    = brk.planned_end_time
        dur   = brk.planned_duration_minutes
        label = f"B{dur}"
        stat  = brk.status
        label_map.setdefault(uid,  {})
        status_map.setdefault(uid, {})
        for slot in slots:
            if _is_in_shift(slot, bs, be):
                label_map[uid][slot]  = label
                status_map[uid][slot] = stat
    return label_map, status_map


def _build_movement_map(movements, slots):
    mv_map: dict[str, dict[str, list]] = {}
    for mv in movements:
        for slot in slots:
            if _is_in_shift(slot, mv.start_time, mv.end_time):
                mv_map.setdefault(slot, {})
                mv_map[slot].setdefault(mv.to_room_id, []).append(mv)
    return mv_map


def _build_children_map(intervals, slots):
    result: dict[str, dict[str, int]] = {}
    for iv in intervals:
        rid   = iv.get("room_id", "")
        istart = iv.get("interval_start", "")
        count  = iv.get("actual_children") or iv.get("expected_children") or 0
        if rid and istart:
            result.setdefault(rid, {})[istart] = int(count)
    return result


def _mins_str(start: str, end: str) -> int:
    try:
        s = datetime.strptime(start[:5], "%H:%M")
        e = datetime.strptime(end[:5],   "%H:%M")
        return max(0, int((e - s).total_seconds() / 60))
    except Exception:
        return 0
