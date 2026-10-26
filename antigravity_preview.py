"""Run an isolated real scanner preview through official Antigravity CLI."""
from pathlib import Path
import sys
import uuid


def main():
    if any(arg == "--output-dir" or arg.startswith("--output-dir=") for arg in sys.argv[1:]):
        raise ValueError("Preview output location is managed automatically")
    if any(arg in {"--compare", "-c", "--model", "-m"} or arg.startswith("--model=") for arg in sys.argv[1:]):
        raise ValueError("Select a non-Haiku CLI model using ANTIGRAVITY_MODEL, not --model/--compare")
    import config
    import antigravity_client
    root = Path(config.PROJECT_DIR) / "reports" / "antigravity-preview" / uuid.uuid4().hex[:12]
    config.AI_PROVIDER = "antigravity"
    config.SEEN_LEDGER_PATH = str(root / "state" / "seen_items.json")
    config.ENRICHMENT_CACHE_DIR = str(root / "cache")
    # Fail before discovery if the CLI is unavailable or not authenticated.
    model = "antigravity/" + (config.ANTIGRAVITY_MODEL or "default")
    antigravity_client.generate(model, [{"role": "user", "content": "Reply with OK."}], purpose="preflight")
    import main as scanner
    sys.argv = [sys.argv[0], "--output-dir", str(root / "reports"), "--model", model,
                "--no-email", "--as-of", "now", *sys.argv[1:]]
    scanner.main()


if __name__ == "__main__":
    main()
