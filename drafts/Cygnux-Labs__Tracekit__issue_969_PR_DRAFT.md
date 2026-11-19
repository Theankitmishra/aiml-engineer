# PR Draft: OpenAI Agents and Claude Agent SDK adapters: pick the fail mode by the tool's class while the signer is down

**Issue:** https://github.com/Cygnux-Labs/Tracekit/issues/969  
**Branch:** `fix/issue-969` (local commit `b7c0fa3e`, signed off with DCO `Signed-off-by`)

## Root Cause & Motivation
- When the signer is unreachable during `decide`, `fail_open(fail_modes, tool_class)` selects the fail mode for the tool's class in `fail_modes`, falling back to `default` (closed unless set).
- `tracekit/integrations/openai_agents.py` (`TracekitAgents._gate`, line 136) and `tracekit/integrations/claude_agent_sdk.py` (`tracekit_hooks.pre`, line 128) called `fail_open(...)` with no `tool_class` argument, causing `default` to decide every tool call. A run with `fail_modes={"default": "closed", "shell": "open"}` therefore refused `shell`-class tools (`ShellTool` / `shell` / `Bash`) under those adapters even though LangChain and `RunHandle.decide` (used by CrewAI, Hermes Agent, etc.) allowed them.
- `docs/limits.md:78` also still listed OpenAI Agents, Claude Agent SDK, and CrewAI as applying `default` to every tool.

## Summary of Changes
- **`tracekit/integrations/openai_agents.py`**:
  - Imported `_class_of` from `tracekit.sdk.client`.
  - Updated `TracekitAgents._gate` to call `fail_open(self.fail_modes, _class_of(tool))`.
  - Updated the module docstring to document per-class fail-open behavior under `fail_modes`.
- **`tracekit/integrations/claude_agent_sdk.py`**:
  - Imported `_class_of` from `tracekit.sdk.client`.
  - Updated `tracekit_hooks` (`pre`) to call `fail_open(rpc.fail_modes, _class_of(tool))`.
  - Updated the module docstring to document per-class fail-open behavior under `fail_modes`.
- **`tests/test_contract_openai_agents.py`**:
  - Added `TestOnRealSigner.test_signer_unreachable_follows_the_fail_mode_of_the_tools_class` verifying that with `fail_modes={"default": "closed", "shell": "open"}` and an unreachable signer, `ShellTool` (`shell`) runs unrecorded while `echo` (`unknown`) is refused (`signer unavailable`).
- **`tests/test_contract_claude_agent_sdk.py`**:
  - Added `TestOnRealSigner.test_signer_unreachable_follows_the_fail_mode_of_the_tools_class` verifying that with `fail_modes={"default": "closed", "shell": "open"}` and an unreachable signer, `Bash` (`shell`) runs unrecorded while `echo` (`unknown`) is refused (`signer unavailable`).
- **`docs/limits.md`**:
  - Updated row 78 (and its "What to do" column) to reflect that OpenAI Agents, Claude Agent SDK, and CrewAI pick the tool's fail mode by its default-policy class alongside LangChain, Hermes Agent, and the Claude Code hook.
  - Checked `docs/integrations/openai-agents.md` and `docs/integrations/claude-agent-sdk.md`; neither "Not covered" section contained the outdated claim.

## Verification
- Reproduce-first (before fix):
  - `.venv/bin/python -m pytest tests/test_contract_openai_agents.py tests/test_contract_claude_agent_sdk.py -k fail_mode` -> **2 failed, 2 passed** (both new class-based fail-mode tests failed with `Tool call blocked by policy: signer unavailable...`).
- After fix:
  - `.venv/bin/python -m pytest tests/test_contract_openai_agents.py tests/test_contract_claude_agent_sdk.py -k fail_mode` -> **4 passed** (`103 deselected`).
  - `.venv/bin/python -m pytest tests/test_contract_openai_agents.py tests/test_contract_claude_agent_sdk.py tests/test_docs.py tests/test_eval_docs.py` -> **94 passed, 26 skipped** (standard contract `SKIP` cases).
  - `.venv/bin/python -m pytest tests/test_sdk_client.py tests/test_crewai.py` -> **72 passed**.
  - `.venv/bin/python -m pytest tests/test_integration_langchain.py -k fail_mode` -> **2 passed** (`43 deselected`).
  - `.venv/bin/python -m pyflakes tracekit tracekit_sdk.py tests eval examples contrib integrations` -> **0 errors/warnings** (exit code 0).

## Push Instructions
Fork `Cygnux-Labs/Tracekit` (or add your fork remote), push the signed-off branch, and open a PR targeting `Cygnux-Labs/Tracekit:main` referencing `Closes #969`:
```bash
cd /Users/ankitkumarmishra/Downloads/Github/repos/Tracekit
git remote add fork git@github.com:<your-user>/Tracekit.git
git push -u fork fix/issue-969
```
