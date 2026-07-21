"""End-to-end check of the OpenAI path, with no Streamlit involved.

Uploads the sample dataset, asks one analytical question, waits for the model to
run code in the sandbox, and downloads whatever it produced. Run this after any
change to core/session.py and before deploying -- it fails loudly and specifically
rather than leaving you to debug through the UI.

    python scripts/smoke_test.py
    python scripts/smoke_test.py --question "Find anomalies in this data"
    python scripts/smoke_test.py --follow-up      # also exercises container reuse
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import config  # noqa: E402
from core.files import upload_data_file  # noqa: E402
from core.openai_client import MissingAPIKey, build_client  # noqa: E402
from core.session import AnalysisError, AnalysisSession  # noqa: E402

SAMPLE = PROJECT_ROOT / "data" / "sample_solar_generation.csv"
DEFAULT_QUESTION = (
    "Load this solar generation data, then show me total monthly generation "
    "across all sites as a line chart saved to a PNG. Tell me what the trend shows."
)
FOLLOW_UP = "Now break that same monthly total down by site on one chart."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--file", default=str(SAMPLE))
    parser.add_argument("--follow-up", action="store_true", help="ask a second question too")
    parser.add_argument("--model", default=config.MODEL)
    args = parser.parse_args()

    data_path = Path(args.file)
    if not data_path.exists():
        print(f"FAIL  Data file not found: {data_path}")
        print("      Run: python scripts/make_sample_data.py")
        return 1

    try:
        client = build_client()
    except MissingAPIKey as exc:
        print("FAIL  No API key configured.\n")
        print(exc)
        return 1

    print(f"Model      : {args.model}")
    print(f"Data file  : {data_path.name} ({data_path.stat().st_size / 1024:.0f} KB)")

    session = AnalysisSession(client, model=args.model)

    print("\n[1/3] Uploading to the Files API ...")
    with data_path.open("rb") as handle:
        uploaded = upload_data_file(client, data_path.name, handle, data_path.stat().st_size)
    session.attach(uploaded)
    print(f"      file_id = {uploaded.file_id}")

    print("\n[2/3] Asking the model (this runs code server-side, expect 30-90s) ...")
    print(f'      "{args.question}"')
    try:
        result = session.ask(args.question)
    except AnalysisError as exc:
        print(f"\nFAIL  {exc}")
        return 1

    ok = report(result, session, label="first turn")

    if args.follow_up and ok:
        print("\n[3/3] Follow-up question, reusing the same sandbox ...")
        print(f'      "{FOLLOW_UP}"')
        container_before = session.container_id
        try:
            follow = session.ask(FOLLOW_UP)
        except AnalysisError as exc:
            print(f"\nFAIL  {exc}")
            return 1
        ok = report(follow, session, label="follow-up") and ok
        if session.container_id == container_before:
            print("PASS  Sandbox was reused across turns (state persists).")
        else:
            print("WARN  Sandbox changed between turns; state would have been lost.")
    else:
        print("\n[3/3] Skipped follow-up (pass --follow-up to test container reuse).")

    print("\n" + ("PASS  Smoke test succeeded." if ok else "FAIL  Smoke test failed."))
    return 0 if ok else 1


def report(result, session, label: str) -> bool:
    """Print what came back and judge whether the round trip actually worked."""
    print(f"\n--- {label} ---")

    if result.error:
        print(f"WARN  {result.error}")

    text = result.text.strip()
    print(f"Text        : {len(text)} chars")
    if text:
        preview = text if len(text) <= 400 else text[:400] + " ..."
        print("\n" + "\n".join("  " + line for line in preview.splitlines()))

    print(f"\nCode blocks : {len(result.code_blocks)}")
    print(f"Log outputs : {len(result.logs)}")
    print(f"Container   : {session.container_id or 'none captured'}")
    print(f"Artifacts   : {len(result.artifacts)}")

    out_dir = PROJECT_ROOT / config.OUTPUT_DIR
    for artifact in result.artifacts:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / artifact.name
        target.write_bytes(artifact.data)
        kind = "image" if artifact.is_image else "file "
        print(f"  {kind} {artifact.name:<40} {len(artifact.data):>9,} bytes -> {target}")

    print(
        f"\nTokens      : {result.input_tokens:,} in / {result.output_tokens:,} out"
        f"  (about ${result.token_cost_usd:.4f})"
    )
    if result.sandbox_cost_usd:
        print(f"Sandbox     : ${result.sandbox_cost_usd:.2f} for this session")
    print(f"Turn cost   : about ${result.cost_usd:.4f}")

    problems = []
    if not text:
        problems.append("no text came back")
    if not result.code_blocks:
        problems.append("the model never ran any code")
    if not session.container_id:
        problems.append("no container id was captured, so follow-ups cannot reuse state")
    if not any(a.is_image for a in result.artifacts):
        problems.append("no chart image was produced or downloaded")

    for problem in problems:
        print(f"FAIL  {problem}")
    return not problems


if __name__ == "__main__":
    raise SystemExit(main())
