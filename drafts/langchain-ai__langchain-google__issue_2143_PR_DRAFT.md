# PR Draft: `fix(vertexai): filter out OpenAI Responses function_call and foreign reasoning blocks in Anthropic formatter (#2143)`

- **Issue:** [langchain-ai/langchain-google#2143](https://github.com/langchain-ai/langchain-google/issues/2143)
- **Repository:** `langchain-ai/langchain-google`
- **Local Branch:** `fix/issue-2143` (commit `a2630ec`)

## Root Cause

In [`libs/vertexai/langchain_google_vertexai/_anthropic_utils.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/langchain-google/libs/vertexai/langchain_google_vertexai/_anthropic_utils.py#L178-L280), `_format_message_anthropic` fell through to `content.append(block)` for unrecognized dict content block types and unconditionally converted any `block["type"] == "reasoning"` into an Anthropic `{"type": "thinking", ...}` block.

When a conversation started with `ChatOpenAI(use_responses_api=True)` is passed to `ChatAnthropicVertex`, the assistant `AIMessage` carries `response_metadata={"model_provider": "openai"}`, `content=[{"type": "function_call", ...}]` (and optionally OpenAI `"reasoning"` blocks), and `tool_calls=[...]`. `_format_message_anthropic` turned `tool_calls` into an Anthropic `tool_use` block while also forwarding the raw `function_call` block (or converting foreign `reasoning` blocks into invalid `thinking` blocks), causing the Anthropic Messages API on Vertex to reject the payload with HTTP 400.

Unlike `langchain-anthropic` (fixed in `langchain-ai/langchain#35417`), `ChatAnthropicVertex` does not yet populate `response_metadata["model_provider"] = "anthropic"` on its own `AIMessage` outputs (tracked in `#1756`). To avoid breaking `ChatAnthropicVertex`'s own v1 `reasoning` → `thinking` conversion when `model_provider` is unset, `function_call` is filtered whenever `model_provider != "anthropic"` and `reasoning` is filtered whenever the message is not an `AIMessage` or `model_provider not in (None, "anthropic")`.

## Diff Summary

- **Updated** [`libs/vertexai/langchain_google_vertexai/_anthropic_utils.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/langchain-google/libs/vertexai/langchain_google_vertexai/_anthropic_utils.py#L183-L195):
  - Inspected `message.response_metadata.get("model_provider")` when `isinstance(message, AIMessage)`.
  - Skipped `function_call` content blocks unless `model_provider == "anthropic"`.
  - Skipped `reasoning` content blocks when the message is not an `AIMessage` or `model_provider not in (None, "anthropic")`, preserving `ChatAnthropicVertex`'s own v1 `reasoning` → `thinking` conversion when `model_provider` is `None` or `"anthropic"`.
- **Updated** [`libs/vertexai/tests/unit_tests/test_anthropic_utils.py`](file:///Users/ankitkumarmishra/Downloads/Github/repos/langchain-google/libs/vertexai/tests/unit_tests/test_anthropic_utils.py#L1864-L1960):
  - Added `test_format_messages_anthropic_filters_openai_responses_function_call` reproducing the exact OpenAI Responses → `ChatAnthropicVertex` handoff from `#2143`.
  - Added `test_format_messages_anthropic_filters_non_anthropic_blocks` (parametrized over `reasoning` and `function_call`, covering OpenAI `AIMessage`, `HumanMessage`, and `AIMessage` without `model_provider` for `function_call`).
  - Added `test_format_messages_anthropic_preserves_own_and_anthropic_reasoning_blocks` verifying v1 `reasoning` → `thinking` conversion still works for both unset `model_provider` and `model_provider == "anthropic"`.

## Verification

| Check | Command | Result |
|---|---|---|
| Baseline `test_anthropic_utils.py` (before edits) | `cd libs/vertexai && ../.venv/bin/pytest tests/unit_tests/test_anthropic_utils.py` | 74 passed |
| Reproducing TDD tests (before fix) | `cd libs/vertexai && ../.venv/bin/pytest tests/unit_tests/test_anthropic_utils.py` | 3 failed (`['function_call', 'tool_use'] != ['tool_use']`, `thinking` kept for OpenAI `reasoning`, raw `function_call` kept), 76 passed |
| Targeted unit tests (after fix & format) | `cd libs/vertexai && ../.venv/bin/pytest tests/unit_tests/test_anthropic_utils.py tests/unit_tests/test_chat_models.py` | **227 passed**, 1 skipped (`Pydantic v1 compatibility not supported on Python 3.14` in `test_chat_models.py:2300`) |
| Ruff lint & format check (after fix) | `cd libs/vertexai && ../.venv/bin/ruff check --config pyproject.toml langchain_google_vertexai/_anthropic_utils.py tests/unit_tests/test_anthropic_utils.py && ../.venv/bin/ruff format --check --config pyproject.toml langchain_google_vertexai/_anthropic_utils.py tests/unit_tests/test_anthropic_utils.py` | All checks passed (`0` errors, `2 files already formatted`) |

## Push & Open PR Instructions

```bash
cd /Users/ankitkumarmishra/Downloads/Github/repos/langchain-google
git push -u origin fix/issue-2143
gh pr create --repo langchain-ai/langchain-google \
  --title "fix(vertexai): filter out OpenAI Responses function_call and foreign reasoning blocks in Anthropic formatter (#2143)" \
  --body-file /Users/ankitkumarmishra/Downloads/Github/drafts/langchain-ai__langchain-google__issue_2143_PR_DRAFT.md
```
