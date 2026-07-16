"""
CWM Service — the intelligence layer that connects Claude to the Sparrow
pipeline via the Code World Model.

The CWM itself lives at data/cwm/sparrow_world_model.py and is a living,
executable Python file that the AI can read, execute, and evolve.
"""
from __future__ import annotations
import os
import sys
import time
import json
import asyncio
import subprocess
import importlib.util
from pathlib import Path
from typing import Optional, Callable, Any

import anthropic


CWM_TEMPLATE_PATH = Path(__file__).parent.parent.parent / "cwm" / "sparrow_world_model.py"


async def run_analysis(
    data_dir: str,
    anthropic_api_key: str,
    prompt: str,
    on_event: Optional[Callable[[dict], None]] = None,
) -> dict:
    """
    Run a CWM analysis using Claude. The agent can read the CWM file,
    execute Python against it, and evolve the model.

    Returns {"output": str, "model_evolved": bool, "error": Optional[str]}
    """
    cwm_path = Path(data_dir) / "cwm" / "sparrow_world_model.py"

    # Seed the CWM file if it doesn't exist yet
    if not cwm_path.exists() and CWM_TEMPLATE_PATH.exists():
        import shutil
        cwm_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(CWM_TEMPLATE_PATH), str(cwm_path))

    if not anthropic_api_key:
        return {"output": "No Anthropic API key configured. Set it in Settings to enable CWM intelligence.", "model_evolved": False, "error": None}

    client = anthropic.Anthropic(api_key=anthropic_api_key)

    system_prompt = f"""You are the Sparrow AI pipeline assistant. Your job is to monitor and maintain the media download pipeline using the Code World Model (CWM).

The CWM is at: {cwm_path}
The data directory is: {data_dir}

When given a task:
1. Read the CWM file to understand current pipeline state
2. Execute relevant methods by running Python code
3. If a method is needed but doesn't exist, ADD it to the CWM file following existing patterns
4. Return clear, actionable findings

The CWM has a SparrowWorldModel class. Instantiate it with:
  model = SparrowWorldModel(data_dir="{data_dir}")

Then call methods like:
  model.analyze_pipeline_health()
  model.get_active_downloads()
  model.audit_library()
  etc.

If you add a new method to the CWM, document it clearly and follow the naming convention: verb_noun().

Be concise and focus on actionable findings. If something is broken, say what and how to fix it."""

    tools = [
        {
            "name": "read_file",
            "description": "Read a file from the filesystem",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "write_file",
            "description": "Write content to a file (used to evolve the CWM)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                    "content": {"type": "string", "description": "File content"}
                },
                "required": ["path", "content"]
            }
        },
        {
            "name": "run_python",
            "description": "Execute Python code and return stdout/stderr",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to run"}
                },
                "required": ["code"]
            }
        },
    ]

    messages = [{"role": "user", "content": prompt}]
    model_evolved = False
    output_parts = []
    error = None

    try:
        while True:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )

            # Collect text output
            for block in response.content:
                if hasattr(block, "text"):
                    output_parts.append(block.text)
                    if on_event:
                        on_event({"type": "text", "text": block.text})

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason != "tool_use":
                break

            # Process tool calls
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                tool_result = ""

                if tool_name == "read_file":
                    try:
                        p = Path(tool_input["path"])
                        tool_result = p.read_text() if p.exists() else f"File not found: {p}"
                    except Exception as e:
                        tool_result = f"Error: {e}"

                elif tool_name == "write_file":
                    try:
                        p = Path(tool_input["path"])
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_text(tool_input["content"])
                        tool_result = f"Written: {p}"
                        if "cwm" in str(p) or "world_model" in str(p):
                            model_evolved = True
                        if on_event:
                            on_event({"type": "model_evolved", "path": str(p)})
                    except Exception as e:
                        tool_result = f"Error: {e}"

                elif tool_name == "run_python":
                    try:
                        result = subprocess.run(
                            [sys.executable, "-c", tool_input["code"]],
                            capture_output=True,
                            text=True,
                            timeout=30,
                            cwd=str(Path(data_dir).parent),
                        )
                        tool_result = result.stdout or result.stderr or "(no output)"
                    except subprocess.TimeoutExpired:
                        tool_result = "Timeout after 30s"
                    except Exception as e:
                        tool_result = f"Error: {e}"

                if on_event:
                    on_event({"type": "tool_call", "tool": tool_name, "result": tool_result[:500]})

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": tool_result,
                })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

    except Exception as e:
        error = str(e)
        output_parts.append(f"\n[Error: {e}]")

    return {
        "output": "\n".join(output_parts),
        "model_evolved": model_evolved,
        "error": error,
    }


async def quick_health_check(data_dir: str, anthropic_api_key: str) -> dict:
    """Run a lightweight pipeline health check via CWM."""
    return await run_analysis(
        data_dir=data_dir,
        anthropic_api_key=anthropic_api_key,
        prompt="Run analyze_pipeline_health() and give me a brief status report. Flag any issues.",
    )
