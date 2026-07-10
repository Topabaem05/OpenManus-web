# QA Artifacts — Manus-style OpenManus-web pass

Date: 2026-06-28
Environment: macOS local desktop thread

## Verified artifacts

1. Document/PPT tool: `app/tool/document_generation.py`
   - Unit test: produced valid `workspace/QA_Deck.pptx` and `workspace/Unit_Test.docx`
2. Theme + CSS: `web/src/index.css`, `web/src/App.tsx`
   - Production build succeeded via Vite
   - Static production build screenshot: `.codex-cache/ui_static_screenshot.png`
3. Skill prompts / open-source skills: `docs/open-source-skills/`, updated `app/web/agent_runner.py`

## Environment blockers

- Loopback networking in this container/sandbox prevents external `curl` / Playwright from reaching local servers, even though processes bind successfully.
- Could not finish end-to-end VM session test because `POST /api/sessions` times out from an external caller.
- Could not capture the active-agent UI animation state live.

## How to verify the remaining pieces locally

```bash
# 1. Backend
uvicorn app.web.server:app --host 0.0.0.0 --port 9000

# 2. Frontend
cd web && npm run dev

# 3. Open http://localhost:5173/ in a browser
# 4. Create a session and send: /slides Create a 5-slide deck about AI agents
# 5. Watch the timeline and the Preview tab auto-open when the .pptx is created.
```
