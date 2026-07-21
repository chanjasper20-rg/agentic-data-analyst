# CLAUDE.md

Working notes for this repo. `README.md` explains what the app is and how to run it —
this file covers what is not obvious from reading the code.

## What this is

A Streamlit app that hands a spreadsheet to OpenAI's code interpreter and shows what
comes back. The model writes and runs real pandas in a sandboxed container; we upload
files, parse the response, pull the artifacts out, and render them.

Everything interesting lives in `core/session.py`. The rest is plumbing around it.

## Environment

Windows, PowerShell, `.venv` in the project root. Use the venv interpreter explicitly —
`.venv\Scripts\python.exe` — rather than a bare `python`, which may resolve elsewhere.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## Verify against the API, not through the browser

`scripts/smoke_test.py` runs the whole path — upload, ask, code execution, artifact
download — with no Streamlit involved, and fails with a specific reason.

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test.py --follow-up
```

Run it after any change to `core/session.py` or `core/files.py`. Debugging the API path
through the UI is slow and conflates two layers; this separates them. `--follow-up` also
checks that the second question reused the same container, which is what makes
conversational analysis work.

Each run costs real money (a few cents). Don't loop it.

## Things that will bite you

**`gpt-4` cannot run this app.** The literal model id `gpt-4` is the 2023 release and its
model card lists function calling as unsupported, so it cannot drive the code interpreter
tool at all. `gpt-4.1` is the current GPT-4 generation and is the default in
`core/config.py`. Changing `MODEL` there changes it everywhere.

**The sandbox expires after ~20 minutes idle.** `AnalysisSession.ask` catches that,
rebuilds the container, re-mounts the files, and retries exactly once. OpenAI does not
document a specific error class for a dead container, so `_mentions_container` matches on
the message text across any 4xx — if expiry recovery ever breaks silently, suspect that
matcher first.

**Artifacts are discovered two ways, and neither is guaranteed.** A file reaches the UI
only if it appears as a `code_interpreter_call` output file_id or as a
`container_file_citation` annotation on the message. A file the model writes to
`/mnt/data` and merely *names in prose* has neither, and will not appear as a download.

**Never render the model's reply text raw.** The code interpreter links its output files
as `sandbox:/mnt/data/report.xlsx`. Streamlit renders that as a live anchor with
`target="_blank"`, but no browser can resolve the `sandbox:` scheme, so clicking it
yields a dead or empty file — users click it, conclude downloads are broken, and never
find the real buttons below. `core/rendering.py` defuses these via `strip_sandbox_links()`
before `st.markdown`. Keep that in place; it is load-bearing UX, not cosmetic.

**Prompt changes are behaviour changes.** `core/prompts.py` is the reason answers lead
with the finding, charts get saved as PNGs at all, and exports become real files. Edit it
like code, and re-run the smoke test after.

## Conventions

- Docstrings explain *why*, not *what* — match that. Comments earn their place by
  recording a decision or a trap, not by narrating the next line.
- Type hints throughout, `from __future__ import annotations` at the top of every module.
- Failures the user should see get phrased for humans: raise `AnalysisError` with a
  sentence they can act on, not a stack trace. Failures they can do nothing about (one
  chart that would not download) are swallowed so they don't sink the whole answer.
- Never log, print, or display the API key. `core/openai_client.py` has `describe()` and
  `diagnose()` for this — they report where a key was found and its shape, never its value.

## Secrets

`.env` is gitignored and holds a real key. `.env.example` is the committed template.
Don't commit `.env`, don't paste the key into code, docs, or a commit message, and redact
it from any command output you surface.

Key resolution order is sidebar box → Streamlit secrets → `OPENAI_API_KEY` env var.
`.env` is loaded with `override=True` against a path anchored to the project root, so a
stale shell variable cannot shadow the file and the app can be launched from anywhere.

## Data uploaded here leaves the machine

Every uploaded file goes to OpenAI. This is stated in the sidebar and the README and
should stay stated — it is the main reason not to point this at anything confidential.

## Layout

| Path | Role |
| --- | --- |
| `app.py` | Streamlit UI: sidebar, chat loop, streaming callbacks |
| `core/session.py` | The turn loop — container reuse, expiry recovery, response parsing |
| `core/files.py` | Files API upload, artifact download, filename safety |
| `core/rendering.py` | `TurnResult` → Streamlit widgets |
| `core/prompts.py` | Analyst persona and canned prompts |
| `core/config.py` | Model, sandbox size, limits, pricing table |
| `core/openai_client.py` | Key resolution and client construction |
| `scripts/smoke_test.py` | UI-free end-to-end verification |
| `scripts/make_sample_data.py` | Solar dataset with four planted faults |
| `scripts/make_test_orders.py` | Second dataset for testing a different shape |
