import asyncio
import io
import json
from contextlib import asynccontextmanager
from typing import Dict, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.web.sandbox_manager import session_manager


class WebSocketManager:
    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._stream_tasks: Dict[str, asyncio.Task] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        if session_id not in self._connections:
            self._connections[session_id] = set()
        self._connections[session_id].add(websocket)

        if session_id not in self._stream_tasks:
            task = asyncio.create_task(self._forward_events(session_id))
            self._stream_tasks[session_id] = task

    def disconnect(self, websocket: WebSocket, session_id: str):
        if session_id in self._connections:
            self._connections[session_id].discard(websocket)
            if not self._connections[session_id]:
                del self._connections[session_id]

    async def _forward_events(self, session_id: str):
        try:
            async for event in session_manager.stream_events(session_id):
                if session_id in self._connections:
                    dead = set()
                    for ws in self._connections[session_id]:
                        try:
                            await ws.send_json(event)
                        except Exception:
                            dead.add(ws)
                    for ws in dead:
                        self._connections[session_id].discard(ws)
                    if not self._connections.get(session_id):
                        break
        except Exception as e:
            error_event = {"type": "error", "message": f"Stream error: {e}"}
            if session_id in self._connections:
                for ws in list(self._connections[session_id]):
                    try:
                        await ws.send_json(error_event)
                    except Exception:
                        pass
        finally:
            self._stream_tasks.pop(session_id, None)

    async def broadcast(self, session_id: str, event: dict):
        if session_id in self._connections:
            for ws in list(self._connections[session_id]):
                try:
                    await ws.send_json(event)
                except Exception:
                    pass


ws_manager = WebSocketManager()


class CreateSessionRequest(BaseModel):
    session_id: str | None = None


class SendMessageRequest(BaseModel):
    message: str = ""
    message_type: str = "chat"
    action: str = "approve"
    revision: str = ""
    plan_id: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for sid in list(session_manager._sessions.keys()):
        await session_manager.destroy_session(sid)


app = FastAPI(title="Web-Manus Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/sessions")
async def create_session(req: CreateSessionRequest):
    try:
        info = await session_manager.create_session(req.session_id)
        return {
            "session_id": info.session_id,
            "status": info.status,
            "vm_name": info.vm_name,
            "llm_config": info.llm_config,
            "llm_model": info.llm_model,
            "vm_health": info.vm_health,
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to create session: {e}"},
        )


@app.get("/api/sessions")
async def list_sessions():
    return {"sessions": session_manager.list_sessions_with_persisted()}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    state = await session_manager.get_session_state(session_id)
    if state.get("status") == "not_found":
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return state


@app.get("/api/sessions/{session_id}/health")
async def get_session_health(session_id: str):
    state = await session_manager.get_session_health(session_id)
    if state.get("status") == "not_found":
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return state



@app.get("/api/sessions/{session_id}/files/list")
async def list_session_files(session_id: str, path: str = "/workspace"):
    try:
        result = await session_manager.list_vm_files(session_id, path)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions/{session_id}/files")
async def download_session_file(session_id: str, path: str):
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        content = await session_manager.read_vm_file(session_id, path)
        import mimetypes

        media_type, _ = mimetypes.guess_type(path)
        return StreamingResponse(
            io.BytesIO(content),
            media_type=media_type or "application/octet-stream",
            headers={"Content-Disposition": f"inline; filename={path.split('/')[-1]}"},
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str):
    ok = await session_manager.stop_agent(session_id)
    if not ok:
        return JSONResponse(status_code=404, content={"error": "Session not found or no agent running"})
    return {"status": "stopped"}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    # Persist deletion before destroying; final cleanup removes the entry.
    try:
        session_manager.store.update_status(session_id, "deleted")
    except Exception:
        pass
    ok = await session_manager.destroy_session(session_id)
    if not ok:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return {"status": "deleted"}


@app.post("/api/sessions/{session_id}/message")
async def send_message(session_id: str, req: SendMessageRequest):
    try:
        if req.message_type == "plan_response":
            ok = await session_manager.send_message(session_id, json.dumps({
                "message_type": "plan_response",
                "action": req.action,
                "revision": req.revision,
            }))
        else:
            ok = await session_manager.send_message(session_id, req.message)
        if not ok:
            return JSONResponse(status_code=404, content={"error": "Session not found"})
        return {"status": "sent"}
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await ws_manager.connect(websocket, session_id)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "message":
                    await session_manager.send_message(session_id, msg.get("message", ""))
            except json.JSONDecodeError:
                await session_manager.send_message(session_id, data)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, session_id)
    except Exception:
        ws_manager.disconnect(websocket, session_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9000)
