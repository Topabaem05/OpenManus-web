from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

from microsandbox import Sandbox
from microsandbox.types import Network

from app.config import config
from app.logger import logger

EVENT_MARKER = b"WEBMANUS_EVENT:"
AGENT_RUNNER_PATH = "app/web/agent_runner.py"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SESSION_STORE_PATH = PROJECT_ROOT / "workspace" / ".webmanus_sessions.json"


class SessionStore:
    """Lightweight JSON persistence for session metadata."""

    def __init__(self, path: Path = SESSION_STORE_PATH):
        self.path = path
        self._entries: list[dict] = []
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self._entries = json.loads(self.path.read_text(encoding="utf-8")) or []
            except (json.JSONDecodeError, IOError):
                self._entries = []
        else:
            self._entries = []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._entries, ensure_ascii=False, indent=2), encoding="utf-8")

    def upsert(self, session_id: str, **fields):
        for entry in self._entries:
            if entry.get("session_id") == session_id:
                entry.update({k: v for k, v in fields.items() if v is not None})
                entry["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save()
                return
        new_entry = {"session_id": session_id, "created_at": datetime.now(timezone.utc).isoformat()}
        new_entry.update({k: v for k, v in fields.items() if v is not None})
        new_entry["updated_at"] = new_entry["created_at"]
        self._entries.append(new_entry)
        self._save()

    def update_status(self, session_id: str, status: str):
        self.upsert(session_id, status=status)

    def remove(self, session_id: str):
        self._entries = [e for e in self._entries if e.get("session_id") != session_id]
        self._save()

    def list(self) -> list[dict]:
        return list(self._entries)

INPUT_FILE = "/tmp/webmanus_input.json"
STATE_FILE = "/tmp/webmanus_state.json"
SESSION_CREATE_TIMEOUT_SECONDS = 120
VM_SETUP_TIMEOUT_SECONDS = 180
PLAYWRIGHT_BROWSER_TIMEOUT_SECONDS = 180
RUNNER_START_TIMEOUT_SECONDS = 30
SESSION_CLEANUP_TIMEOUT_SECONDS = 30
PLAYWRIGHT_SYSTEM_PACKAGES = [
    "ca-certificates",
    "fonts-liberation",
    "libasound2",
    "libatk-bridge2.0-0",
    "libatk1.0-0",
    "libatspi2.0-0",
    "libcairo2",
    "libcups2",
    "libdbus-1-3",
    "libdrm2",
    "libexpat1",
    "libfontconfig1",
    "libgbm1",
    "libglib2.0-0",
    "libgtk-3-0",
    "libnspr4",
    "libnss3",
    "libpango-1.0-0",
    "libx11-6",
    "libxcb1",
    "libxcomposite1",
    "libxdamage1",
    "libxext6",
    "libxfixes3",
    "libxkbcommon0",
    "libxrandr2",
    "libxrender1",
    "libxshmfence1",
]


@dataclass
class SessionInfo:
    session_id: str
    vm_name: str
    sandbox: Any = None
    agent_stream: Any = None
    status: str = "initializing"
    error: Optional[str] = None
    llm_config: str = "default"
    llm_model: str = "unknown"
    vm_health: Dict[str, Any] = field(default_factory=dict)
    event_parser: EventParser | None = None


class EventParser:
    def __init__(self):
        self._buffer = b""

    def parse_raw_bytes(self, data: bytes) -> list[dict]:
        self._buffer += data
        events = []
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            line = line.strip()
            if line.startswith(EVENT_MARKER):
                try:
                    payload = line[len(EVENT_MARKER) :].decode("utf-8")
                    event = json.loads(payload)
                    events.append(event)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.warning(f"Failed to parse event: {e}, raw: {line[:200]}")
        return events


class SandboxSessionManager:
    def __init__(self):
        self._sessions: Dict[str, SessionInfo] = {}
        self._event_queues: Dict[str, asyncio.Queue[dict]] = {}
        self.store = SessionStore()

    async def create_session(self, session_id: Optional[str] = None) -> SessionInfo:
        session_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        vm_name = f"webmanus_{session_id}"

        llm_config_name = os.getenv("WEBMANUS_LLM_CONFIG", "default")
        llm_settings = config.llm.get(llm_config_name, config.llm["default"])
        info = SessionInfo(
            session_id=session_id,
            vm_name=vm_name,
            llm_config=llm_config_name,
            llm_model=llm_settings.model,
            event_parser=EventParser(),
        )
        self._sessions[session_id] = info
        self._event_queues[session_id] = asyncio.Queue()

        self.store.upsert(session_id, status=info.status, last_message=None, summary=None)

        try:
            sandbox = await asyncio.wait_for(
                Sandbox.create(
                    vm_name,
                    image="python:3.12-slim",
                    network=Network.allow_all(),
                ),
                timeout=SESSION_CREATE_TIMEOUT_SECONDS,
            )
            info.sandbox = sandbox
            info.status = "setting_up"

            await asyncio.wait_for(
                self._setup_vm(sandbox), timeout=VM_SETUP_TIMEOUT_SECONDS
            )
            info.vm_health = await self._probe_vm_health(sandbox)
            if info.vm_health.get("status") != "ready":
                raise RuntimeError(
                    info.vm_health.get("error", "VM health check failed")
                )

            agent_stream = await asyncio.wait_for(
                self._start_agent_runner(
                    sandbox,
                    browser_executable_path=info.vm_health.get(
                        "browser_executable_path"
                    ),
                ),
                timeout=RUNNER_START_TIMEOUT_SECONDS,
            )
            info.agent_stream = agent_stream
            info.status = "ready"
            self.store.update_status(session_id, "ready")
            logger.info(f"Session {session_id} ready (VM: {vm_name})")
            return info

        except Exception as e:
            info.status = "error"
            info.error = str(e)
            self.store.upsert(session_id, status="error", error=str(e))
            logger.error(f"Session {session_id} setup failed: {e}")
            await self.destroy_session(session_id)
            raise

    async def _setup_vm(self, sandbox):
        result = await sandbox.exec("python3", ["--version"])
        logger.info(f"VM Python: {result.stdout_text.strip()}")

        await self._copy_project_code(sandbox)

        runtime_packages = [
            "pydantic~=2.10.6",
            "openai~=1.66.3",
            "tenacity~=9.0.0",
            "pyyaml~=6.0.2",
            "loguru~=0.7.3",
            "structlog~=25.2.0",
            "tiktoken~=0.9.0",
            "boto3~=1.37.18",
            "requests~=2.32.3",
            "beautifulsoup4~=4.13.3",
            "browser-use~=0.1.40",
            "playwright~=1.51.0",
            "mcp~=1.5.0",
            "docker~=7.1.0",
            "googlesearch-python~=1.3.0",
            "baidusearch~=1.0.3",
            "duckduckgo_search~=7.5.3",
        ]
        result = await sandbox.exec("pip", ["install", "-q", *runtime_packages])
        if result.exit_code != 0:
            logger.warning(f"pip install had issues: {result.stderr_text[:500]}")

        browser_result = await asyncio.wait_for(
            sandbox.exec(
                "python3",
                [
                    "-m",
                    "playwright",
                    "install",
                    "--with-deps",
                    "--only-shell",
                    "chromium",
                ],
            ),
            timeout=PLAYWRIGHT_BROWSER_TIMEOUT_SECONDS,
        )
        if browser_result.exit_code != 0:
            logger.warning(
                "Playwright dependency install failed; retrying Chromium-only "
                f"install: {browser_result.stderr_text[:500]}"
            )
            deps_result = await asyncio.wait_for(
                sandbox.exec(
                    "bash",
                    [
                        "-lc",
                        (
                            "apt-get update -qq && "
                            "DEBIAN_FRONTEND=noninteractive apt-get install -y "
                            "--no-install-recommends "
                            + " ".join(PLAYWRIGHT_SYSTEM_PACKAGES)
                        ),
                    ],
                ),
                timeout=PLAYWRIGHT_BROWSER_TIMEOUT_SECONDS,
            )
            if deps_result.exit_code != 0:
                logger.warning(
                    "Manual Playwright dependency install had issues: "
                    f"{deps_result.stderr_text[:500]}"
                )
            browser_result = await asyncio.wait_for(
                sandbox.exec(
                    "python3",
                    ["-m", "playwright", "install", "--only-shell", "chromium"],
                ),
                timeout=PLAYWRIGHT_BROWSER_TIMEOUT_SECONDS,
            )
            if browser_result.exit_code != 0:
                raise RuntimeError(
                    "Failed to install Chromium for Playwright: "
                    f"{browser_result.stderr_text[:500]}"
                )

        await sandbox.exec("mkdir", ["-p", "/tmp"])
        await sandbox.exec("mkdir", ["-p", "/workspace"])

    async def _probe_vm_health(self, sandbox) -> Dict[str, Any]:
        health: Dict[str, Any] = {"status": "ready"}
        try:
            python_result = await sandbox.exec("python3", ["--version"])
            pwd_result = await sandbox.exec("pwd", [])
            runner_result = await sandbox.exec(
                "test", ["-f", "/app/web/agent_runner.py"]
            )
            tmp_result = await sandbox.exec(
                "python3",
                [
                    "-c",
                    (
                        "from pathlib import Path;"
                        "p=Path('/tmp/webmanus_probe');"
                        "p.write_text('ok');"
                        "p.unlink();"
                        "print('ok')"
                    ),
                ],
            )
            browser_result = await sandbox.exec(
                "python3",
                [
                    "-c",
                    (
                        "from playwright.sync_api import sync_playwright;"
                        "p=sync_playwright().start();"
                        "b=p.chromium.launch(headless=True);"
                        "b.close();"
                        "p.stop();"
                        "print('ok')"
                    ),
                ],
            )
            browser_path_result = await sandbox.exec(
                "find",
                [
                    "/.cache/ms-playwright",
                    "-name",
                    "headless_shell",
                    "-type",
                    "f",
                    "-print",
                    "-quit",
                ],
            )
        except Exception as exc:
            return {"status": "error", "error": f"VM probe failed: {exc}"}

        health["python_version"] = python_result.stdout_text.strip()
        health["working_directory"] = pwd_result.stdout_text.strip()
        health["runner_path_exists"] = runner_result.exit_code == 0
        health["tmp_writable"] = tmp_result.exit_code == 0
        health["browser_ready"] = browser_result.exit_code == 0
        health["browser_executable_path"] = browser_path_result.stdout_text.strip()

        if not health["runner_path_exists"]:
            health["status"] = "error"
            health["error"] = "Runner file missing in VM"
        elif not health["tmp_writable"]:
            health["status"] = "error"
            health["error"] = "VM /tmp is not writable"
        elif not health["browser_ready"]:
            health["status"] = "error"
            health["error"] = (
                "Chromium browser failed to launch in VM: "
                f"{browser_result.stderr_text[:2000]}"
            )

        return health

    async def _copy_project_code(self, sandbox):
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            for item in ["app", "config"]:
                item_path = PROJECT_ROOT / item
                if not item_path.exists():
                    continue
                self._add_to_tar(tar, item_path, item)

            for req_file in ["requirements.txt"]:
                req_path = PROJECT_ROOT / req_file
                if req_path.exists():
                    data = req_path.read_bytes()
                    info = tarfile.TarInfo(name=req_file)
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))

        tar_buffer.seek(0)
        await sandbox.fs.write("/project.tar.gz", tar_buffer.read())

        result = await sandbox.exec("tar", ["-xzf", "/project.tar.gz", "-C", "/"])
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to extract project code: {result.stderr_text}")

        await sandbox.exec("rm", ["/project.tar.gz"])

    def _add_to_tar(self, tar: tarfile.TarFile, path: Path, arcname: str):
        if path.is_file():
            data = path.read_bytes()
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        elif path.is_dir():
            for child in sorted(path.iterdir()):
                if child.name.startswith(".") or child.name == "__pycache__":
                    continue
                child_arc = f"{arcname}/{child.name}"
                self._add_to_tar(tar, child, child_arc)

    async def _start_agent_runner(
        self, sandbox, browser_executable_path: str | None = None
    ):
        config_toml = PROJECT_ROOT / "config" / "config.toml"
        if config_toml.exists():
            await sandbox.fs.write("/config/config.toml", config_toml.read_bytes())

        await sandbox.fs.write(INPUT_FILE, b"")

        env_args = [
            f"WEBMANUS_LLM_CONFIG={os.getenv('WEBMANUS_LLM_CONFIG', 'default')}",
            f"WEBMANUS_STEP_TIMEOUT={os.getenv('WEBMANUS_STEP_TIMEOUT', '120')}",
        ]
        if browser_executable_path:
            env_args.append("WEBMANUS_BROWSER_HEADLESS=true")

        stream = await sandbox.exec_stream(
            "env",
            [*env_args, "python3", "-u", "/app/web/agent_runner.py"],
        )

        return stream

    async def send_message(self, session_id: str, message: str) -> bool:
        info = self._sessions.get(session_id)
        if not info or not info.sandbox:
            raise ValueError(f"Session {session_id} not found")
        if info.status != "ready":
            raise ValueError(f"Session {session_id} is not ready")

        msg_data = json.dumps({"message": message}).encode("utf-8")
        await info.sandbox.fs.write(INPUT_FILE, msg_data)
        self.store.upsert(session_id, last_message=message)
        await self._emit_server_event(
            session_id,
            {
                "type": "message_dispatched",
                "status": "vm_input_written",
                "message_length": len(message),
            },
        )
        return True

    async def get_session_state(self, session_id: str) -> dict:
        info = self._sessions.get(session_id)
        if not info:
            return {"status": "not_found"}

        state = {
            "session_id": info.session_id,
            "status": info.status,
            "vm_name": info.vm_name,
            "llm_config": info.llm_config,
            "llm_model": info.llm_model,
            "vm_health": info.vm_health,
        }
        if info.error:
            state["error"] = info.error

        if info.sandbox and info.status == "ready":
            try:
                state_result = await info.sandbox.exec("cat", [STATE_FILE])
                if state_result.exit_code == 0:
                    try:
                        state.update(json.loads(state_result.stdout_text))
                    except json.JSONDecodeError:
                        pass
            except Exception:
                pass

        return state

    async def get_session_health(self, session_id: str) -> dict:
        info = self._sessions.get(session_id)
        if not info:
            return {"status": "not_found"}
        return {
            "session_id": info.session_id,
            "vm_name": info.vm_name,
            "status": info.status,
            "vm_health": info.vm_health,
        }

    async def stream_events(self, session_id: str) -> AsyncGenerator[dict, None]:
        info = self._sessions.get(session_id)
        if not info or not info.agent_stream:
            return

        stream = info.agent_stream
        queue = self._event_queues.get(session_id)
        stream_iter = stream.__aiter__()
        stream_task = asyncio.create_task(self._next_stream_event(stream_iter))
        queue_task = asyncio.create_task(queue.get()) if queue is not None else None
        try:
            while True:
                pending = [stream_task]
                if queue_task is not None:
                    pending.append(queue_task)
                done, _ = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if queue_task in done:
                    yield queue_task.result()
                    queue_task = asyncio.create_task(queue.get())

                if stream_task in done:
                    try:
                        event = stream_task.result()
                    except StopAsyncIteration:
                        break

                    if event.event_type == "stdout" and event.data:
                        parsed = (info.event_parser or EventParser()).parse_raw_bytes(
                            event.data
                        )
                        for parsed_event in parsed:
                            yield parsed_event

                    elif event.event_type == "exited":
                        yield {"type": "agent_exited", "code": event.code}
                        info.status = "stopped"
                        break

                    elif event.event_type == "stderr" and event.data:
                        try:
                            text = event.data.decode("utf-8", errors="replace")
                            if text.strip():
                                yield {"type": "stderr", "message": text.strip()[:500]}
                        except Exception:
                            pass

                    stream_task = asyncio.create_task(
                        self._next_stream_event(stream_iter)
                    )
        except Exception as e:
            logger.error(f"Stream error for session {session_id}: {e}")
            yield {"type": "error", "message": f"Stream error: {e}"}
        finally:
            stream_task.cancel()
            if queue_task is not None:
                queue_task.cancel()

    async def stop_agent(self, session_id: str) -> bool:
        info = self._sessions.get(session_id)
        if not info or not info.agent_stream:
            return False

        try:
            await info.agent_stream.kill()
            info.status = "stopped"
            info.agent_stream = None
            self.store.update_status(session_id, "stopped")
            logger.info(f"Agent stopped for session {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to stop agent for session {session_id}: {e}")
            return False

    async def destroy_session(self, session_id: str) -> bool:
        info = self._sessions.get(session_id)
        if not info:
            return False

        info.status = "deleting"

        async def cleanup():
            if info.agent_stream:
                try:
                    await info.agent_stream.kill()
                except Exception:
                    pass

            if info.sandbox:
                try:
                    await info.sandbox.stop()
                except Exception:
                    pass
                try:
                    await Sandbox.remove(info.vm_name)
                except Exception:
                    pass

        try:
            await asyncio.wait_for(cleanup(), timeout=SESSION_CLEANUP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning(f"Session {session_id} cleanup timed out")
        finally:
            self._sessions.pop(session_id, None)
            self._event_queues.pop(session_id, None)
            self.store.remove(session_id)

        logger.info(f"Session {session_id} destroyed")
        return True

    def list_sessions(self) -> list[dict]:
        return [
            {
                "session_id": s.session_id,
                "vm_name": s.vm_name,
                "status": s.status,
                "error": s.error,
                "llm_config": s.llm_config,
                "llm_model": s.llm_model,
                "vm_health": s.vm_health,
            }
            for s in self._sessions.values()
        ]

    def list_sessions_with_persisted(self) -> list[dict]:
        """Merge in-memory sessions with persisted sessions (live wins on overlap)."""
        merged = {s["session_id"]: s for s in self.store.list()}
        for live in self.list_sessions():
            merged[live["session_id"]] = live
        return list(merged.values())

    @staticmethod
    def _validate_file_path(path: str, allow_relative: bool = False) -> str:
        if not path:
            raise ValueError("path is required")
        if ".." in path.split("/"):
            raise ValueError("path traversal not allowed")
        if not allow_relative and not path.startswith("/"):
            raise ValueError("path must be absolute")
        return path

    async def list_vm_files(self, session_id: str, path: str) -> dict:
        info = self._sessions.get(session_id)
        if not info or not info.sandbox:
            raise ValueError(f"Session {session_id} not found")
        path = self._validate_file_path(path)
        result = await info.sandbox.exec("ls", ["-la", path])
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to list directory: {result.stderr_text[:500]}")
        files = []
        for line in result.stdout_text.splitlines()[1:]:
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue
            name = parts[8]
            if name in (".", ".."):
                continue
            full_path = f"{path.rstrip(chr(47)):s}/{name}"
            try:
                size = int(parts[4]) if parts[4].isdigit() else None
            except Exception:
                size = None
            files.append({"name": name, "path": full_path, "size": size})
        return {"path": path, "files": files}

    async def read_vm_file(self, session_id: str, path: str) -> bytes:
        info = self._sessions.get(session_id)
        if not info or not info.sandbox:
            raise ValueError(f"Session {session_id} not found")
        path = self._validate_file_path(path)
        try:
            return await info.sandbox.fs.read(path)
        except Exception:
            pass
        result = await info.sandbox.exec("cat", [path])
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to read file: {result.stderr_text[:500]}")
        return result.stdout

    async def _emit_server_event(self, session_id: str, event: dict) -> None:
        queue = self._event_queues.get(session_id)
        if queue is not None:
            await queue.put(event)

    async def _next_stream_event(self, stream_iter):
        return await anext(stream_iter)


session_manager = SandboxSessionManager()
