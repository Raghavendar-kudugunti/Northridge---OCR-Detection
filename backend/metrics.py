"""Process-local aggregate counters. Values are metadata only and reset on restart."""
from collections import Counter
from threading import Lock

_lock = Lock()
_requests = Counter()
_checks = Counter()
_entities = Counter()
_stages = Counter()
_fallbacks = 0

def record(status, outcome, entities, stage_ms, fallback_count=0):
    global _fallbacks
    with _lock:
        _requests[outcome] += 1
        _checks[status] += 1
        _entities.update(entities)
        for stage, duration in stage_ms.items():
            _stages[f'{stage}.count'] += 1
            _stages[f'{stage}.total_ms'] += int(duration)
        _fallbacks += int(fallback_count)
        return snapshot_locked()

def snapshot_locked():
    return {'outcomes': dict(_requests), 'check_status': dict(_checks),
            'fallback_count': _fallbacks, 'entity_counts': dict(_entities),
            'stage_durations': dict(_stages)}

def snapshot():
    with _lock:
        return snapshot_locked()
