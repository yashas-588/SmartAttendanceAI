from datetime import datetime

# 🔥 MODE SWITCH
MODE = "TEST"   # 👉 "TEST" or "REAL"

# 🔥 TEST MODE SUBJECT (you control this anytime)
TEST_SUBJECT = "DAA"

timetable = {
    "MON": [
        ("09:00", "10:00", "DAA"),
        ("10:00", "11:00", "DBMS"),
        ("11:15", "12:15", "MAT")
    ],
    "TUE": [
        ("09:00", "11:00", "LAB"),
        ("11:15", "12:15", "COCA"),
        ("12:15", "01:15", "DAA")
    ],
    "WED": [
        ("09:00", "10:00", "COCA"),
        ("10:00", "11:00", "CBM")
    ],
    "THU": [
        ("09:00", "10:00", "COCA"),
        ("10:00", "11:00", "CBM")
    ],
    "FRI": [
        ("09:00", "10:00", "MAT"),
        ("10:00", "11:00", "DAA")
    ],
    "SAT": [
        ("09:00", "10:00", "DAA"),
        ("10:00", "11:00", "DBMS")
    ]
}

def get_current_class():
    # 🔥 TEST MODE (FREE CONTROL)
    if MODE == "TEST":
        return TEST_SUBJECT

    # 🔥 REAL MODE (TIMETABLE BASED)
    now = datetime.now()
    day = now.strftime("%a").upper()
    current_time = now.strftime("%H:%M")

    if day not in timetable:
        return None

    for start, end, subject in timetable[day]:
        if start <= current_time <= end:
            return subject

    return None