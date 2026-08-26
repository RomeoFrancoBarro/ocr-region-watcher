import unittest

from ocr_region_watcher.templates import empty_snapshot, events_from_snapshot, events_snapshot


class _StubTarget:
    """Stands in for a qt.target.TargetMarker -- these functions only ever
    care about object identity/position within `targets`, never anything
    Qt-specific, so a bare stub is enough to test them without a
    QApplication."""


class EventsSnapshotTests(unittest.TestCase):
    def test_round_trips_through_the_saved_shape(self):
        t0, t1 = _StubTarget(), _StubTarget()
        targets = [t0, t1]
        events = [{"target": t1, "delay": 250}, {"target": t0, "delay": 0}]

        saved = events_snapshot(events, targets)
        self.assertEqual(saved, [{"target_index": 1, "delay": 250}, {"target_index": 0, "delay": 0}])

        restored = events_from_snapshot(saved, targets)
        self.assertEqual(restored, [{"target": t1, "delay": 250}, {"target": t0, "delay": 0}])

    def test_same_target_referenced_by_more_than_one_step(self):
        t0 = _StubTarget()
        events = [{"target": t0, "delay": 0}, {"target": t0, "delay": 100}]

        saved = events_snapshot(events, [t0])
        self.assertEqual(saved, [{"target_index": 0, "delay": 0}, {"target_index": 0, "delay": 100}])

    def test_step_pointing_at_a_target_no_longer_in_the_list_is_dropped(self):
        t0 = _StubTarget()
        removed = _StubTarget()
        events = [{"target": t0, "delay": 0}, {"target": removed, "delay": 50}]

        saved = events_snapshot(events, [t0])  # `removed` isn't in the live targets list any more

        self.assertEqual(saved, [{"target_index": 0, "delay": 0}])

    def test_restoring_against_fewer_targets_drops_out_of_range_steps(self):
        # e.g. hand-edited JSON, or a template whose targets section
        # failed to restore in full -- shouldn't raise, just skip it.
        data = [{"target_index": 0, "delay": 0}, {"target_index": 5, "delay": 100}]

        restored = events_from_snapshot(data, [_StubTarget()])

        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["delay"], 0)

    def test_restoring_tolerates_malformed_entries(self):
        data = [{"delay": 0}, {"target_index": "not-an-int", "delay": 0}]

        restored = events_from_snapshot(data, [_StubTarget()])

        self.assertEqual(restored, [])

    def test_restoring_defaults_missing_delay_to_zero(self):
        restored = events_from_snapshot([{"target_index": 0}], [_StubTarget()])

        self.assertEqual(restored, [{"target": restored[0]["target"], "delay": 0}])


class EmptySnapshotTests(unittest.TestCase):
    def test_includes_events_and_loop(self):
        self.assertEqual(
            empty_snapshot(),
            {"regions": [], "manual_inputs": [], "targets": [], "events": [], "loop": False},
        )


if __name__ == "__main__":
    unittest.main()
