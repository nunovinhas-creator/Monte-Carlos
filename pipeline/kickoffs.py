"""Operational schedule separate from immutable, as-observed prediction times.

Callers own a BEGIN IMMEDIATE transaction. A revision is evidence observed now,
never a claim about when BSD originally changed its schedule.
"""
from datetime import datetime, timezone


def event_kickoff(event):
    for field in ('event_date', 'date', 'starting_at', 'match_date'):
        value = event.get(field)
        if not value:
            continue
        try:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if dt.tzinfo is not None:
                return int(dt.timestamp()), field
        except (ValueError, OverflowError):
            pass
    return None, None


def current(conn, match_id):
    return conn.execute('''SELECT operational_kickoff, revision_id, schedule_state
        FROM operational_schedule WHERE match_id=?''', (str(match_id),)).fetchone()


def observe(conn, match_id, kickoff, *, detected_at=None, source='bsd:events',
            change_kind=None, reason=None):
    """Apply only evidenced future schedules for pending, unscored events.

    Terminal rows and changes discovered after the proposed start are manual
    review cases. Repeated identical evidence is idempotent, including quarantine.
    A later evidenced future date can release a pending event from quarantine.
    """
    match_id = str(match_id)
    row = conn.execute('SELECT timestamp,status,result_o25,result_btts FROM predictions WHERE match_id=?',
                       (match_id,)).fetchone()
    if row is None:
        return None
    detected_at = detected_at or datetime.now(timezone.utc)
    if detected_at.tzinfo is None:
        raise ValueError('detection requires timezone')
    if kickoff is not None and (isinstance(kickoff, bool) or not isinstance(kickoff, int) or kickoff <= 0):
        raise ValueError('invalid kickoff')
    latest = conn.execute('''SELECT id,new_kickoff,state,reason FROM kickoff_revisions
        WHERE match_id=? ORDER BY id DESC LIMIT 1''', (match_id,)).fetchone()
    _, previous_id, _ = current(conn, match_id)
    old = conn.execute('SELECT last_confirmed_kickoff FROM operational_schedule WHERE match_id=?',(match_id,)).fetchone()[0]
    if latest and latest[1] == kickoff and (reason is None or latest[3] == reason):
        return latest[0]
    if not latest and kickoff == old and reason is None:
        return None
    state = 'applied'
    if row[1] != 'pending' or row[2] is not None or row[3] is not None:
        state, reason = 'quarantined', 'terminal_event_requires_manual_review'
    elif reason or kickoff is None:
        state, reason = 'quarantined', reason or 'missing_evidenced_kickoff'
    elif kickoff <= detected_at.timestamp():
        state, reason = 'quarantined', 'change_detected_at_or_after_new_kickoff'
    else:
        reason = 'future_schedule_observed_before_start'
    kind = change_kind or ('unknown' if kickoff is None or old is None else
                           'postponement' if kickoff > old else
                           'advancement' if kickoff < old else 'data_correction')
    if kind not in ('postponement', 'advancement', 'data_correction', 'unknown'):
        raise ValueError('invalid change kind')
    cursor = conn.execute('''INSERT INTO kickoff_revisions
        (match_id,detected_at,old_kickoff,new_kickoff,source,change_kind,state,reason,previous_revision_id)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (match_id,detected_at.astimezone(timezone.utc).isoformat(),old,kickoff,source,kind,state,reason,previous_id))
    def utc(ts):
        return datetime.fromtimestamp(ts,timezone.utc).isoformat() if ts is not None else 'N/D'
    print(f'Kickoff {match_id}: old={utc(old)} new={utc(kickoff)} source={source} '
          f'detected={detected_at.isoformat()} kind={kind} state={state} reason={reason}')
    return cursor.lastrowid


def observe_events(conn, events, detected_at=None):
    for event in events:
        if event.get('id') is None:
            continue
        kickoff, field = event_kickoff(event)
        if kickoff is not None:
            observe(conn,event['id'],kickoff,detected_at=detected_at,source=f'bsd:events:{field}')
