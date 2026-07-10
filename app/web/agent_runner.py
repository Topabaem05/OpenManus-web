#!/usr/bin/env python3
"""Agent runner for Web-Manus.

Runs inside a microsandbox VM. Wraps the OpenManus Manus agent with
structured JSON event emission to stdout for the FastAPI backend to capture.

Communication protocol:
- Input:  reads JSON from /tmp/webmanus_input.json (written by backend)
- Output: writes JSON events to stdout with "WEBMANUS_EVENT:" prefix
- Events: agent_start, agent_ready, thought, tool_call, tool_result,
          final_answer, message_complete, error
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Add project root to Python path so we can import app.* modules
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Inside the VM, code is extracted to / so app/ is at /app/
# On the host, project root is the parent of app/
if (_PROJECT_ROOT / "__init__.py").exists():
    # We're inside VM: app/ is at _PROJECT_ROOT, so add parent of app/ to path
    sys.path.insert(0, str(_PROJECT_ROOT.parent))
else:
    # We're on host: add project root to path
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.agent.manus import Manus
from app.agent.toolcall import ToolCallAgent
from app.llm import LLM
from app.schema import Message, ToolCall
from app.tool.base import ToolResult

EVENT_MARKER = "WEBMANUS_EVENT:"
INPUT_FILE = "/tmp/webmanus_input.json"
STATE_FILE = "/tmp/webmanus_state.json"
DEFAULT_STEP_TIMEOUT_SECONDS = 120


_VISUAL_BROWSER_ACTIONS = frozenset(
    {
        "go_to_url",
        "click_element",
        "input_text",
        "scroll_down",
        "scroll_up",
        "scroll_to_text",
        "send_keys",
        "get_dropdown_options",
        "select_dropdown_option",
        "open_tab",
        "close_tab",
        "extract_content",
    }
)


async def emit_event(event: Dict[str, Any]):
    line = f"{EVENT_MARKER}{json.dumps(event, ensure_ascii=False)}"
    print(line, flush=True)


async def update_state(status: str, step: int = 0):
    state = {"status": status, "step": step, "pid": os.getpid()}
    Path(STATE_FILE).write_text(json.dumps(state))


async def emit_agent_capabilities(agent: EventEmittingManus):
    tool_calls_enabled = bool(getattr(agent.llm, "tool_calls_enabled", True))
    tool_names = sorted(getattr(agent.available_tools, "tool_map", {}).keys())
    await emit_event(
        {
            "type": "agent_capabilities",
            "tools_available": tool_calls_enabled and bool(tool_names),
            "tool_calls_enabled": tool_calls_enabled,
            "tools": tool_names,
        }
    )


async def emit_browser_state(agent: EventEmittingManus, step: int):
    browser_tool = agent.available_tools.get_tool("browser_use")
    if not browser_tool or not hasattr(browser_tool, "get_current_state"):
        return

    try:
        state_result = await browser_tool.get_current_state()
    except Exception as exc:
        await emit_event(
            {
                "type": "browser_state",
                "step": step,
                "error": f"Failed to capture browser state: {exc}",
            }
        )
        return

    if not state_result:
        return

    state_payload: Dict[str, Any] = {}
    output = getattr(state_result, "output", None)
    if output:
        try:
            state_payload.update(json.loads(output))
        except json.JSONDecodeError:
            state_payload["content"] = str(output)[:2000]

    screenshot = getattr(state_result, "base64_image", None)
    if screenshot:
        state_payload["screenshot"] = screenshot

    if not state_payload:
        return

    state_payload.update({"type": "browser_state", "step": step})
    await emit_event(state_payload)


def latest_assistant_content(agent: EventEmittingManus) -> str:
    for msg in reversed(agent.memory.messages):
        if msg.role == "assistant" and msg.content and not msg.tool_calls:
            return msg.content
    return ""


def step_signature(agent: EventEmittingManus) -> str:
    if not agent.memory.messages:
        return ""
    last_msg = agent.memory.messages[-1]
    tool_calls = []
    if last_msg.tool_calls:
        tool_calls = [
            {
                "name": call.function.name,
                "arguments": call.function.arguments,
            }
            for call in last_msg.tool_calls
        ]
    return json.dumps(
        {
            "role": last_msg.role,
            "content": last_msg.content,
            "tool_calls": tool_calls,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


async def read_input_message() -> Optional[Dict[str, Any]]:
    input_path = Path(INPUT_FILE)
    if not input_path.exists():
        return None
    try:
        content = input_path.read_text()
        if not content.strip():
            return None
        msg_data = json.loads(content)
        input_path.unlink()
        await emit_event(
            {
                "type": "message_received",
                "message_length": len(msg_data.get("message", "")),
            }
        )
        return msg_data
    except json.JSONDecodeError:
        return None
    except IOError as e:
        await emit_event({"type": "error", "message": f"Failed to read input: {e}"})
        return None



def _extract_created_files(tool_name: str, tool_input: dict, output_text: str) -> list[str]:
    """Best-effort extraction of file paths created by str_replace_editor or python_execute."""
    paths: list[str] = []
    if tool_name == "str_replace_editor":
        path = tool_input.get("path")
        if path and isinstance(path, str):
            paths.append(path)
    elif tool_name == "document_generation":
        path = tool_input.get("output_path")
        if path and isinstance(path, str):
            paths.append(path)
    elif tool_name == "python_execute":
        # Look for /workspace, /tmp, or any absolute path in output
        for candidate in re.findall(r"(/(?:workspace|tmp)/[A-Za-z0-9_./+\-]+)", output_text):
            # Trim common trailing punctuation
            candidate = candidate.strip('.;:!?"')
            if candidate not in paths:
                paths.append(candidate)
    return paths




_PLAN_REVISION = 0


def _parse_plan_steps(text: str) -> list[dict[str, str]]:
    """Extract numbered steps from a plan text."""
    steps = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(?:\d+[.\)\-\s]+)?(.*?)$", line)
        if m:
            title = m.group(1).strip()
            if title:
                steps.append({"id": f"step_{len(steps) + 1}", "title": title, "status": "pending"})
    return steps


async def _generate_plan(agent: EventEmittingManus, message: str) -> dict:
    """Ask the LLM for a concise todo plan."""
    global _PLAN_REVISION
    _PLAN_REVISION += 1
    prompt = (
        "You are a task planner. Given the user request below, produce 2-6 concise numbered steps "
        "that an AI agent should execute to complete the task. Be specific. "
        "Return ONLY a numbered list, nothing else.\n\n"
        f"Request: {message}"
    )
    try:
        response = await agent.llm.ask(
            [{"role": "user", "content": prompt}],
            system_msgs=[{"role": "system", "content": "You are a concise task planner."}],
            stream=False,
            temperature=0.2,
        )
    except Exception as exc:
        response = f"1. Plan generation failed ({exc}); proceed anyway"
    steps = _parse_plan_steps(response)
    if not steps:
        steps = [{"id": "step_1", "title": message.strip()[:80], "status": "pending"}]
    return {"revision": f"rev_{_PLAN_REVISION}", "steps": steps}


async def _wait_for_plan_response(timeout: float = 1800.0) -> dict:
    """Poll input file until it contains a plan_response message."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        msg = await read_input_message()
        if msg:
            raw = msg.get("message", msg)
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    raw = {}
            if isinstance(raw, dict) and raw.get("message_type") == "plan_response":
                return raw
        await asyncio.sleep(0.2)
    return {"message_type": "plan_response", "action": "approve"}


async def _emit_plan_update(plan: dict) -> None:
    await emit_event({"type": "plan", "plan": plan})


def _mark_step(plan: dict, step_id: str, status: str):
    for step in plan.get("steps", []):
        if step["id"] == step_id:
            step["status"] = status

class EventEmittingManus(Manus):
    async def think(self) -> bool:
        result = await super().think()

        if self.memory.messages:
            last_msg = self.memory.messages[-1]
            if last_msg.role == "assistant" and last_msg.content:
                await emit_event(
                    {
                        "type": "thought",
                        "content": last_msg.content,
                        "step": self.current_step,
                    }
                )

        return result

    async def execute_tool(self, command: ToolCall) -> str:
        tool_name = command.function.name
        try:
            tool_input = json.loads(command.function.arguments or "{}")
        except json.JSONDecodeError:
            tool_input = {"_raw": command.function.arguments}

        await emit_event(
            {
                "type": "tool_call",
                "tool": tool_name,
                "input": tool_input,
                "step": self.current_step,
            }
        )

        result = await super().execute_tool(command)
        output_text = str(result) if result else ""

        event: Dict[str, Any] = {
            "type": "tool_result",
            "tool": tool_name,
            "step": self.current_step,
        }

        created_files = _extract_created_files(tool_name, tool_input, output_text)
        if created_files:
            event["created_files"] = created_files

        if tool_name == "str_replace_editor":
            event["file_path"] = tool_input.get("path", "")
            event["command"] = tool_input.get("command", "")
            event["output"] = output_text[:2000]
        elif tool_name == "browser_use":
            event["action"] = tool_input.get("action", "")
            event["url"] = tool_input.get("url", "")
            event["output"] = output_text[:2000]
        elif tool_name == "document_generation":
            event["format"] = tool_input.get("format", "markdown")
            event["output"] = output_text[:2000]
        elif tool_name == "python_execute":
            event["output"] = output_text[:2000]
        elif tool_name == "bash":
            event["command"] = tool_input.get("command", "")
            event["output"] = output_text[:2000]
        elif tool_name == "web_search":
            event["query"] = tool_input.get("query", "")
            event["output"] = output_text[:2000]
        elif tool_name == "terminate":
            event["output"] = output_text[:500]
        else:
            event["output"] = output_text[:2000]

        if isinstance(result, ToolResult) and result.error:
            event["error"] = result.error

        await emit_event(event)
        if tool_name == "browser_use" and tool_input.get("action") in _VISUAL_BROWSER_ACTIONS:
            await emit_browser_state(self, self.current_step)

        return result if isinstance(result, str) else str(result)


_SKILL_PROMPTS = {
    "research": "You are performing deep web research. Use web_search to find authoritative sources, browser_use to read pages, and synthesize findings with citations. Structure your output as a research report with sources.",
    "code": "You are a senior software engineer. Write clean, tested code. Use str_replace_editor to create/edit files, python_execute to test, and bash to run commands. Follow existing code conventions.",
    "write": "You are a professional writer. Use document_generation with format='markdown' or 'docx' to create well-structured documents. Match the requested tone and format. Proofread before finishing.",
    "slides": "You are creating a slide deck. Use the document_generation tool with format='pptx'. Provide a title and a list of sections with heading and bullets. The tool will save the .pptx to /workspace. Report the file path.",
    "pdf": "You are generating a formatted PDF report. First try document_generation with format='markdown' to create the content, then use python_execute with fpdf2 or reportlab to convert it to a .pdf saved to /workspace. Report the file path.",
    "data": "You are a data analyst. Use python_execute with pandas/matplotlib to load, clean, analyze data, and save any chart image plus a summary to /workspace. Report the file paths.",
    "analyze": "You are a data analyst. Use python_execute to load, clean, and analyze data. Create visualizations if needed. Report findings with statistics and clear conclusions.",
    "translate": "You are a professional translator. Preserve meaning, tone, and cultural context. Use the target language naturally.",
    "summarize": "You are an expert summarizer. Extract key points, maintain accuracy, and keep it concise. Structure with headers if the source is long.",
    "wide-research": "__WIDE_RESEARCH__",
}


def _augment_message(message: str) -> str:
    stripped = message.strip()
    if not stripped.startswith("/"):
        return message
    parts = stripped.split(None, 1)
    skill_name = parts[0][1:]
    user_query = parts[1] if len(parts) > 1 else ""
    skill_prompt = _SKILL_PROMPTS.get(skill_name)
    if skill_prompt is None:
        return message
    if skill_prompt == "__WIDE_RESEARCH__":
        return f"[WIDE_RESEARCH]{user_query}"
    return f"[SKILL: {skill_name}] {skill_prompt}\n\nUser request: {user_query}"


async def _wide_research_decompose(agent: EventEmittingManus, query: str, n: int = 5) -> list[str]:
    prompt = (
        f"Break this research request into {n} independent sub-questions. "
        "Return ONLY a numbered list, one per line.\n\n"
        f"Request: {query}"
    )
    try:
        resp = await asyncio.wait_for(
            agent.llm.ask(
                [{"role": "user", "content": prompt}],
                system_msgs=[{"role": "system", "content": "You are a research task decomposer."}],
                stream=False,
                temperature=0.2,
            ),
            timeout=30,
        )
    except Exception:
        return [query]
    subs = []
    for line in resp.strip().splitlines():
        line = line.strip()
        if line and len(line) > 3:
            for prefix in range(len(line)):
                if line[prefix].isdigit() or line[prefix] in ".-): ":
                    continue
                subs.append(line[prefix:].strip())
                break
    return subs[:n] if subs else [query]


async def _wide_research_subtask(agent: EventEmittingManus, sub_query: str) -> str:
    try:
        resp = await asyncio.wait_for(
            agent.llm.ask(
                [{"role": "user", "content": f"Research this and provide a concise answer with key findings:\n\n{sub_query}"}],
                system_msgs=[{"role": "system", "content": "You are a research sub-agent. Be concise and factual."}],
                stream=False,
                temperature=0.2,
            ),
            timeout=30,
        )
        return f"## {sub_query}\n\n{resp}"
    except Exception as e:
        return f"## {sub_query}\n\nError: {e}"


async def _run_wide_research(agent: EventEmittingManus, query: str):
    await emit_event({"type": "thought", "content": f"Breaking down research task into sub-questions...", "step": 0})

    sub_queries = await _wide_research_decompose(agent, query)
    await emit_event({"type": "thought", "content": f"Dispatched {len(sub_queries)} parallel sub-agents:\n" + "\n".join(f"  {i+1}. {q}" for i, q in enumerate(sub_queries)), "step": 0})

    await emit_event({"type": "tool_call", "tool": "wide_research", "input": {"sub_agents": len(sub_queries), "queries": sub_queries}, "step": 0})

    raw_results = await asyncio.gather(
        *[_wide_research_subtask(agent, sq) for sq in sub_queries],
        return_exceptions=True,
    )
    results = [r if isinstance(r, str) else f"Error: {r}" for r in raw_results]

    await emit_event({"type": "tool_result", "tool": "wide_research", "step": 0, "output": f"Collected {len(results)} sub-agent results"})

    synthesis_prompt = (
        "Synthesize the following research sub-agent results into a single cohesive report. "
        "Remove duplicates, organize by theme, and add a summary.\n\n"
        + "\n\n---\n\n".join(results)
   )
    try:
        final = await asyncio.wait_for(
            agent.llm.ask(
                [{"role": "user", "content": synthesis_prompt}],
                system_msgs=[{"role": "system", "content": "You are a research synthesizer."}],
                stream=False,
                temperature=0.2,
            ),
            timeout=60,
        )
    except Exception as e:
        final = f"Synthesis failed: {e}\n\n" + "\n\n".join(results)

    await emit_event({"type": "final_answer", "content": final, "step": 0})
    await emit_event({"type": "message_complete", "total_steps": len(sub_queries)})
    await update_state("idle", 0)


async def run_agent_on_message(agent: EventEmittingManus, message: str):
    if message.startswith("[WIDE_RESEARCH]"):
        query = message[len("[WIDE_RESEARCH]"):]
        agent.update_memory("user", f"/wide-research {query}")
        await _run_wide_research(agent, query)
        return
    agent.update_memory("user", message)
    agent.state = type(agent.state).IDLE
    agent.current_step = 0
    start_step = agent.current_step
    repeated_step_count = 0
    last_signature = ""
    emitted_final = False
    current_plan: Optional[dict] = None

    # Planning phase
    current_plan = await _generate_plan(agent, message)
    await _emit_plan_update(current_plan)
    plan_response = await _wait_for_plan_response()
    action = plan_response.get("action", "approve")
    if action == "reject":
        reason = plan_response.get("revision") or "Plan was rejected by user."
        await emit_event({"type": "final_answer", "content": f"Task stopped: {reason}", "step": 0})
        await emit_event({"type": "message_complete", "total_steps": 0})
        await update_state("idle", 0)
        return
    elif action == "edit":
        revision = plan_response.get("revision", "") or message
        current_plan = await _generate_plan(agent, revision)
        await _emit_plan_update(current_plan)
        # Second approval: if still rejected, stop
        plan_response = await _wait_for_plan_response()
        if plan_response.get("action") != "approve":
            await emit_event({"type": "final_answer", "content": "Task stopped after plan edit.", "step": 0})
            await emit_event({"type": "message_complete", "total_steps": 0})
            await update_state("idle", 0)
            return

    # Mark first step active if present
    if current_plan and current_plan.get("steps"):
        current_plan["steps"][0]["status"] = "active"
        await _emit_plan_update(current_plan)

    while agent.current_step < agent.max_steps and agent.state.value != "FINISHED":
        agent.current_step += 1
        await update_state("running", agent.current_step)

        await emit_event(
            {
                "type": "step_start",
                "step": agent.current_step,
                "max_steps": agent.max_steps,
            }
        )

        # Advance plan step
        if current_plan and current_plan.get("steps"):
            active_index = max(0, min(agent.current_step - 1, len(current_plan["steps"]) - 1))
            for i, step in enumerate(current_plan["steps"]):
                if i < active_index:
                    step["status"] = "done"
                elif i == active_index:
                    step["status"] = "active"
                else:
                    step["status"] = "pending"
            await _emit_plan_update(current_plan)

        step_timeout = float(
            os.getenv("WEBMANUS_STEP_TIMEOUT", str(DEFAULT_STEP_TIMEOUT_SECONDS))
        )
        try:
            step_result = await asyncio.wait_for(agent.step(), timeout=step_timeout)
        except asyncio.TimeoutError:
            await emit_event(
                {
                    "type": "error",
                    "message": f"Agent step timed out after {step_timeout:g} seconds.",
                    "step": agent.current_step,
                }
            )
            agent.state = type(agent.state).FINISHED
            break
        signature = step_signature(agent)
        if signature and signature == last_signature:
            repeated_step_count += 1
        else:
            repeated_step_count = 0
        last_signature = signature

        if agent.state.value == "FINISHED":
            final_content = latest_assistant_content(agent)
            await emit_event(
                {
                    "type": "final_answer",
                    "content": final_content,
                    "step": agent.current_step,
                }
            )
            emitted_final = True
            break

        if not agent.tool_calls:
            final_content = latest_assistant_content(agent) or str(step_result)
            await emit_event(
                {
                    "type": "final_answer",
                    "content": final_content,
                    "step": agent.current_step,
                }
            )
            emitted_final = True
            agent.state = type(agent.state).FINISHED
            break

        if repeated_step_count >= 2 or agent.is_stuck():
            final_content = latest_assistant_content(agent)
            await emit_event(
                {
                    "type": "error",
                    "message": (
                        "Agent stopped because it repeated the same step. "
                        "Try narrowing the request or clearing the session."
                    ),
                    "step": agent.current_step,
                }
            )
            if final_content:
                await emit_event(
                    {
                        "type": "final_answer",
                        "content": final_content,
                        "step": agent.current_step,
                    }
                )
                emitted_final = True
            agent.state = type(agent.state).FINISHED
            break

    if not emitted_final and agent.current_step >= agent.max_steps:
        final_content = latest_assistant_content(agent)
        await emit_event(
            {
                "type": "error",
                "message": f"Agent stopped after reaching max steps ({agent.max_steps}).",
                "step": agent.current_step,
            }
        )
        if final_content:
            await emit_event(
                {
                    "type": "final_answer",
                    "content": final_content,
                    "step": agent.current_step,
                }
            )

    await update_state("idle", agent.current_step)

    if current_plan and current_plan.get("steps"):
        for step in current_plan["steps"]:
            if step["status"] == "active":
                step["status"] = "done"
            elif step["status"] == "pending":
                step["status"] = "done"
        await _emit_plan_update(current_plan)

    await emit_event(
        {
            "type": "message_complete",
            "total_steps": agent.current_step - start_step,
        }
    )


async def main():
    await emit_event({"type": "agent_start", "status": "initializing"})
    await update_state("initializing")

    try:
        llm_config_name = os.getenv("WEBMANUS_LLM_CONFIG", "default")
        agent = await EventEmittingManus.create(llm=LLM(config_name=llm_config_name))
        await emit_event(
            {
                "type": "agent_ready",
                "model": agent.llm.model if agent.llm else "unknown",
                "llm_config": llm_config_name,
            }
        )
        await emit_agent_capabilities(agent)
        await update_state("idle")
    except Exception as e:
        await emit_event({"type": "error", "message": f"Failed to create agent: {e}"})
        await update_state("error")
        return

    while True:
        msg_data = await read_input_message()
        if msg_data:
            raw_message = msg_data.get("message", "")
            if isinstance(raw_message, str) and raw_message.strip().startswith('{'):
                try:
                    parsed = json.loads(raw_message)
                    if isinstance(parsed, dict) and parsed.get("message_type") == "plan_response":
                        await asyncio.sleep(0.1)
                        continue
                except json.JSONDecodeError:
                    pass
            if isinstance(raw_message, str) and not raw_message.strip():
                pass
            else:
                try:
                    await run_agent_on_message(agent, _augment_message(raw_message))
                except Exception as e:
                    await emit_event(
                        {
                            "type": "error",
                            "message": f"Error processing message: {e}",
                        }
                    )
                    await update_state("error")
        else:
            await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(main())
