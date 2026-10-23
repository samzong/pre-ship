import fcntl
import json
import os
from pathlib import Path
import time

LIMIT = 1024 * 1024


class GateError(ValueError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


def event_path():
    return Path.home() / '.cache' / 'pre-ship' / 'events.jsonl'


def log_event(root, event, result, reason):
    try:
        path = event_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            row = json.dumps({'time': time.time(), 'root': root, 'event': event, 'result': result, 'reason': reason}, ensure_ascii=True) + '\n'
            if path.exists() and path.stat().st_size + len(row.encode()) > LIMIT:
                os.replace(path, path.with_suffix('.jsonl.1'))
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'w') as output:
                output.write(row)
    except (OSError, ValueError, TypeError, UnicodeError):
        pass


def event_summary():
    path = event_path()
    counts = {}
    skipped = 0
    for source in [path.with_suffix('.jsonl.1'), path]:
        try:
            with source.open() as rows:
                for row in rows:
                    try:
                        value = json.loads(row)
                        key = (value['result'], value['reason'])
                        if key[0] not in {'allow', 'block', 'exempt'} or not isinstance(key[1], str):
                            raise ValueError('Invalid event')
                        counts[key] = counts.get(key, 0) + 1
                    except (ValueError, TypeError, KeyError):
                        skipped += 1
        except FileNotFoundError:
            continue
    return {'counts': [{'result': result, 'reason': reason, 'count': count} for (result, reason), count in sorted(counts.items())], 'skipped': skipped}
