"""Tunable constants. Change the model here and the whole app follows."""

from __future__ import annotations

# The model that writes and runs the analysis code.
#
# On "GPT-4": the literal model id `gpt-4` is the original 2023 release. Its
# model card lists function calling as unsupported, so it cannot drive the code
# interpreter tool at all -- this app would not work on it. `gpt-4.1` is the
# current GPT-4 generation, supports the tool, and is what we default to.
#
# Other models that support code interpreter, if you want to experiment:
#   gpt-4.1         $2.00 / $8.00   per Mtok  -- current default
#   gpt-4o          $2.50 / $10.00
#   gpt-5.6-luna    $1.00 / $6.00             -- cheapest of the current flagship line
#   gpt-5.6-terra   $2.50 / $15.00            -- best quality/cost balance today
MODEL = "gpt-4.1"

# Sandbox RAM per session: "1g" (default, $0.03/20min), "4g" ($0.12),
# "16g" ($0.48), "64g" ($1.92). Anything doing real pandas work wants 4g.
SANDBOX_MEMORY = "4g"

# Ceiling on a single reply. Analyses that write a lot of code need headroom.
MAX_OUTPUT_TOKENS = 16000

# Safety net for the tool-call loop, in case a run never settles.
MAX_TOOL_ITERATIONS = 30

# Files API hard limit is far higher, but pandas in a small sandbox struggles
# well before that, so warn early.
FILE_SIZE_WARN_BYTES = 50 * 1024 * 1024
FILE_SIZE_MAX_BYTES = 200 * 1024 * 1024

# Extensions we accept in the uploader.
ALLOWED_EXTENSIONS = ["csv", "xlsx", "xls", "json", "tsv", "txt", "parquet"]

# Rough pricing (USD per million tokens) for the sidebar cost meter. Update if
# OpenAI changes their rate card -- this is display-only, never billing truth.
PRICE_PER_MTOK = {
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-5.6-luna": {"input": 1.00, "output": 6.00},
    "gpt-5.6-terra": {"input": 2.50, "output": 15.00},
    "gpt-5.6-sol": {"input": 5.00, "output": 30.00},
}
DEFAULT_PRICE = {"input": 2.00, "output": 8.00}

# Where downloaded artifacts are written when running the smoke test.
OUTPUT_DIR = "outputs"


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Approximate USD cost of a turn, for display in the sidebar."""
    price = PRICE_PER_MTOK.get(model, DEFAULT_PRICE)
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000
