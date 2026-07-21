"""Agentic Data Analyst -- ask questions about a spreadsheet in plain English.

Files go to the OpenAI Files API, the model writes and runs pandas/matplotlib in
a sandboxed container, and the charts and reports it produces come back here.

Run:  streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from core import config
from core.files import upload_data_file
from core.openai_client import MissingAPIKey, get_client
from core.prompts import REPORT_PROMPT, STARTER_QUESTIONS
from core.rendering import render_turn
from core.session import AnalysisError, AnalysisSession, TurnResult

SAMPLE_PATH = Path(__file__).parent / "data" / "sample_solar_generation.csv"

st.set_page_config(
    page_title="Agentic Data Analyst",
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------- state

def init_state() -> None:
    defaults = {
        "history": [],          # list of {"role": ..., "text"/"result": ...}
        "session": None,        # AnalysisSession
        "known_uploads": set(),  # Streamlit file ids we have already sent
        "pending": None,        # question queued by a button click
        "spend": 0.0,
        "tokens_in": 0,
        "tokens_out": 0,
        "api_key_override": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def reset_conversation() -> None:
    st.session_state.history = []
    st.session_state.session = None
    st.session_state.known_uploads = set()
    st.session_state.pending = None
    st.session_state.spend = 0.0
    st.session_state.tokens_in = 0
    st.session_state.tokens_out = 0
    st.session_state.pop("setup_help", None)


def get_session() -> AnalysisSession | None:
    """Build the analysis session lazily so a missing key is a message, not a crash."""
    if st.session_state.session is not None:
        return st.session_state.session
    try:
        client = get_client(st.session_state.api_key_override or None)
    except MissingAPIKey as exc:
        st.session_state.setup_help = str(exc)
        return None
    # The key works now, so drop any earlier complaint about it -- otherwise the
    # setup message blocks the page for the rest of the browser session.
    st.session_state.pop("setup_help", None)
    st.session_state.session = AnalysisSession(client)
    return st.session_state.session


# ------------------------------------------------------------- sidebar

def render_sidebar() -> None:
    with st.sidebar:
        st.subheader("Data")

        uploads = st.file_uploader(
            "Upload spreadsheets",
            type=config.ALLOWED_EXTENSIONS,
            accept_multiple_files=True,
            help="CSV, Excel, JSON, Parquet. Everything you upload is sent to OpenAI.",
        )
        if uploads:
            ingest(uploads)

        session = st.session_state.session
        if session and session.files:
            for item in session.files:
                st.caption(f"✓ {item.name}  ·  {item.size_bytes / 1024:,.0f} KB")
        elif SAMPLE_PATH.exists():
            if st.button("Try the sample dataset", use_container_width=True):
                load_sample()

        st.divider()
        st.subheader("Session")

        if st.session_state.tokens_in or st.session_state.tokens_out:
            st.metric("Estimated spend", f"${st.session_state.spend:.3f}")
            st.caption(
                f"{st.session_state.tokens_in:,} tokens in · "
                f"{st.session_state.tokens_out:,} out · model {config.MODEL}"
            )
        else:
            st.caption("No questions asked yet.")

        if st.button("Start a new analysis", use_container_width=True):
            reset_conversation()
            st.rerun()

        with st.expander("API key"):
            st.session_state.api_key_override = st.text_input(
                "Use a different key for this session",
                value=st.session_state.api_key_override,
                type="password",
                help="Leave blank to use OPENAI_API_KEY from the environment or Streamlit secrets.",
            )

        st.divider()
        st.caption(
            f"Sandbox: {config.SANDBOX_MEMORY} RAM, expires after 20 minutes idle. "
            "Data is uploaded to OpenAI for analysis."
        )


def ingest(uploads) -> None:
    """Send any newly-selected files to the Files API exactly once."""
    session = get_session()
    if session is None:
        return

    for upload in uploads:
        if upload.file_id in st.session_state.known_uploads:
            continue
        if upload.size > config.FILE_SIZE_MAX_BYTES:
            st.error(f"{upload.name} is too large ({upload.size / 1e6:.0f} MB). Limit is 200 MB.")
            continue
        if upload.size > config.FILE_SIZE_WARN_BYTES:
            st.warning(
                f"{upload.name} is {upload.size / 1e6:.0f} MB. Large files can exhaust the "
                "sandbox memory; consider aggregating first."
            )
        with st.spinner(f"Uploading {upload.name} ..."):
            try:
                session.attach(upload_data_file(session.client, upload.name, upload, upload.size))
                st.session_state.known_uploads.add(upload.file_id)
            except Exception as exc:
                st.error(f"Could not upload {upload.name}: {exc}")


def load_sample() -> None:
    session = get_session()
    if session is None:
        return
    with st.spinner("Uploading the sample dataset ..."):
        try:
            with SAMPLE_PATH.open("rb") as handle:
                session.attach(
                    upload_data_file(
                        session.client, SAMPLE_PATH.name, handle, SAMPLE_PATH.stat().st_size
                    )
                )
            st.rerun()
        except Exception as exc:
            st.error(f"Could not upload the sample dataset: {exc}")


# ------------------------------------------------------------ main body

def render_history() -> None:
    for index, entry in enumerate(st.session_state.history):
        with st.chat_message(entry["role"]):
            if entry["role"] == "user":
                st.markdown(entry["text"])
            else:
                render_turn(entry["result"], key_prefix=f"turn{index}")


def render_welcome() -> None:
    st.markdown(
        "#### Ask questions about a spreadsheet\n"
        "Upload a file in the sidebar, then ask in plain English. The model writes "
        "and runs Python against your data and answers with charts and findings."
    )
    if SAMPLE_PATH.exists():
        st.caption(
            "No data handy? Use **Try the sample dataset** in the sidebar — two years of "
            "solar generation across five sites, with a few faults hidden in it."
        )


def render_suggestions() -> None:
    st.caption("Try asking:")
    columns = st.columns(2)
    for index, question in enumerate(STARTER_QUESTIONS):
        with columns[index % 2]:
            if st.button(question, key=f"starter{index}", use_container_width=True):
                st.session_state.pending = question
                st.rerun()


def run_question(question: str) -> None:
    """Send one question, streaming the reply into the page as it arrives."""
    session = get_session()
    if session is None:
        return

    st.session_state.history.append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        text_area = st.empty()
        status = st.status("Thinking ...", expanded=False)
        buffer: list[str] = []

        def on_event(kind: str, payload: str) -> None:
            if kind == "text":
                buffer.append(payload)
                text_area.markdown("".join(buffer))
            elif kind == "status":
                status.update(label=payload.capitalize() + " ...")
            elif kind == "code":
                status.update(label="Running code ...")

        try:
            result = session.ask(question, on_event=on_event)
        except AnalysisError as exc:
            status.update(label="Failed", state="error")
            st.error(str(exc))
            st.session_state.history.pop()  # don't strand a question with no reply
            return
        except Exception as exc:
            status.update(label="Failed", state="error")
            st.error(f"Something went wrong: {exc}")
            st.session_state.history.pop()
            return

        status.update(label="Done", state="complete")
        text_area.empty()  # render_turn redraws the text alongside charts
        render_turn(result, key_prefix=f"live{len(st.session_state.history)}")

    record(result)


def record(result: TurnResult) -> None:
    st.session_state.history.append({"role": "assistant", "result": result})
    st.session_state.tokens_in += result.input_tokens
    st.session_state.tokens_out += result.output_tokens
    st.session_state.spend += result.cost_usd


def main() -> None:
    init_state()

    st.title("Agentic Data Analyst")
    render_sidebar()

    session = st.session_state.session
    if session is None and "setup_help" in st.session_state:
        session = get_session()  # the key may have been fixed since we last looked

    has_data = bool(session and session.files)

    if "setup_help" in st.session_state and session is None:
        st.info(st.session_state.setup_help)
        return

    if not st.session_state.history:
        render_welcome()
        if has_data:
            render_suggestions()

    render_history()

    if has_data and st.session_state.history:
        if st.button("Generate a report package", use_container_width=True):
            st.session_state.pending = REPORT_PROMPT
            st.rerun()

    question = st.chat_input(
        "Ask about your data ..." if has_data else "Upload a file first",
        disabled=not has_data,
    )

    pending = st.session_state.pending
    if pending:
        st.session_state.pending = None

    asked = question or pending
    if asked:
        run_question(asked)
        st.rerun()


if __name__ == "__main__":
    main()
