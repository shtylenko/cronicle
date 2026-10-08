"""Natural-language schedule parsing: 'Every Wednesday at 5pm' -> '0 17 * * 3'.

Local rule-based parser, no network involved. Anything it cannot express as a
single cron line raises ValueError with examples (it never guesses silently).
"""
from __future__ import annotations

import re

from .core import validate_schedule

DAY_NUM = {
    "monday": 1, "mondays": 1, "mon": 1, "mons": 1,
    "tuesday": 2, "tuesdays": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wednesdays": 3, "wed": 3, "weds": 3,
    "thursday": 4, "thursdays": 4, "thu": 4, "thur": 4, "thurs": 4,
    "friday": 5, "fridays": 5, "fri": 5, "fris": 5,
    "saturday": 6, "saturdays": 6, "sat": 6, "sats": 6,
    "sunday": 0, "sundays": 0, "sun": 0, "suns": 0,
}
_DAY_ALT = "|".join(sorted(DAY_NUM, key=len, reverse=True))
TIME12_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b")
TIME24_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
TIME_AT_RE = re.compile(r"\bat\s+(\d{1,2})\b")
DOM_RE = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b")
DAY_RE = re.compile(rf"\b({_DAY_ALT})\b")
RANGE_RE = re.compile(rf"\b({_DAY_ALT})\s*(?:-|to)\s*({_DAY_ALT})\b")
MIN_INTERVAL_RE = re.compile(r"\bevery\s+(\d+)\s*(minutes?|mins?)\b")
EVERY_MINUTE_RE = re.compile(r"\bevery\s+minute\b|\bminutely\b")
HOUR_INTERVAL_RE = re.compile(r"\bevery\s+(\d+)\s*hours?\b")
EVERY_HOUR_RE = re.compile(r"\bevery\s+hour\b|\bhourly\b")

# Phrases that look parseable but mean something cron can't express in one line.
_AMBIGUOUS = [
    (re.compile(r"\bother\b"), "schedules like 'every other day' need two jobs; cron can't express them in one line"),
    (re.compile(r"\bbi-?weekly\b"), "'biweekly' can't be expressed in one cron line"),
    (re.compile(r"\bfirst\b|\bsecond\b|\bthird\b|\bfourth\b|\bfifth\b|\blast\b"),
     "nth-weekday schedules like 'first Monday' aren't supported"),
    (re.compile(r"\btwice\b|\bthrice\b|\b\d+\s+times\b"), "only one time of day per job is supported"),
]

_EXAMPLES = ("Try things like 'Every Wednesday at 5pm', 'Weekdays at 9am', "
              "'Every 15 minutes', or 'Daily at midnight'.")


def _fail(text: str, why: str = "") -> ValueError:
    msg = f"could not understand {text!r} as a schedule. "
    return ValueError(msg + (why + ". " if why else "") + _EXAMPLES)


def _extract_days(text: str) -> tuple[list[int], str]:
    """Returns (sorted unique weekday numbers, text with day words removed)."""
    days: list[int] = []
    if re.search(r"\bweekdays?\b", text):
        days += [1, 2, 3, 4, 5]
        text = re.sub(r"\bweekdays?\b", " ", text)
    if re.search(r"\bweekends?\b", text):
        days += [0, 6]
        text = re.sub(r"\bweekends?\b", " ", text)

    def expand(m: re.Match) -> str:
        start, end = DAY_NUM[m.group(1)], DAY_NUM[m.group(2)]
        n = start
        while True:
            days.append(n)
            if n == end:
                break
            n = (n + 1) % 7
        return " "

    text = RANGE_RE.sub(expand, text)
    for m in DAY_RE.finditer(text):
        days.append(DAY_NUM[m.group(1)])
    text = DAY_RE.sub(" ", text)
    return sorted(set(days)), text


def _extract_time(text: str) -> tuple[tuple[int, int] | None, str]:
    """Returns ((minute, hour) or None if no time given, remaining text)."""
    times: list[tuple[int, int]] = []

    def take12(m: re.Match) -> str:
        hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if not 1 <= hour <= 12 or minute > 59:
            raise _fail(m.group(0), f"{m.group(0)!r} is not a valid time")
        if meridiem == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
        times.append((minute, hour))
        return " "

    try:
        text = TIME12_RE.sub(take12, text)
    except ValueError:
        raise

    def take24(m: re.Match) -> str:
        times.append((int(m.group(2)), int(m.group(1))))
        return " "

    text = TIME24_RE.sub(take24, text)

    if "midnight" in text:
        times.append((0, 0))
        text = text.replace("midnight", " ")
    if "noon" in text or "midday" in text:
        times.append((0, 12))
        text = text.replace("noon", " ").replace("midday", " ")

    def take_at(m: re.Match) -> str:
        hour = int(m.group(1))
        if hour > 23:
            raise _fail(m.group(0), f"{m.group(0)!r} is not a valid time")
        times.append((0, hour))
        return " "

    text = TIME_AT_RE.sub(take_at, text)

    unique = sorted(set(times))
    if len(unique) > 1:
        raise ValueError("only one time of day per job is supported. " + _EXAMPLES)
    return (unique[0] if unique else None), text


def _has_time_hint(text: str) -> bool:
    return bool(re.search(r"\bat\b|midnight|noon|midday|am\b|pm\b|:", text))


def parse_natural_schedule(text: str) -> str:
    """Parse plain English into a 5-field cron schedule (or @-form)."""
    original = text
    s = re.sub(r"\s+", " ", text.strip().lower())
    if not s:
        raise _fail(original)

    for pattern, why in _AMBIGUOUS:
        if pattern.search(s):
            raise _fail(original, why)

    days, rest = _extract_days(s)

    minute_interval = MIN_INTERVAL_RE.search(s)
    hour_interval = HOUR_INTERVAL_RE.search(s)
    if minute_interval or EVERY_MINUTE_RE.search(s) or hour_interval or EVERY_HOUR_RE.search(s):
        if _has_time_hint(rest):
            raise _fail(original, "a time of day can't be combined with an hourly/minute interval")
        if minute_interval:
            n = int(minute_interval.group(1))
            if n < 1 or n > 59:
                raise _fail(original, "minute interval must be 1-59")
            sched = f"*/{n} * * * *"
        elif hour_interval:
            n = int(hour_interval.group(1))
            if n < 1 or n > 23:
                raise _fail(original, "hour interval must be 1-23")
            sched = f"0 */{n} * * *"
        elif EVERY_MINUTE_RE.search(s):
            sched = "* * * * *"
        else:
            sched = "0 * * * *"
        if days:
            sched = sched.rsplit(" ", 1)[0] + " " + ",".join(map(str, days))
        return validate_schedule(sched)

    try:
        time, rest = _extract_time(rest)
    except ValueError as e:
        if "could not understand" in str(e):
            raise _fail(original, str(e).split(". ")[1] if ". " in str(e) else "")
        raise _fail(original, str(e).split(". ")[0])

    minute, hour = time or (0, 0)

    dom_match = DOM_RE.search(rest)
    dom = None
    if dom_match:
        dom = int(dom_match.group(1))
        if dom < 1 or dom > 31:
            raise _fail(original, "day of month must be 1-31")
        rest = DOM_RE.sub(" ", rest)
    if dom is not None and days:
        raise _fail(original, "pick a day of the month or day(s) of the week, not both")

    month = "*"
    if re.search(r"\byearly\b|\bannually\b", rest):
        month, dom = "1", dom or 1
        rest = re.sub(r"\byearly\b|\bannually\b", " ", rest)
    if re.search(r"\bmonthly\b", rest):
        dom = dom or 1
        rest = re.sub(r"\bmonthly\b", " ", rest)
    if re.search(r"\bweekly\b", rest):
        days = days or [0]
        rest = re.sub(r"\bweekly\b", " ", rest)

    recognized = bool(time) or bool(days) or dom_match or bool(
        re.search(r"\bdaily\b|\bevery\b|\bday\b|\bweekly\b|\bmonthly\b|\byearly\b"
                  r"|\bannually\b|\bhourly\b|\bminutely\b", s))
    if not recognized:
        raise _fail(original)

    sched = f"{minute} {hour} {dom or '*'} {month} {','.join(map(str, days)) if days else '*'}"
    return validate_schedule(sched)


# --- cron -> plain English (display only; never raises) ---

_MONTH_TOKENS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_DOW_FULL = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
_MONTH_FULL = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]
_AT_DESC = {
    "@hourly": "Hourly", "@reboot": "At system reboot",
    "@daily": "Daily at midnight", "@midnight": "Daily at midnight",
    "@weekly": "Every Sunday at midnight", "@monthly": "Monthly on day 1 at midnight",
    "@yearly": "Yearly on January 1 at midnight",
    "@annually": "Yearly on January 1 at midnight",
}


class _Undescribable(Exception):
    pass


def _tok_num(token: str, token_map: dict | None, lo: int, hi: int) -> int:
    t = token.strip().lower()
    if token_map and t in token_map:
        return token_map[t]
    try:
        n = int(t)
    except ValueError:
        raise _Undescribable
    if not lo <= n <= hi:
        raise _Undescribable
    return n


def _parse_field(field: str, token_map: dict | None, lo: int, hi: int):
    """Returns ("all", None) | ("step", n) | ("values", [ints])."""
    f = field.strip()
    if f == "*":
        return ("all", None)
    if "/" in f:
        base, _, step = f.partition("/")
        if base.strip() != "*" or not step.strip().isdigit():
            raise _Undescribable
        n = int(step)
        if n <= 1:
            return ("all", None)
        if n > hi:
            raise _Undescribable
        return ("step", n)
    vals: list[int] = []
    for part in f.split(","):
        part = part.strip()
        if not part:
            raise _Undescribable
        if "-" in part:
            a, _, b = part.partition("-")
            start = _tok_num(a, token_map, lo, hi)
            end = _tok_num(b, token_map, lo, hi)
            if end < start:
                raise _Undescribable
            vals.extend(range(start, end + 1))
        else:
            vals.append(_tok_num(part, token_map, lo, hi))
    out = sorted(set(vals))
    if not out:
        raise _Undescribable
    return ("values", out)


def _fmt_time(hour: int, minute: int) -> str:
    ap = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {ap}"


def _join_and(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _day_prefix(days: list[int]) -> str:
    if days == [1, 2, 3, 4, 5]:
        return "Weekdays"
    if days == [0, 6]:
        return "Weekends"
    if len(days) == 1:
        return f"Every {_DOW_FULL[days[0]]}"
    return "Every " + _join_and([_DOW_FULL[d] for d in days])


def _day_suffix(days: list[int]) -> str:
    if days == [1, 2, 3, 4, 5]:
        return "on weekdays"
    if days == [0, 6]:
        return "on weekends"
    if len(days) == 1:
        return f"on {_DOW_FULL[days[0]]}"
    return "on " + _join_and([f"{_DOW_FULL[d]}s" for d in days])


def _describe5(minute_f: str, hour_f: str, dom_f: str, mon_f: str, dow_f: str) -> str:
    minute = _parse_field(minute_f, None, 0, 59)
    hour = _parse_field(hour_f, None, 0, 23)
    dom = _parse_field(dom_f, None, 1, 31)
    month = _parse_field(mon_f, _MONTH_TOKENS, 1, 12)
    dow = _parse_field(dow_f, DAY_NUM, 0, 7)
    if dow[0] == "values":
        dow = ("values", sorted({0 if v == 7 else v for v in dow[1]}))

    interval: str | None = None
    times: list[str] = []
    hourly_at: str | None = None
    if minute[0] == "all" and hour[0] == "all":
        interval = "Every minute"
    elif minute[0] == "step" and hour[0] == "all":
        interval = f"Every {minute[1]} minutes"
    elif minute[0] == "all" and hour[0] == "step":
        interval = f"Every minute, every {hour[1]} hours"
    elif hour[0] == "step":
        if minute[0] == "values" and len(minute[1]) == 1:
            if minute[1][0] == 0:
                interval = f"Every {hour[1]} hours"
            else:
                interval = f"At :{minute[1][0]:02d} past every {hour[1]} hours"
        else:
            raise _Undescribable
    elif hour[0] == "all":
        if minute[0] != "values" or len(minute[1]) > 4:
            raise _Undescribable
        hourly_at = "Every hour at " + _join_and([f":{m:02d}" for m in minute[1]])
    else:
        if minute[0] != "values" or hour[0] != "values":
            raise _Undescribable
        combos = [(h, m) for h in hour[1] for m in minute[1]]
        if len(combos) > 4:
            raise _Undescribable
        times = [_fmt_time(h, m) for h, m in combos]

    prefix: str | None = None   # "Every Monday" / "Weekdays" (time-based, dow only)
    suffix: str | None = None   # "on weekdays" / "on January 1" (lowercase start)
    if dom[0] == "all" and month[0] == "all":
        if dow[0] == "values":
            prefix = _day_prefix(dow[1])
            suffix = _day_suffix(dow[1])
        elif dow[0] == "step":
            raise _Undescribable
    else:
        bits: list[str] = []
        if month[0] == "values" and dom[0] == "values":
            if len(month[1]) == 1 and len(dom[1]) == 1:
                bits.append(f"on {_MONTH_FULL[month[1][0]]} {dom[1][0]}")
            else:
                raise _Undescribable
        elif dom[0] == "values":
            if len(dom[1]) > 4:
                raise _Undescribable
            bits.append("on day " + _join_and([str(d) for d in dom[1]]) + " of the month")
        elif month[0] == "values":
            if len(month[1]) > 4:
                raise _Undescribable
            bits.append("every day in " + _join_and([_MONTH_FULL[m] for m in month[1]]))
        elif dom[0] == "step":
            bits.append(f"every {dom[1]} days")
        else:
            raise _Undescribable
        if dow[0] == "values":
            bits.append(_day_suffix(dow[1]))
        elif dow[0] == "step":
            raise _Undescribable
        suffix = " or ".join(bits)  # cron fires when EITHER day field matches

    if interval:
        return interval if suffix is None else f"{interval} {suffix}"
    if hourly_at:
        return hourly_at if suffix is None else f"{hourly_at} {suffix}"
    at = " at " + _join_and(times)
    if prefix:
        return prefix + at
    if suffix:
        return suffix[0].upper() + suffix[1:] + at
    return "Daily" + at


def describe_schedule(schedule: str) -> str:
    """Describe a cron schedule in plain English; returns input on anything odd."""
    s = " ".join(schedule.split())
    if s in _AT_DESC:
        return _AT_DESC[s]
    parts = s.split(" ")
    if len(parts) != 5:
        return schedule
    try:
        return _describe5(*parts)
    except _Undescribable:
        return schedule
    except Exception:
        return schedule
