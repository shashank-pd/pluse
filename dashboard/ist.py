# Functions for working with IST

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")

def now_ist_dt():
    return datetime.now(IST)

def now_ist_iso():
    return datetime.now(IST).isoformat()

def to_ist_iso(s):
    try:
        dt = datetime.fromisoformat(s)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(IST).isoformat()
    except:
        return s
