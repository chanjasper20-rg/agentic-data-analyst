"""The analysis loop: send a question, let the model run code, collect what came back.

One AnalysisSession corresponds to one conversation. It owns two pieces of
server-side state:

  * `last_response_id` -- chains turns together so the model remembers the
    conversation without us resending the transcript.
  * `container_id` -- the sandbox. Reusing it is what lets a follow-up question
    build on dataframes the previous answer already loaded.

Containers expire after a period of inactivity. When that happens we start a
fresh one, re-attach the same uploaded files, and retry once; the system prompt
tells the model to reload its data if its variables have vanished, so the user
never has to know it happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import openai
from openai import OpenAI

from core import config
from core.files import Artifact, UploadedDataFile, download_artifact
from core.prompts import SYSTEM_PROMPT


@dataclass
class TurnResult:
    """Everything the UI needs to render one exchange."""

    text: str = ""
    code_blocks: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    response_id: str | None = None
    container_restarted: bool = False
    error: str | None = None

    @property
    def cost_usd(self) -> float:
        return config.estimate_cost(config.MODEL, self.input_tokens, self.output_tokens)


class AnalysisError(RuntimeError):
    """A failure worth showing the user verbatim, already phrased for humans."""


class AnalysisSession:
    def __init__(self, client: OpenAI, model: str | None = None) -> None:
        self.client = client
        self.model = model or config.MODEL
        self.last_response_id: str | None = None
        self.container_id: str | None = None
        self.files: list[UploadedDataFile] = []
        self._files_in_container: set[str] = set()

    # ---------------------------------------------------------------- files

    def attach(self, uploaded: UploadedDataFile) -> None:
        """Register a file so the next turn mounts it into the sandbox."""
        if any(existing.file_id == uploaded.file_id for existing in self.files):
            return
        self.files.append(uploaded)

    @property
    def file_ids(self) -> list[str]:
        return [item.file_id for item in self.files]

    # ------------------------------------------------------------ the turn

    def ask(
        self,
        question: str,
        on_event: Callable[[str, str], None] | None = None,
    ) -> TurnResult:
        """Run one question to completion and return the rendered pieces.

        `on_event` receives (kind, payload) as things happen, where kind is one
        of "text" (a streamed delta), "code" (the model started running code),
        or "status". It lets the UI show progress; it is optional.
        """
        try:
            return self._ask_once(question, on_event)
        except _ContainerGone:
            # Sandbox expired. Rebuild it, re-mount the files, try once more.
            self.container_id = None
            self._files_in_container.clear()
            result = self._ask_once(question, on_event)
            result.container_restarted = True
            return result
        except openai.RateLimitError as exc:
            raise AnalysisError(
                "OpenAI is rate limiting this key right now. Wait a moment and ask again."
            ) from exc
        except openai.APIConnectionError as exc:
            raise AnalysisError(
                "Could not reach the OpenAI API. Check your network connection and retry."
            ) from exc
        except openai.APIStatusError as exc:
            raise AnalysisError(f"OpenAI returned an error ({exc.status_code}): {exc.message}") from exc

    def _ask_once(
        self,
        question: str,
        on_event: Callable[[str, str], None] | None,
    ) -> TurnResult:
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": SYSTEM_PROMPT,
            "input": question,
            "tools": [self._code_interpreter_tool()],
            "max_output_tokens": config.MAX_OUTPUT_TOKENS,
            "store": True,
        }
        if self.last_response_id:
            request["previous_response_id"] = self.last_response_id

        response = self._create_with_container_check(request, on_event)

        self.last_response_id = getattr(response, "id", None)
        self._remember_container(response)
        self._files_in_container.update(self.file_ids)

        return self._collect(response)

    def _create_with_container_check(
        self,
        request: dict[str, Any],
        on_event: Callable[[str, str], None] | None,
    ) -> Any:
        try:
            if on_event is None:
                return self.client.responses.create(**request)
            return self._create_streaming(request, on_event)
        except openai.APIStatusError as exc:
            # OpenAI does not document which error a dead sandbox raises, so we
            # match on the message across any 4xx rather than a specific class.
            if self.container_id and _mentions_container(str(exc)):
                raise _ContainerGone from exc
            raise

    def _create_streaming(self, request: dict[str, Any], on_event: Callable[[str, str], None]) -> Any:
        """Stream the reply so the UI can show text and code as they arrive."""
        final: Any = None
        with self.client.responses.stream(**request) as stream:
            for event in stream:
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    on_event("text", getattr(event, "delta", "") or "")
                elif kind == "response.code_interpreter_call_code.delta":
                    on_event("code", getattr(event, "delta", "") or "")
                elif kind == "response.code_interpreter_call.in_progress":
                    on_event("status", "running code")
                elif kind == "response.code_interpreter_call.completed":
                    on_event("status", "code finished")
            final = stream.get_final_response()
        return final

    def _code_interpreter_tool(self) -> dict[str, Any]:
        """Reuse the existing sandbox if we have one; otherwise ask for a new one."""
        if self.container_id:
            return {"type": "code_interpreter", "container": self.container_id}
        return {
            "type": "code_interpreter",
            "container": {
                "type": "auto",
                "file_ids": self.file_ids,
                # The 1 GB default is tight once pandas copies a frame; 4 GB
                # costs $0.12 per 20-minute session instead of $0.03.
                "memory_limit": config.SANDBOX_MEMORY,
            },
        }

    def _remember_container(self, response: Any) -> None:
        for item in _output_items(response):
            if getattr(item, "type", "") == "code_interpreter_call":
                found = getattr(item, "container_id", None)
                if found:
                    self.container_id = found
                    return

    # -------------------------------------------------------- reading output

    def _collect(self, response: Any) -> TurnResult:
        result = TurnResult(response_id=getattr(response, "id", None))

        usage = getattr(response, "usage", None)
        if usage is not None:
            result.input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            result.output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        # file_id -> (container_id, filename); the citation annotations carry
        # both, which saves us a metadata lookup per file.
        wanted: dict[str, tuple[str | None, str | None]] = {}

        for item in _output_items(response):
            item_type = getattr(item, "type", "")

            if item_type == "code_interpreter_call":
                code = getattr(item, "code", None)
                if code:
                    result.code_blocks.append(str(code))
                call_container = getattr(item, "container_id", None)
                for output in getattr(item, "outputs", None) or []:
                    logs = getattr(output, "logs", None)
                    if logs:
                        result.logs.append(str(logs))
                    file_id = getattr(output, "file_id", None)
                    if file_id:
                        wanted.setdefault(str(file_id), (call_container, None))

            elif item_type == "message":
                for block in getattr(item, "content", None) or []:
                    text = getattr(block, "text", None)
                    if text:
                        result.text += str(text)
                    for annotation in getattr(block, "annotations", None) or []:
                        if getattr(annotation, "type", "") != "container_file_citation":
                            continue
                        file_id = getattr(annotation, "file_id", None)
                        if file_id:
                            wanted[str(file_id)] = (
                                getattr(annotation, "container_id", None),
                                getattr(annotation, "filename", None),
                            )

        # `output_text` is the SDK's convenience join; prefer it when our own
        # walk came up empty (shapes differ slightly across model families).
        if not result.text:
            result.text = str(getattr(response, "output_text", "") or "")

        if getattr(response, "status", "") == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "")
            if reason == "max_output_tokens":
                result.error = (
                    "The reply hit the output limit and was cut short. "
                    "Try asking for one thing at a time."
                )

        result.artifacts = self._download_all(wanted)
        return result

    def _download_all(self, wanted: dict[str, tuple[str | None, str | None]]) -> list[Artifact]:
        artifacts: list[Artifact] = []
        for file_id, (container_id, filename) in wanted.items():
            container = container_id or self.container_id
            if not container:
                continue
            try:
                artifacts.append(download_artifact(self.client, container, file_id, filename))
            except Exception:
                # A chart we cannot fetch should not sink the whole answer.
                continue
        artifacts.sort(key=lambda item: (not item.is_image, item.name))
        return artifacts


class _ContainerGone(Exception):
    """Internal signal that the sandbox expired and we should rebuild it."""


def _mentions_container(message: str) -> bool:
    lowered = message.lower()
    return "container" in lowered and any(
        word in lowered for word in ("expired", "not found", "no longer", "deleted", "invalid")
    )


def _output_items(response: Any) -> list[Any]:
    return list(getattr(response, "output", None) or [])
