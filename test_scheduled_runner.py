import json,tempfile,unittest
from pathlib import Path
from scheduled_runner import take_next_model,DEFAULT_MODEL

class NextModel(unittest.TestCase):
    def test_choice_is_used_once_then_default_restored(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'next_run_model.json'
            path.write_text(json.dumps({'model':'anthropic/claude-haiku-5.5'}),encoding='utf-8')
            self.assertEqual(take_next_model(path),'anthropic/claude-haiku-5.5')
            self.assertEqual(take_next_model(path),DEFAULT_MODEL)
            self.assertTrue(path.with_name('consumed_run_model.json').exists())

    def test_invalid_choice_is_not_consumed(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'next_run_model.json'
            path.write_text(json.dumps({'model':'bad model; command'}),encoding='utf-8')
            with self.assertRaises(ValueError):take_next_model(path)
            self.assertTrue(path.exists())
