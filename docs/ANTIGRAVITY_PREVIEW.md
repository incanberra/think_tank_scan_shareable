# Antigravity CLI preview

Experimental replacement for the scanner's OpenRouter model requests. The ordinary
scanner and scheduled task still default to OpenRouter. No scheduled task is
installed or changed. Antigravity runs cannot send email, including when SMTP
credentials exist or the caller omits `--no-email`.

## Run on your Windows PC

Use a separate checkout so your scheduled scanner continues running its existing
code and settings:

```powershell
git clone --branch feature/antigravity-cli-preview https://github.com/incanberra/think_tank_scan_shareable.git think-tank-antigravity-preview
cd think-tank-antigravity-preview
python -m pip install -r requirements.txt
agy
```

Complete the official CLI sign-in, then exit it. Verify `agy models` works. The
preview uses your CLI default model unless `ANTIGRAVITY_MODEL` is set to a specific
non-Haiku slug. No OpenRouter key or SMTP credentials are required. Optional
settings can go in `.env` (see `.env.example`). If Python cannot find `agy`, set
`ANTIGRAVITY_CLI` to the full path to the official executable. Prefer the native
executable, not a shell script or `.cmd` wrapper.

```powershell
python antigravity_preview.py
```

Or double-click `scripts\run_antigravity_preview.cmd` from a terminal. You can pass
normal scanner options such as `--date 2026-10-26` or `--skip-enrichment`; do not
use `--compare` or OpenRouter model IDs. Select the model with `ANTIGRAVITY_MODEL`.
The managed output directory cannot be overridden.

Each preview starts with an authenticated CLI preflight, then discovers and
extracts real publications using the existing scanner pipeline. It uses a unique
folder under `reports/antigravity-preview/` for reports, duplicate history,
workflow state and enrichment cache. Production state is not read or updated.
Because previews have fresh history, previously delivered articles may reappear.
The normal 48-hour coverage window and evidence checks still apply.

Open the generated `report_YYYY-MM-DD.html`, `.pdf`, `.md` or `.json` under the
printed run directory. `run.json` records provider, requested model, CLI usage,
conversation ID, errors and elapsed time. Empty or failed reviews remain visible
in audit files; they are not proof that there are no relevant publications.

## Transport

Each request starts a fresh official `agy` process in an empty temporary working
directory, with `--sandbox` and the default permission controls. Prompts travel
as documented NDJSON on stdin, avoiding Windows command-line length limits and
shell expansion. The review schema is passed as a temporary file and validated
again locally. The scanner's existing article evidence validation also runs.
There is no fallback to OpenRouter and no automatic transport retry. Existing
analyser repair requests can still occur for invalid editorial decisions.

No OAuth tokens are read or copied. Existing API keys and the SMTP password are
removed from the child environment. The CLI still uses its normal sign-in and
user-level configuration; the empty workspace and sandbox are not a guarantee
against every tool action. Never enable global automatic tool approval. If your
installed CLI does not support the documented streaming/schema/sandbox flags,
update it rather than disabling permissions. Rate limits and supported consumer
subscription usage must be checked against Google's current guidance.

References:
- https://antigravity.google/docs/cli/headless/
- https://antigravity.google/docs/faq/

## Validation

```powershell
python -m unittest test_antigravity -v
python -m unittest discover -v
```

The offline integration test replaces only the CLI subprocess response and source
fetches. It exercises analysis, validation, JSON/HTML/Markdown/PDF rendering,
provider auditing and email suppression without a key. Its synthetic fixture
output does **not** establish the quality of real Antigravity summaries.
A live model test requires an installed, signed-in official CLI on your PC.
