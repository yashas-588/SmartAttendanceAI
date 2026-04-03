from datetime import datetime

# 🔥 TEST MODE SWITCH
TEST_MODE = True   # 👉 change to False later

# 🔥 FORCE SUBJECT WHEN TESTING
TEST_SUBJECT = "DAA"

timetable = {
    "MON": [("09:00","10:00","DAA"), ("10:00","11:00","DBMS")],
    "TUE": [("09:00","11:00","LAB")],
    "WED": [("09:00","10:00","COCA")],
    "THU": [("09:00","10:00","COCA")],
    "FRI": [("09:00","10:00","MAT")],
    "SAT": [("09:00","10:00","DAA")]
}

def get_current_class():
    # 🔥 FORCE MODE
    if TEST_MODE:
        return TEST_SUBJECT

    now = datetime.now()
    day = now.strftime("%a").upper()
    current_time = now.strftime("%H:%M")

    if day not in timetable:
        return None

    for start, end, subject in timetable[day]:
        if start <= current_time <= end:
            return subject

    return None