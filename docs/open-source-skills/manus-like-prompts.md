# Manus-like Skill Prompts for OpenManus

These prompts are wired into `app/web/agent_runner.py` via the `/` command dispatcher.

| Command | Skill prompt |
|---------|-------------|
| `/slides` | Create a slide deck using `document_generation` with `format='pptx'`. Provide `title` and an array of `sections` with `heading` and `bullets`. The tool saves the .pptx to `/workspace`. Report the file path. |
| `/write` | Use `document_generation` with `format='markdown'` or `format='docx'` to create well-structured documents. Match the requested tone and format. Proofread before finishing. |
| `/pdf` | Generate a formatted PDF report. First use `document_generation` with `format='markdown'`, then convert to PDF with `python_execute` (reportlab or fpdf2) and save to `/workspace`. |
| `/data` | Analyze data with `python_execute` (pandas, matplotlib). Save charts and a summary to `/workspace`. Report all file paths. |
| `/research` | Perform deep web research with `web_search` and `browser_use`, then synthesize findings with citations. Output a research report. |

## Tool registration
- `document_generation` is registered in `app/agent/manus.py` as part of `Manus.available_tools`.
- `app/web/agent_runner.py` extracts created file paths and emits them as `created_files` in `tool_result` events.
