"""
AstroHealth - actions on alarms (shared by wristband and helmet, no Tk).

Clearly separates:
- RECEPTION: the notice reached the wristband (the engine records it when creating the alarm).
- ACKNOWLEDGED: read receipt. Silences ONLY the current cycle for that person; if nobody ATTENDS,
  the alarm repeats every REPEAT_S and sounds/blinks again (and is escalated to the ship).
- ATTENTION (I'M ON MY WAY): someone takes charge. Stops repetitions and blinking for everyone.
- RESOLUTION: the engine closes it when the indicator is back within range (not when going from red to yellow).

Rewards (one per action, never duplicated between the helmet and wristband of the same owner):
- Affected person presses ACKNOWLEDGED for the first time in the incident -> +10.
- Crewmate responds for the first time within REPEAT_S from the START of the incident -> +5.
- First crewmate to press I'M ON MY WAY -> +20.
"""
from simulation import (PHASE_CRITICAL, REPEAT_S, SIGNAL, IND, OK, ALERT, NO_SIGNAL, INVALID,
                        STATE_ORDER, ALERT_PRIORITY)


def crew_group(mission, d):
    """Open alarms of the same person (one response covers all of them)."""
    return [x for x in mission.broadcast_queue() if x.affected == d.affected]


def respond(mission, routines, owner, d, action):
    """action: 'ack', 'view' or 'go'. Returns a dict with what happened (for tests and records)."""
    result = {"new": 0, "points": 0, "first_to_attend": False}
    alarms = crew_group(mission, d)
    if not alarms:
        return result
    if action in ("ack", "view"):
        times = [mission.acknowledged(x.id, owner) for x in alarms]
        new_ones = [s for s in times if s is not None]
        result["new"] = len(new_ones)
        if new_ones and routines is not None:
            if d.affected == owner:
                result["points"] = routines.handled_own_alert(owner)
            elif min(new_ones) <= REPEAT_S:
                result["points"] = routines.ack_on_time(owner)
    elif action == "go":
        if d.affected == owner:
            return result
        first_ones = [mission.attend(x.id, owner) for x in alarms]
        result["first_to_attend"] = any(first_ones)
        if any(first_ones):
            mission.record_on_the_way(owner, d)
        result["new"] = sum(first_ones)
        if any(first_ones) and routines is not None:
            result["points"] = routines.helped_crewmate(owner)
    return result


def notice_for(mission, owner, force_id=None):
    """Alarm this person must see in large format (or None)."""
    queue_ = mission.broadcast_queue()
    if force_id is not None:
        d = next((x for x in queue_ if x.id == force_id), None)
        if d is not None:
            return d
    for d in queue_:
        if d.phase != PHASE_CRITICAL:
            continue                                   # in caution it is no longer shown in large format
        if owner in d.cycle_acks or d.attended_by == owner:
            continue
        if d.affected == owner or d.attended_by is None:
            return d
    return None


def alarm_summary(mission, owner):
    """One entry per person with open alarms (for the fixed strip visible on every tab)."""
    people = {}
    for d in mission.broadcast_queue():
        people.setdefault(d.affected, []).append(d)
    items = []
    for affected, alarms in people.items():
        d = alarms[0]
        t = mission.crew[affected]
        who = "YOU" if affected == owner else t.name_.split()[0]
        if d.phase != PHASE_CRITICAL:
            status = "left critical · in caution"
        elif d.attended_by is None:
            status = "waiting for help" + (" · ESCALATED" if d.escalated else "")
        elif d.attended_by == owner:
            status = "you are attending"
        else:
            status = f"{mission.crew[d.attended_by].name_.split()[0]} is attending"
        datum = "no signal" if d.key == SIGNAL else f"{d.indicator_name().split(' (')[0]} {d.value_text()}"
        extra = f" (and {len(alarms) - 1} more)" if len(alarms) > 1 else ""
        items.append({"affected": affected, "id": d.id, "critical": d.phase == PHASE_CRITICAL,
                      "text": f"{who} · {datum}{extra} · {status}"})
    return items


def segments(values_):
    """Splits a series into segments without gaps (None). Never joins points across a signal loss."""
    segments_, current_ = [], []
    for i, v in enumerate(values_):
        if v is None:
            if current_:
                segments_.append(current_)
            current_ = []
        else:
            current_.append((i, v))
    if current_:
        segments_.append(current_)
    return segments_


# short action for the action line (2nd person / to help a crewmate)
ALERT_ACTION = "stop the activity and tell the medical officer"


def action_line(t, is_you):
    """A single action line for the most worrying indicator, or None if everything is green.
    E.g.: "Pulse high: rest 10 min and breathe slowly."  (includes sleep and exercise)"""
    key = max(ALERT_PRIORITY, key=lambda c: (STATE_ORDER[t.indicator_state(c)],
                                                 -ALERT_PRIORITY.index(c)))
    e = t.indicator_state(key)
    if e == OK:
        return None, OK
    if e == NO_SIGNAL:
        return ("No signal: check that the sensor is well placed and has battery." if is_you
                else f"No signal from {t.name_.split()[0]}: call them on the radio."), e
    if e == INVALID or t.values_.get(key) is None:
        return ("Invalid data: check that the sensor is well placed." if is_you
                else f"Invalid data from {t.name_.split()[0]}: ask them to check their sensor."), e
    ind = IND[key]
    low = t.values_[key] < ind.range_[0]
    status = "low" if low else "high"
    if e == ALERT:
        action = ALERT_ACTION if is_you else "go help them and tell the medical officer"
    else:
        action = (ind.actions[0 if low else 1] or "rest 10 min and measure again") if is_you \
            else "ask them how they are"
    subject = ind.simple.capitalize() if is_you else f"{t.name_.split()[0]}'s {ind.simple}"
    return f"{subject} {status}: {action}.", e
