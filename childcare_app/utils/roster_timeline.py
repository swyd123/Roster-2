from datetime import datetime, timedelta
import pandas as pd


def generate_time_slots():
    slots = []
    current = datetime.strptime("07:00", "%H:%M")
    end = datetime.strptime("18:00", "%H:%M")

    while current <= end:
        slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=15)

    return slots


def build_roster_grid(roster_rows):
    """
    roster_rows:
    [
        {
            "educator": "Sara",
            "room": "Discovery",
            "start": "07:00",
            "finish": "16:00"
        }
    ]
    """

    slots = generate_time_slots()

    rows = []

    for staff in roster_rows:

        row = {
            "Educator": staff["educator"],
            "Start": staff["start"],
            "Finish": staff["finish"]
        }

        start = datetime.strptime(staff["start"], "%H:%M")
        finish = datetime.strptime(staff["finish"], "%H:%M")

        for slot in slots:

            slot_time = datetime.strptime(slot, "%H:%M")

            if start <= slot_time < finish:
                row[slot] = staff["room"]
            else:
                row[slot] = ""

        rows.append(row)

    return pd.DataFrame(rows)
