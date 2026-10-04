# DR-015: The outer loop is integral-only, and does not wind up against a limit

| | |
|---|---|
| Status | Superseded by DR-054 |
| Since | Design v0.8, in the code from 0.6.0 (2026-08-16); anti-windup ordering fixed in 0.8.6 (2026-08-21) |
| Origin | Design v0.8; finding 7 |
| Related | DR-003, DR-016, DR-031, DR-037, DR-054 |

## Decision

The unit's thermostat regulates to its own return-air sensor, which is not the
room. An integral-only loop adds a trim to the commanded setpoint until the
room sensor reads the target. The trim is only integrated while the
compressor is actually working toward the target: never while the room is
off, coasting, held by an opening, refused a start by the guard, or capped by
the power ceiling.

## Why

Integral only, because the unit's thermostat is already the proportional loop.
A second proportional term is two controllers fighting over one actuator.

The anti-windup gate exists because integrating while nothing is correcting
the error winds the trim to its limit, and the first thing the room does on
coming back is overshoot by the full trim. Until 0.8.6 the regulator ran
before the short-cycle guard and integrated on the step that was wanted, not
the one carried out, so a start the guard had just refused was integrated
anyway (finding 7).

## Rejected

- **PI or PID.** Duplicates the unit's own proportional loop.
- **A fixed calibration offset.** Covered in `docs/regulation.md`: the offset
  between head and room is not constant.

## Consequences

Gain, deadband and trim limit are constants, not settings (DR-045). When the
trim pins at its limit, the trace says the unit is not keeping up.

## In the code

Checked against: 0.9.1. **Superseded by DR-054.** The integrator, its deadband and its anti-windup remain as the integral term of the loop; the proportional and derivative terms this record refused now exist, and the trim is kept per direction.

- `regulate.py:74` - `INTEGRAL_GAIN_PER_HOUR` - 0.35
- `regulate.py:79` - `DEADBAND_C` - 0.3 C
- `regulate.py:68` - `MAX_TRIM_C` - 3.0 C
- `regulate.py:149` - `integrate` - the gated integrator
- `coordinator.py:1062-1063` - `_guard_cycling` - guard first, then regulate
- `coordinator.py:1160-1163` - `regulating` - integrates only on the applied step, and not while capped
