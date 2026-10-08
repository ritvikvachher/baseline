"""The dashboard greeting ("GOOD MORNING, RITVIK") and the info line under it.

Kept free of tkinter so the wording rules can be tested on their own. The first rule
that matches wins, in the order pick_greeting checks them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional, Sequence, Tuple

BACK_AT_IT = timedelta(hours=3)     # reopened within this -> "BACK AT IT"
AWAY = timedelta(days=3)            # away at least this long -> "WELCOME BACK"
COUNTDOWN_DAYS = 14                 # "12 DAYS TO DELIVERY" from this many days out

MORNING, AFTERNOON, EVENING, NIGHT = range(4)
SLOT_GREETINGS = {
    MORNING: "GOOD MORNING",
    AFTERNOON: "GOOD AFTERNOON",
    EVENING: "GOOD EVENING",
    NIGHT: "BURNING THE MIDNIGHT OIL",
}


@dataclass
class Greeting:
    text: str           # "GOOD MORNING"
    name: str = ""      # "RITVIK", or "" when BASELINE doesn't know a real name
    badge: str = ""     # "12 DAYS": drawn as a red tag in front of the text
    slot: int = MORNING


def time_slot(now: datetime) -> int:
    """Morning 05:00-11:59, afternoon 12:00-16:59, evening 17:00-21:59, night 22:00-04:59."""
    if 5 <= now.hour < 12:
        return MORNING
    if 12 <= now.hour < 17:
        return AFTERNOON
    if 17 <= now.hour < 22:
        return EVENING
    return NIGHT


def pick_greeting(now: datetime, name: Optional[str], last_seen: Optional[datetime],
                  delivery: Optional[date] = None) -> Greeting:
    """`last_seen` is when this person last had BASELINE open (None = never)."""
    slot = time_slot(now)

    def greet(text, badge=""):
        return Greeting(text, (name or "").upper(), badge, slot)

    if last_seen is None:
        return greet("WELCOME TO BASELINE")
    days = (delivery - now.date()).days if delivery else None
    if days is not None and 1 <= days <= COUNTDOWN_DAYS:
        return greet("TO DELIVERY. LET'S GO", f"{days} DAY" if days == 1 else f"{days} DAYS")
    if days == 0:
        return greet("DELIVERY DAY. BRING IT HOME")
    away = now - last_seen
    if away < BACK_AT_IT:
        return greet("BACK AT IT")
    if away >= AWAY:
        return greet("WELCOME BACK")
    if now.weekday() == 4 and slot != NIGHT:
        return greet("LAST PUSH OF THE WEEK")
    return greet(SLOT_GREETINGS[slot])


# --- Info line ---------------------------------------------------------------
# A list of parts (shown joined by " · "). Each part is a list of (text, style) runs,
# style being "" (muted), "bold", "mono" or "link" (the whole part opens My tasks).

Run = Tuple[str, str]


def info_line(week: int, total_weeks: Optional[int], my_tasks: int,
              new_publishes: Sequence[Tuple[str, str]], first_launch: bool = False) -> List[List[Run]]:
    """`new_publishes`: [(first name, "SH010 anim v007")], newest first, other people only."""
    parts = []
    if total_weeks:
        parts.append([("Week ", ""), (str(week), "bold"), (" of ", ""), (str(total_weeks), "bold")])
    if my_tasks:
        parts.append([(f"{my_tasks} task{'' if my_tasks == 1 else 's'} assigned to you", "link")])
    if new_publishes:
        parts.append(_publishes_part(new_publishes))
    if not my_tasks and not new_publishes and not first_launch:
        parts.append([("All caught up", "")])
    return parts


def _publishes_part(publishes: Sequence[Tuple[str, str]]) -> List[Run]:
    if len(publishes) == 1:
        who, what = publishes[0]
        return [(who, "bold"), (" published ", ""), (what, "mono")]
    people = list(dict.fromkeys(who for who, _ in publishes))
    runs = [(people[0], "bold")]
    if len(people) == 2:
        runs += [(" and ", ""), (people[1], "bold")]
    elif len(people) > 2:
        runs += [(" and ", ""), (str(len(people) - 1), "bold"), (" others", "")]
    return runs + [(" published ", ""), (str(len(publishes)), "bold"), (" versions since you were last here", "")]
