
from typing import Any
import asyncio

class ShellExecTool:
    name = "shell_exec"
    description = "Execute a shell command and return stdout/stderr output. Use for simple filesystem operations, running scripts, etc."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute"
            }
        },
        "required": ["command"]
    }

    async def execute(self, **kwargs: Any) -> str:
        command = kwargs["command"]
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        output = []
        if stdout:
            output.append(f"stdout:\n{stdout.decode().strip()}")
        if stderr:
            output.append(f"stderr:\n{stderr.decode().strip()}")
        output.append(f"exit code: {proc.returncode}")
        return "\n".join(output) if output else "Command completed with no output."
