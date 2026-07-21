"""Turning a TurnResult into Streamlit widgets."""

from __future__ import annotations

import streamlit as st

from core.files import Artifact
from core.session import TurnResult


def render_turn(result: TurnResult, key_prefix: str) -> None:
    """Draw one assistant reply: prose, then charts, then the working, then files."""
    if result.container_restarted:
        st.caption("The sandbox had expired, so the data was reloaded into a fresh one.")

    if result.text.strip():
        st.markdown(result.text)

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
        st.caption("Files produced")
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
