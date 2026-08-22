import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ocr_region_watcher.templates import TemplateStore, empty_snapshot


class TemplateStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "templates.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_starts_empty(self):
        store = TemplateStore(self.path)
        self.assertEqual(store.names(), [])
        self.assertIsNone(store.get_last_active())

    def test_corrupt_file_falls_back_to_empty(self):
        self.path.write_text("not valid json{{{", encoding="utf-8")
        store = TemplateStore(self.path)
        self.assertEqual(store.names(), [])

    def test_malformed_templates_data_falls_back_to_empty(self):
        self.path.write_text('{"templates": ["a", "b"]}', encoding="utf-8")
        store = TemplateStore(self.path)
        self.assertEqual(store.names(), [])

    def test_save_then_get_round_trips(self):
        store = TemplateStore(self.path)
        snapshot = {
            "regions": [{"name": "Red", "formula_key": "PM", "left": 1, "top": 2, "width": 3, "height": 4}],
            "manual_inputs": [{"name": "C", "value": "5"}],
            "targets": [],
        }
        store.save("Template 1", snapshot)
        self.assertEqual(store.get("Template 1"), snapshot)
        # a fresh instance reading the same path sees it too
        reloaded = TemplateStore(self.path)
        self.assertEqual(reloaded.get("Template 1"), snapshot)

    def test_get_unknown_name_returns_none(self):
        store = TemplateStore(self.path)
        self.assertIsNone(store.get("nope"))

    def test_delete_removes_it_and_clears_last_active_if_it_matched(self):
        store = TemplateStore(self.path)
        store.save("Template 1", empty_snapshot())
        store.set_last_active("Template 1")
        store.delete("Template 1")
        self.assertNotIn("Template 1", store.names())
        self.assertIsNone(store.get_last_active())

    def test_delete_unrelated_template_leaves_last_active_alone(self):
        store = TemplateStore(self.path)
        store.save("Template 1", empty_snapshot())
        store.save("Template 2", empty_snapshot())
        store.set_last_active("Template 1")
        store.delete("Template 2")
        self.assertEqual(store.get_last_active(), "Template 1")

    def test_rename_moves_snapshot_and_updates_last_active(self):
        store = TemplateStore(self.path)
        store.save("Template 1", empty_snapshot())
        store.set_last_active("Template 1")
        store.rename("Template 1", "Site A")
        self.assertIsNone(store.get("Template 1"))
        self.assertEqual(store.get("Site A"), empty_snapshot())
        self.assertEqual(store.get_last_active(), "Site A")

    def test_next_default_name_skips_taken_numbers(self):
        store = TemplateStore(self.path)
        store.save("Template 1", empty_snapshot())
        store.save("Template 2", empty_snapshot())
        self.assertEqual(store.next_default_name(), "Template 3")

    def test_next_default_name_fills_a_gap(self):
        store = TemplateStore(self.path)
        store.save("Template 2", empty_snapshot())
        self.assertEqual(store.next_default_name(), "Template 1")


if __name__ == "__main__":
    unittest.main()
