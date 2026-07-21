"""Moving files between the user's machine, the OpenAI Files API, and the sandbox."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from openai import OpenAI

MIME_BY_EXTENSION = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".json": "application/json",
    ".parquet": "application/octet-stream",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Extensions we render inline as charts rather than offering as a download.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@dataclass(frozen=True)
class UploadedDataFile:
    """A user file that now lives in the OpenAI Files API."""

    file_id: str
    name: str
    size_bytes: int


@dataclass(frozen=True)
class Artifact:
    """A file the model produced inside the sandbox and we pulled back out."""

    file_id: str
    name: str
    data: bytes

    @property
    def is_image(self) -> bool:
        return Path(self.name).suffix.lower() in IMAGE_EXTENSIONS

    @property
    def mime_type(self) -> str:
        return guess_mime(self.name)


def guess_mime(filename: str) -> str:
    return MIME_BY_EXTENSION.get(Path(filename).suffix.lower(), "application/octet-stream")


def safe_filename(raw: str, fallback: str = "download") -> str:
    """Strip any directory component so a model-chosen name can't escape a folder."""
    name = os.path.basename((raw or "").strip().replace("\\", "/"))
    if not name or name in {".", ".."}:
        return fallback
    return name


def upload_data_file(client: OpenAI, name: str, stream: BinaryIO, size_bytes: int) -> UploadedDataFile:
    """Push one user file to the Files API so a sandbox container can mount it."""
    filename = safe_filename(name, fallback="upload.csv")
    mime = guess_mime(filename)

    # "user_data" is the documented general-purpose value; "assistants" is the
    # older one that container file_ids have historically wanted. Try the
    # general one first and fall back rather than guessing wrong.
    last_error: Exception | None = None
    for purpose in ("user_data", "assistants"):
        stream.seek(0)
        try:
            result = client.files.create(file=(filename, stream, mime), purpose=purpose)
            return UploadedDataFile(file_id=result.id, name=filename, size_bytes=size_bytes)
        except Exception as exc:  # narrow retry: only the purpose differs
            last_error = exc

    raise RuntimeError(f"Could not upload {filename}: {last_error}") from last_error


def download_artifact(client: OpenAI, container_id: str, file_id: str, name: str | None = None) -> Artifact:
    """Fetch the bytes of a file the model wrote inside the sandbox container."""
    filename = safe_filename(name or "", fallback=f"{file_id}.bin")

    if not name:
        # Ask the container what it called the file, so downloads keep a
        # meaningful name and extension.
        try:
            meta = client.containers.files.retrieve(file_id, container_id=container_id)
            candidate = getattr(meta, "path", None) or getattr(meta, "filename", None)
            if candidate:
                filename = safe_filename(str(candidate), fallback=filename)
        except Exception:
            pass

    content = client.containers.files.content.retrieve(file_id, container_id=container_id)
    data = _read_binary(content)
    return Artifact(file_id=file_id, name=filename, data=data)


def _read_binary(response: object) -> bytes:
    """Normalise the several shapes the SDK may hand back for file content."""
    if isinstance(response, (bytes, bytearray)):
        return bytes(response)
    for attribute in ("content", "text"):
        value = getattr(response, attribute, None)
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
    reader = getattr(response, "read", None)
    if callable(reader):
        return bytes(reader())
    raise TypeError(f"Could not read binary content from {type(response)!r}")
