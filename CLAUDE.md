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

A bare `streamlit run app.py` fails with "not recognized" unless the venv is activated in
that shell — Streamlit is installed only inside `.venv`, never globally. The failure
happens before any project code loads, so it looks like a broken app rather than a
broken PATH. The explicit-interpreter form above always works; `.\.venv\Scripts\Activate.ps1`
first is the alternative, per shell session.

**Restart the app after editing anything under `core/`.** Streamlit's auto-reload
re-runs `app.py` but keeps already-imported modules cached, so a new function in
`core/config.py` is invisible to the running process and the page dies with
`AttributeError: module 'core.config' has no attribute ...`. The code is fine; the
process is stale. Nothing in the traceback says so, and re-reading the source will not
help — kill the process and start it again.

## Verify against the API, not through the browser

`scripts/smoke_test.py` runs the whole path — upload, ask, code execution, artifact
download — with no Streamlit involved, and fails with a specific reason.

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test.py --follow-up
```

Run it after any change to `core/session.py`, `core/files.py`, or `core/prompts.py` —
the prompt is in that list because whether the model finishes its work is a prompt
property, and this test is the only thing that checks it. Debugging the API path
through the UI is slow and conflates two layers; this separates them. `--follow-up` also
checks that the second question reused the same container, which is what makes
conversational analysis work.

Each run costs real money — about **$0.13**, of which $0.12 is the sandbox session fee
and under two cents is tokens. Don't loop it.

The pure helpers are covered by free unit tests — no key, no network — and those are the
ones to reach for first:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

`tests/test_pure.py` pins `_mentions_container` and `strip_sandbox_links` in particular.
Both fail *silently* if they regress — expiry recovery quietly stops happening, dead
download links quietly come back — and the smoke test is too coarse to catch either.

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

**A container's file list is fixed when it is created.** Passing `file_ids` in the tool
spec only does anything on the turn that builds the container; after that the field is
ignored, and a file uploaded mid-conversation stays invisible to the sandbox no matter
how many turns pass. The sidebar shows it as uploaded, the Files API has it, and the
model insists it does not exist. `AnalysisSession._mount_new_files` closes this by
diffing `file_ids` against `_files_in_container` and calling
`containers.files.create` for the difference. It records each file as mounted
individually, so a failure part-way through does not re-mount what already succeeded.

**The model will announce work and then stop.** Left to itself it replies "next, I'll
aggregate and plot..." and ends the turn — you get a paragraph and no chart, and the
smoke test fails on `no chart image was produced`. Nothing in `_ask_once` continues a
response that quit early; there is one API call per question. What prevents it is
prompt rules 1, 2 and 6 in `core/prompts.py`, which say that inspection is not a
checkpoint, that error recovery happens inside the turn, and that a reply is the end of
the task rather than a progress update. Rule 2 matters most on follow-ups, where the
model hits a `KeyError`, narrates the fix, and hands back. If this regresses, suspect
those three before anything in `session.py`.

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

**The sandbox fee, not tokens, is most of a short session's cost.** The container is
billed per 20-minute session by tier — $0.12 at 4g — while a typical turn is under a
cent of tokens. A smoke test run bills about $0.13, of which $0.12 is the container. So
`TurnResult.cost_usd` is `token_cost_usd + sandbox_cost_usd`, with the fee attributed to
the turn where `container_started` is true (the first turn, and any expiry rebuild).
That makes a single turn's figure lumpy but the session total right. Keep the two
components separate in anything user-facing; a token-only meter reads low by multiples
and that is what it used to do.

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
| `core/config.py` | Model, sandbox size, limits, token and sandbox pricing |
| `core/openai_client.py` | Key resolution and client construction |
| `scripts/smoke_test.py` | UI-free end-to-end verification (costs money) |
| `tests/test_pure.py` | Unit tests for the pure helpers (free, no network) |
| `scripts/make_sample_data.py` | Solar dataset with four planted faults |
| `scripts/make_test_orders.py` | Second dataset for testing a different shape |
