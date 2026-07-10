import asyncio
import json
import os
import sys
import tempfile
from typing import Dict

from app.tool.base import BaseTool


class PythonExecute(BaseTool):
    """A tool for executing Python code in a subprocess with timeout."""

    name: str = "python_execute"
    description: str = "Executes Python code string. Note: Only print outputs are visible, function return values are not captured. Use print statements to see results."
    parameters: dict = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The Python code to execute.",
            },
        },
        "required": ["code"],
    }

    async def execute(
        self,
        code: str,
        timeout: int = 10,
    ) -> Dict:
        """
        Executes the provided Python code in a subprocess with a timeout.

        Args:
            code (str): The Python code to execute.
            timeout (int): Execution timeout in seconds.

        Returns:
            Dict: Contains 'output' with execution output or error message and 'success' status.
        """
        # ponytail: stdlib-only wrapper run by a fresh interpreter; no heavy deps
        wrapper = (
            "import json, sys\n"
            "from io import StringIO\n"
            "code = sys.argv[1]\n"
            "output_path = sys.argv[2]\n"
            "buf = StringIO()\n"
            "old_stdout = sys.stdout\n"
            "try:\n"
            "    sys.stdout = buf\n"
            "    exec(code, {\"__builtins__\": __builtins__})\n"
            "    result = {\"observation\": buf.getvalue(), \"success\": True}\n"
            "except Exception as e:\n"
            "    result = {\"observation\": str(e), \"success\": False}\n"
            "finally:\n"
            "    sys.stdout = old_stdout\n"
            "with open(output_path, \"w\") as f:\n"
            "    json.dump(result, f)\n"
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            output_path = f.name

        # ponytail: isolate child from heavy project/site hooks that slow startup
        child_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                wrapper,
                code,
                output_path,
                env=child_env,
            )
            await asyncio.wait_for(proc.communicate(), timeout=timeout)

            with open(output_path, "r") as f:
                return json.load(f)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return {"observation": f"Execution timeout after {timeout} seconds", "success": False}
        except Exception as e:
            return {"observation": f"Failed to read execution result: {e}", "success": False}
        finally:
            try:
                os.unlink(output_path)
            except OSError:
                pass
