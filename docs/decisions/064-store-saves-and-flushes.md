# DR-064: Learned state is saved on one pending timer, choices are saved at once, and the store is flushed on unload

| | |
|---|---|
| Status | Accepted; amends DR-031 |
| Since | 0.9.3 (2026-10-12) |
| Origin | Living room overnight review, 11/12 October 2026, findings 2, 3 and 4 |
| Related | DR-031, DR-049, DR-058 |

## Decision

A delayed save of learned state is scheduled only when none is pending. A
choice the user makes (comfort mode, Automatic control, vanes, a profile) is
saved within five seconds. The store is written out when the config entry
unloads.

## Why

Sleep, set at 20:27, was gone after the 21:40 reload, and Automatic control off
was lost at 07:55. In Home Assistant's `Store`, a later `async_delay_save` call
raises the pending write time and the pending timer moves to it; that time is
never reset. The coordinator called it every evaluation with a five-minute
delay, so nothing reached disk while the integration ran, and a reload read a
stale file.

## Rejected

- **Save every evaluation.** A write every thirty seconds for figures that
  change slowly.
- **Shorter delays.** Re-arming still pushes the write out.
- **Flush only at Home Assistant stop.** A reload is not a stop.

## Consequences

At most five minutes of learning is lost to a crash.

## In the code

Checked against: 0.9.6. **Conforms.**

- `store.py:104` - `def _schedule_learned_save` - one pending save
- `store.py:116` - `def _save_choice_soon` - choices within five seconds
- `store.py:127` - `async def async_flush` - written now
- `__init__.py:175` - `await entry.runtime_data.store.async_flush()` - on unload
