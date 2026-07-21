"""Turning a TurnResult into Streamlit widgets."""

from __future__ import annotations

import re

import streamlit as st

from core.files import Artifact
from core.session import TurnResult

# The code interpreter saves its files inside the sandbox and then links to them
# as `sandbox:/mnt/data/report.xlsx`. Streamlit renders that as a real anchor,
# but the scheme means nothing to a browser: clicking it fails or saves an empty
# file. Users reach for those links -- they sit right in the prose, above the
# fold -- conclude the download is broken, and never scroll to the working
# buttons. So we defuse the links and keep only the filename they carried.
_SANDBOX_LINK = re.compile(r"\[([^\]]*)\]\(\s*(?:sandbox|attachment):[^)]*\)")
_BARE_SANDBOX = re.compile(r"(?:sandbox|attachment):(?://)?/?\S+")


def strip_sandbox_links(text: str) -> str:
    """Turn dead sandbox: links into plain filenames, leaving the prose intact."""
    text = _SANDBOX_LINK.sub(lambda match: match.group(1) or "the file below", text)
    return _BARE_SANDBOX.sub(lambda match: match.group(0).rsplit("/", 1)[-1], text)


def render_turn(result: TurnResult, key_prefix: str) -> None:
    """Draw one assistant reply: prose, then charts, then the working, then files."""
    if result.container_restarted:
        st.caption("The sandbox had expired, so the data was reloaded into a fresh one.")

    if result.text.strip():
        st.markdown(strip_sandbox_links(result.text))

    images = [item for item in result.artifacts if item.is_image]
    documents = [item for item in result.artifacts if not item.is_image]

    for index, image in enumerate(images):
        st.image(image.data, caption=image.name, use_container_width=True)

    if result.code_blocks or result.logs:
        with st.expander(f"Show the code ({len(result.code_blocks)} cell(s))"):
            for index, code in enumerate(result.code_blocks):
                st.code(code, language="python")
            if result.logs:
                st.caption("Output")
                st.code("\n".join(result.logs), language="text")

    if documents:
        st.caption("Files produced — click to download")
        columns = st.columns(min(len(documents), 3))
        for index, document in enumerate(documents):
            with columns[index % len(columns)]:
                _download_button(document, key=f"{key_prefix}-doc-{index}")

    if images:
        with st.expander("Download the charts"):
            for index, image in enumerate(images):
                _download_button(image, key=f"{key_prefix}-img-{index}")

    if result.error:
        st.warning(result.error)


def _download_button(artifact: Artifact, key: str) -> None:
    st.download_button(
        label=artifact.name,
        data=artifact.data,
        file_name=artifact.name,
        mime=artifact.mime_type,
        key=key,
        use_container_width=True,
    )
