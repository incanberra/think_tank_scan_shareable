"""Scheduled launch with an optional one-run model choice."""
import json
import re
import sys
from pathlib import Path
import config

DEFAULT_MODEL = config.OPENROUTER_MODEL

def take_next_model(path):
    path = Path(path)
    if not path.exists():
        return DEFAULT_MODEL
    model = json.loads(path.read_text(encoding='utf-8'))['model']
    if not isinstance(model, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._:-]*/[a-z0-9][a-z0-9._:-]*', model):
        raise ValueError('Invalid next-run model; choice left unconsumed')
    path.replace(path.with_name('consumed_run_model.json'))
    return model

def main():
    import config
    import main as scanner
    from state_storage import scanner_lock
    # Do not consume the choice when another scan already holds the lock.
    with scanner_lock():
        model = take_next_model(Path(config.PROJECT_DIR)/'.scanner_state/next_run_model.json')
        print('Scheduled model:', model, flush=True)
        sys.argv = [sys.argv[0], '--model', model]
        scanner.main()

if __name__ == '__main__':
    main()
