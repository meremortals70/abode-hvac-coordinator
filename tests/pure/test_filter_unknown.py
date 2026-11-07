"""The heat filter as architecture.md 4.3 and its pseudocode say: an unknown input widens the air with or without a reading; Q_air is learned on state-only steps."""

from __future__ import annotations

import unittest

from tests.pure import test_model as tm
from tests.pure._load import load

NOW, feed = tm.NOW, tm.feed

filt = load("model.filter")
physics = load("model.physics")


def minute(reading: float | None, *, known: bool) -> object:
    # cooling mode, effort 2 C, compressor known (stopped) or unknown
    return filt.MinuteSample(NOW, 12.0, 24.0, 60.0, 0.0, False, reading, 50.0, 2.0, 1, 1, False, known, False)


class UnknownInputsAndRoomNoise(unittest.TestCase):
    def test_an_unknown_compressor_widens_the_air_even_with_no_reading(self) -> None:
        """4.3: "An input the step could not know widens the air and mass by
        the variance of its possible effect"; the pseudocode puts it in
        PREDICT, before "NO CORRECTION when the room reading is STOOD-IN or
        MISSING"."""
        known = tm.Filter.ready(tm.Filter())
        feed(known, [minute(None, known=True)] * 5)
        unknown = tm.Filter.ready(tm.Filter())
        feed(unknown, [minute(None, known=False)] * 5)
        self.assertGreater(unknown.p[0][0], known.p[0][0] + 1e-6,
                           "no widening while the reading is missing")

    def test_q_air_is_learned_on_state_only_steps(self) -> None:
        """Q_air is learned once a day of learning is done, and at once on a
        step that corrects the state only: a room whose compressor is never
        known would otherwise never follow its own moves [R17][R21]."""
        f = tm.Filter.ready(tm.Filter())
        for k in range(250):  # state-only corrections, the room moving 0.6 C every half hour
            feed(f, [minute(24.0 + (0.6 if (k // 6) % 2 else 0.0), known=False)] * 5)
        self.assertLess(f.corrections, 288)
        self.assertGreater(f.air_noise, 0.0)

if __name__ == "__main__":
    unittest.main()
