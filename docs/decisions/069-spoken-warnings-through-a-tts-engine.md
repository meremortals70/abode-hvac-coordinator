# DR-069: Spoken warnings go through one text-to-speech engine, and a warning that fails never stops an evaluation

| | |
|---|---|
| Status | Accepted; amends DR-050; amended by DR-071 in 0.9.6 |
| Since | 0.9.5 (2026-10-13) |
| Origin | Incident, Office, 12–13 October 2026: eight evaluations failed with "required key not provided at 'media_player_entity_id'". Instruction, 2026-10-13: "ok build it" |
| Related | DR-006, DR-012, DR-050 |

## Decision

A spoken warning calls `tts.speak` with the house's text-to-speech engine as the
target and the room's speakers as `media_player_entity_id`. The engine is one
house-level setting; left empty, the only engine Home Assistant has is used.
Where there is no engine to use, no warning is spoken and a repair issue says
why. A warning that cannot be given, for any reason, is logged once and never
stops the rest of the evaluation.

## Why

The call sent the speakers as the target. In Home Assistant, `tts.speak`
targets an engine entity and requires the speakers as `media_player_entity_id`,
so every call was rejected and no warning was ever spoken. The rejection is a
validation error, not the `HomeAssistantError` the call was guarded against,
so it escaped: each time the Office emptied, that evaluation failed after its
commands had been sent, skipping device clean-up, draw learning, the demand
forecast and the save of what was learned. The tests recorded `tts.speak`
through a mock with no schema, so the rejected call passed them.

## Rejected

- **`media_player.play_media` with a text-to-speech media source.** It names
  the engine inside a URL; the engine is still a choice, and the call is less
  direct than the service built for it.
- **An engine per room.** A house speaks with one voice; the speakers are the
  per-room part.
- **Catch only the validation error.** The point is that a warning, which is
  advice, can never cost a room its control, whatever goes wrong.

## Consequences

A house with several engines must choose one before any warning is spoken; the
repair issue names them. The tests set up Home Assistant's own `tts`
integration to take its real schema, which adds `mutagen` and `ha-ffmpeg` to
the test requirements.

## In the code

Checked against: 0.9.6. **Conforms**, as amended by DR-071.

- `const.py:181` - `CONF_TTS_ENGINE: Final = "tts_engine_entity_id"` - the house-level setting
- `coordinator.py:2245` - `def _tts_engine` - the chosen engine, or the only one
- `coordinator.py:2320` - `"media_player_entity_id": list(` - the speakers, as Home Assistant requires
- `coordinator.py:2327` - `except Exception as err:  # noqa: BLE001 - a spoken warning must never stop the evaluation` - contained
- `coordinator.py:2386` - `def _note_announce_failure` - logged once
- `config_flow.py:788` - `async def async_step_announcements` - where the engine is chosen
