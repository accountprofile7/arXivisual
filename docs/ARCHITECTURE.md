# arXivisual — Backend Architecture

You give the system an arXiv paper ID. It gives you back narrated, animated explainer videos for the key concepts in that paper, embedded in a readable, sectioned presentation of the paper itself. This document describes how, at the level of what actually runs.

```
  arXiv ID
     |
     v
 +-----------+     +------------------+     +--------------------+     +-----------------+
 | Ingestion  | --> | Agent pipeline   | --> | 4 validation gates | --> | Render (Manim   |
 | fetch/parse|     | analyze/plan/gen |     | retry w/ feedback  |     | + TTS) -> R2    |
 +-----------+     +------------------+     +--------------------+     +-----------------+
```

## Job Lifecycle

`POST /api/process` first passes admission control (server-verified Turnstile → durable daily cap from the jobs table → per-IP hourly/daily and global sliding windows; see `SECURITY.md`), then creates a `ProcessingJob` row (status `queued`) and schedules `jobs/worker.py:process_paper_job` as a FastAPI background task, returning the job ID immediately. The frontend polls `GET /api/status/{job_id}`; the worker writes progress milestones as it moves through three phases:

1. **Ingest** (progress 0.10 → 0.30) — fetch and parse the paper, store paper + sections in the database. Skipped if the paper was processed before; a pre-fix abstract-only ingest (`queries.is_stale`) is re-ingested instead.
2. **Generate** (0.50) — run the agent pipeline to produce validated Manim code for up to 5 concepts.
3. **Render** (0.75 → 0.95) — render each visualization to MP4 and upload it, at most 3 concurrently (`asyncio.Semaphore(3)`). Each render task commits through its own DB session; a lock serializes progress updates.

**Visual QA + self-repair** (`agents/visual_qa.py`, `temporal_app/activities.py`): after each render, a vision model samples 3 frames and judges layout defects (overlap / cutoff / collisions), scoring every verdict into Langfuse (`visual_qa_defect`). On the Temporal path, a `major` verdict triggers one **vision-grounded repair**: the video is read back through the storage backend (never the CDN — stable keys cache for a year), the defect frames plus the judge's issues go to a multimodal model, and the repaired code is re-rendered and re-judged. Every vision-failure mode falls back to a text-only repair; an unusable repair keeps the original video. Measured in production: text-only repair fixed 0/6 flagged videos; vision-grounded fixed 2/4 in its first run — the pixels carry information the text descriptions provably don't.

**Human feedback loop** (`POST /api/feedback`): viewers vote 👍/👎 per video (optional reason chip) or leave site suggestions; rows land in the `feedback` table with `paper_id` denormalized from the visualization row. Video votes are human-labeled defect data — the ground truth the vision judge can be calibrated against, and seed material for the eval golden set.

**Terminal status is honest** (`resolve_terminal_job_status` in `jobs/worker.py`): a job is `completed` only if at least one visualization actually rendered. Zero generated or all-failed renders mark the job `failed` with an explanatory error, while the parsed paper text remains readable. Individual render failures are recorded per visualization and don't sink the job.

## Ingestion (`backend/ingestion/`)

`ingest_paper(arxiv_id)` fetches metadata from the arXiv API, then parses content — LaTeXML HTML when available (probed at `arxiv.org/html`, then `ar5iv.labs.arxiv.org/html`, redirects not followed and bodies must carry `ltx_document`, so an abstract page is never mistaken for the paper) (clean headings, equations, figure captions), PDF via pymupdf4llm otherwise. Sections are extracted with heading levels and per-section equations/figures/tables, noise sections (references, acknowledgments, etc.) are dropped, and an LLM summarization pass produces per-section summaries. Output is a `StructuredPaper` (Pydantic), normalized for display by `ingestion/text_normalize.normalize_display_text` (small-caps/zero-width cleanup, organizer scaffold strip, JSON-escape undo, math-fence repair, per-`$` currency escaping, bare-TeX run wrapping, `\[ \]`/`\( \)` conversion) and persisted to the `papers` and `sections` tables. The same idempotent function runs on `GET /api/paper` output (abstracts with `from_organizer=False`), so papers stored before a rule existed render cleanly without a migration; titles go through `tex_to_text`.

## The Agent Pipeline (`backend/agents/pipeline.py`)

The chain, in order:

**SectionAnalyzer → VisualizationPlanner → ManimGenerator → CodeValidator → SpatialValidator → VoiceoverScriptValidator → RenderTester**

All LLM-backed agents extend `BaseAgent` (`agents/base.py`) and load their prompt from `backend/prompts/`.

### 1. SectionAnalyzer (`section_analyzer.py`)

Asks the LLM, per section: which concepts here deserve an animated visualization? Sections are analyzed concurrently via `asyncio.gather`; one failure doesn't stop the rest. Each `VisualizationCandidate` carries a concept name/description, a type (`architecture`, `equation`, `algorithm`, `data_flow`, `matrix`, `three_d`), and a 1–5 priority. Candidates are sorted by priority and capped at 5 (`MAX_VISUALIZATIONS`).

### 2. VisualizationPlanner (`visualization_planner.py`)

Turns a candidate plus its section text into a `VisualizationPlan`: an ordered list of scenes with descriptions and durations, plus narration points, targeting 30–45 seconds total.

### 3. ManimGenerator (`manim_generator.py`)

Generates a complete Python file: a `VoiceoverScene` subclass whose narration is written together with the animation. Before each call it selects a voiceover few-shot example matching the visualization type (`backend/examples/voiceover_*.py`) and fetches current Manim documentation (`context7_docs.py`: the Context7 REST API, with a bundled static reference as fallback; a Dedalus-MCP path exists but only runs on the legacy Dedalus provider). Generated code must set up the speech service in `construct()`, wrap each beat in `with self.voiceover(text=...) as tracker:`, and time animations with `run_time=tracker.duration`. The generator extracts narration lines and `# Beat N` labels as metadata.

### 4–7. The Validation Gates

Each generated scene must pass four gates in sequence. Any failure aborts the attempt, and feedback from **all** failed gates is concatenated and fed to `ManimGenerator.run_with_feedback()` along with the previous code. Stale gate results are cleared between attempts so retries never chase issues that no longer exist. Budget: `MAX_RETRIES` (3) + `VOICE_QUALITY_RETRIES` (2) = 5 attempts.

- **CodeValidator** (`code_validator.py`) — pure static analysis: AST parse, `from manim import *` injection, Scene-class and `construct()` checks, auto-fixes for common typos and unclosed brackets, and detection of LaTeX split across `MathTex` arguments (which crashes Manim and always forces regeneration).
- **TextBudget** (`text_budget.py`) — optional static gate, only with `VISUAL_NARRATION=1` on narrated (not silent-aid) profiles, right after CodeValidator: AST-counts on-screen text (no Paragraph/BulletedList/Title, each Text <=3 words, <=15 tag words per video, math/numbers count 1) and sends the offending strings back through the retry loop. No LLM.
- **SpatialValidator** (`spatial_validator.py`) — regex-extracts positions from `move_to`/`shift`/`next_to`/`to_edge` calls, flags elements outside screen bounds (|x| > 7, |y| > 4) or the safe area, likely overlaps, and `next_to`/`arrange` calls missing `buff`. Static analysis only, so positioning it can't parse (pure `next_to` chains) goes unchecked.
- **VoiceoverScriptValidator** (`voiceover_script_validator.py`) — hard checks (`VoiceoverScene`, `set_speech_service`, at least one voiceover block, tracker-timed plays), then narration quality: a 6–40 word window per line, rejection of animation-command phrasing ("Now we display…"), and alignment (≥ 0.45) and educational (≥ 0.50) scores from heuristics, overridden by an LLM judge when available. The judge is synchronous, so the pipeline runs it in `asyncio.to_thread` to avoid stalling concurrent visualizations.
- **RenderTester** (`render_tester.py`) — the only gate that executes the code: `compile()` for syntax in-process, then **full `construct()` execution in a dry-run subprocess** (`dry_run_driver.py`): manim's `dry_run` config processes every animation without writing frames, TTS is replaced by an embedded silent MP3 (manim-voiceover has no dry-run support and would otherwise call the real service), `add_sound` is a no-op (drops the ffmpeg dependency), and subcaptions are disabled (under dry_run `config.output_file` is an empty string, so their writer raises at finish()). ~0.2s per scene, no network, no spend. This catches the runtime class import testing structurally can't — a production render died on `if a.get_center() == b.get_center():` (numpy truth-value `ValueError`) that only fires when `construct()` runs. Verdicts come from stdout sentinels; a driver crash without a verdict fails OPEN (the real render still guards, and a gate must never block all videos on its own infrastructure). `RENDER_TEST_EXECUTE=0` falls back to legacy import-only validation; `RENDER_TEST_TIMEOUT_SECONDS` (120) bounds it; a timeout fails open (CPU starvation under load is not a code verdict — 1,405 valid scenes were rejected in one week before this).

If all 5 attempts fail, `VOICE_FAIL_BEHAVIOR` (default `return_silent`) keeps the last generated code as a pending, videoless visualization; `drop_viz` and `hard_error` are the alternatives.

### Visual aids (silent loops, `ENABLE_VISUAL_AIDS=1`)

A second, optional kind of visualization: short (5–10 s) **silent looping** clips shown inline right after the paragraph they illustrate, in addition to the narrated videos. Off by default; with the flag off nothing below runs and narrated behavior is unchanged.

- **VisualAidAnalyzer** (`visual_aid_analyzer.py`) — one JSON-mode call per section. It sees the section's *display* paragraphs, numbered (`display_paragraphs` = `split_paragraphs(normalize_display_text(summary or content))`, the exact text the reader shows), and returns up to `VISUAL_AIDS_PER_SECTION` (3) aids, each with a `paragraph_index` anchor and 2–4 storyboard beats. Concepts already covered by narrated videos are skipped. LLM text fields are length-capped.
- **No planner.** `build_aid_plan` turns the beats into a short `VisualAidPlan`, saving one LLM call per aid.
- **`GenerationProfile`** (`pipeline.py`) — `NARRATED` reproduces the narrated settings exactly; `VISUAL_AID` is silent (plain `Scene`, or `ThreeDScene` for `three_d`), 5–10 s, 3 attempts, gate names prefixed `aid:` (ignored by `evals/metrics.py`), and a failed aid is **dropped**, never shown. The generator uses `prompts/manim_generator_silent.md` and the `examples/silent_loop.py` few-shot; narrated prompts are byte-identical to before (snapshot tests).
- **Caps:** `VISUAL_AIDS_PER_SECTION` (3) and `MAX_VISUAL_AIDS` (15 per paper). Aids render through the same render path; `VISUAL_AID_RENDER_QUALITY` (default `low`) sets their quality.
- **Storage:** `visualizations.kind` (`"visual_aid"`; `NULL`/`"narrated"` = narrated) and `anchor_paragraph`. On the legacy path aid ids are `viz_{id}_{101+j}`; on Temporal they take the next free index.
- **Jobs:** aids never fail a job. Terminal status and finalize use narrated results only; progress counts every render. On Temporal, aid generation runs concurrently with the narrated renders behind `workflow.patched("visual-aids")`, and the repair round is narrated-only.
- **Paragraph contract:** `ingestion/text_normalize.py:split_paragraphs` and `frontend/lib/paragraphs.ts` must agree (blank-line split, never inside `$$` or code fences, plain fallback if a block is left open). `frontend/scripts/check-paragraphs.mjs` checks the TypeScript twin against the same cases as `tests/test_split_paragraphs.py`.

### Pipeline Configuration

Behavior knobs at the top of `agents/pipeline.py` (constants unless noted as env vars):

```python
MAX_VISUALIZATIONS = 5            # candidates kept per paper
MAX_RETRIES = 3                   # base generation attempts
VOICE_QUALITY_RETRIES = 2         # extra attempts for voiceover quality
CONCURRENT_ANALYSIS = True        # analyze sections in parallel
CONCURRENT_GENERATION = True      # generate visualizations in parallel
ENABLE_SPATIAL_VALIDATION = True
ENABLE_VOICEOVER = True
VOICE_MODE = "unified_generator"  # narration written with the animation
VOICE_FAIL_BEHAVIOR = "return_silent"
RENDER_MODE                       # env: "local" | "modal"; "modal" also skips RenderTester
VOICEOVER_TTS_SERVICE             # env: "openai" (default) | "gtts"
VOICEOVER_VOICE_NAME              # env: default "nova"
ENABLE_VISUAL_AIDS                # env: "1"/"true" enables silent visual aids (default off)
VISUAL_AIDS_PER_SECTION           # env: default 3
MAX_VISUAL_AIDS                   # env: default 15 per paper
VISUAL_AID_RENDER_QUALITY         # env: "low" (default) | "medium" | "high"
VISUAL_NARRATION                  # env: "1"/"true" makes narrated videos DRAW 3 named quantities (default off; planner + generator prompt blocks; recommended)
```

## LLM Provider Layer (`agents/base.py`)

**Claude caching:** the Manim generator's system prompt goes out as two blocks, a stable cached reference and an uncached per-plan live-docs suffix (`system_suffix`), and the pipeline pre-warms the cache once (`prewarm_system_cache`, `max_tokens=0`) before concurrent generation. Without the split, every concept had a unique prefix: one ResNet run wrote 180k cache tokens and read only 20k.

Every agent call routes through `call_llm` / `call_llm_sync`, which resolve a provider from the environment: **Azure OpenAI is the primary** (set `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`, or force with `LLM_PROVIDER=azure`). Details that matter:

- Calls go through Azure's OpenAI-compatible v1 endpoint (`{endpoint}/openai/v1/`) using the standard `openai` SDK, against the deployment named by `AZURE_OPENAI_DEPLOYMENT`.
- GPT-5 reasoning tokens count against `max_completion_tokens`, so each request adds 4096 tokens of headroom above the agent's visible-answer budget; `AZURE_OPENAI_REASONING_EFFORT` (default `low`) trades depth for speed and cost.
- Prompt templates are formatted with `str.replace`, not `str.format` — paper text is full of LaTeX braces.

**Claude (Anthropic)** is the second provider (`ANTHROPIC_API_KEY`, or force with `LLM_PROVIDER=anthropic`); auto-detect order is Azure, then Anthropic, then Dedalus. It uses the official `anthropic` SDK on the beta messages surface with streaming (no HTTP timeouts on long generations), the `server-side-fallback-2026-07-01` beta with `fallbacks="default"`, an ephemeral `cache_control` on the system prompt (the 17KB Manim reference), adaptive thinking (not sent; `max_tokens` gets 8000 tokens of headroom), `ANTHROPIC_EFFORT` (default `medium`) and `ANTHROPIC_MODEL` (default `claude-sonnet-5-5`). JSON mode is prompt-only (plus the shared repair and retry). Visual QA's vision path stays Azure-only.

A Dedalus Labs provider remains selectable as a legacy fallback (`DEDALUS_API_KEY`). Historical footnote: `agents/dedalus_base.py` (multi-model handoff chains) and `agents/voiceover_generator.py` (a post-hoc voiceover transform, superseded by unified generation and disabled by default) are legacy code from that era, slated for removal.

## Voiceover / TTS

Narration audio is produced **at render time** by manim-voiceover. The generator injects the service setup verbatim into each scene:

```python
self.set_speech_service(OpenAIService(voice="nova", model="gpt-4o-mini-tts", transcription_model=None))
```

`VOICEOVER_TTS_SERVICE` defaults to `openai`; voice and model come from `VOICEOVER_VOICE_NAME` / `VOICEOVER_TTS_MODEL`. There is no separate OpenAI key: the render subprocess environment (`rendering/local_runner.py:_tts_subprocess_env`) maps `AZURE_OPENAI_*` to `OPENAI_API_KEY`/`OPENAI_BASE_URL` (pinning `OPENAI_API_TYPE=openai`), so manim-voiceover's `OpenAIService` transparently talks to an Azure `gpt-4o-mini-tts` deployment. A real `OPENAI_API_KEY`, if set, takes precedence. `VOICEOVER_TTS_SERVICE=gtts` is the free, keyless fallback.

## Rendering (`backend/rendering/`)

`RENDER_MODE=local` (the production setting) renders in-process on the API container: the code is written to a temp directory and `manim render <file> <SceneName> -ql --format=mp4` runs via `subprocess.run` with a 300 s timeout, wrapped in `asyncio.to_thread`. Quality maps to Manim's `-ql`/`-qm`/`-qh`; the pipeline renders at `low_quality`. The worker's `Semaphore(3)` bounds concurrent renders. A `RENDER_MODE=modal` path (serverless rendering on Modal.com via `modal_runner.py`) exists and also disables the local RenderTester gate, but is not used in production.

## Storage (`rendering/storage.py`)

`STORAGE_MODE` selects a backend behind one protocol:

- `local` (default) — MP4s under `media/videos/`, served by `GET /api/video/{id}` as a `FileResponse`.
- `r2` (production) — uploads to Cloudflare R2 (S3-compatible, `boto3`) under a `videos/` prefix with a one-year immutable cache header and one retry; `video_url` becomes a public `S3_PUBLIC_URL` link. `GET /api/video/{id}` 302-redirects to it.

## API Surface (`backend/api/routes.py`)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/process` | Start processing a paper; returns a job ID |
| GET | `/api/status/{job_id}` | Poll job progress |
| GET | `/api/paper/{arxiv_id}` | Processed paper: sections (each with `videos` = every `complete` visualization, newest first; `video_url` = `videos[0]` for older clients) + visualizations; `visual_aids` = complete silent aids in paragraph order, each with `paragraph_index`, never mixed into `videos`; superseded rows excluded; 404 for stale (pre-fix abstract-only) papers so the reader offers re-processing |
| GET | `/api/papers` | Explore gallery: all processed papers |
| GET | `/api/video/{video_id}` | Serve or redirect to a rendered video |
| POST | `/api/render` | Dev-only raw Manim render — in production, 404 unless `RENDER_API_SECRET` is configured and presented via `X-Render-Secret` (timing-safe compare) |
| POST | `/api/feedback` | Store viewer feedback: per-video 👍/👎 (labeled QA ground truth) or site suggestion |
| GET | `/api/health` | Database / Manim / storage health |

CORS allows `arxivisual.org`, `www.arxivisual.org`, localhost dev, and whatever `CORS_EXTRA_ORIGINS` adds (in production: the frontend Container App's own FQDN) — see `backend/api/cors.py`.

## Persistence (`backend/db/`)

SQLAlchemy async ORM, four tables keyed off the arXiv ID: `papers`, `sections` (content, summary, equations/figures/tables as JSON), `visualizations` (concept, storyboard, Manim code, video URL, status, `kind`, `anchor_paragraph`), and `processing_jobs`. `DATABASE_URL` selects Postgres (asyncpg, used in production); unset falls back to local SQLite (aiosqlite). There is no alembic: `init_db` runs `create_all` and then `_add_missing_columns` (`db/connection.py`), which idempotently adds the nullable columns listed in `_ADDED_COLUMNS` (`IF NOT EXISTS` on Postgres, so concurrent replica startups are safe).

## Observability (Langfuse)

Tracing activates when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are both set, and degrades to no-ops otherwise:

- `base.py` swaps the OpenAI SDK for the `langfuse.openai` drop-in, so every LLM call is captured with model, tokens, cost, and latency; each generation is named after its agent's prompt file (e.g. `manim_generator`).
- `@observe` spans build the trace hierarchy: `process-paper` → `generate-visualizations` → `generate-single-visualization`, with `session_id = job_id` grouping everything for one paper run.
- The worker flushes traces before the background task exits, since it runs off-request.

`LANGFUSE_TRACING_ENVIRONMENT` separates production traces from development.

## File Map

```
backend/
  main.py                    FastAPI app: CORS, lifespan DB init
  api/
    routes.py                All endpoints; /api/render auth gate
    throttle.py              Sliding-window limiters, daily-cap verdict, client fingerprint
    turnstile.py             Server-side Cloudflare Turnstile verification (fails closed when configured)
    schemas.py               Request/response models
  jobs/
    worker.py                Background job: ingest -> generate -> render;
                             honest terminal status
  agents/
    pipeline.py              Orchestration, gate sequence, retry loop
    base.py                  Provider routing (Azure OpenAI / Dedalus),
                             Langfuse wrapper, prompt loading
    section_analyzer.py      Finds visualization-worthy concepts
    visualization_planner.py Scene-by-scene storyboards
    manim_generator.py       VoiceoverScene code generation, TTS snippet
    code_validator.py        Gate 1: static syntax/structure checks
    spatial_validator.py     Gate 2: bounds/overlap checks
    voiceover_script_validator.py  Gate 3: narration quality + LLM judge
    render_tester.py         Gate 4: dry-run construct() execution (subprocess)
    dry_run_driver.py        subprocess harness for the gate: dry_run config + TTS/add_sound stubs
    context7_docs.py         Live Manim docs fetch with static fallback
    dedalus_base.py          LEGACY (slated for removal)
    voiceover_generator.py   LEGACY post-transform voiceover (disabled)
  ingestion/                 arXiv fetch, HTML/PDF parse, section extraction
  rendering/
    __init__.py              RENDER_MODE routing, process_visualization()
    local_runner.py          Manim subprocess + TTS env mapping
    modal_runner.py          Modal.com serverless path (unused in prod)
    storage.py               local / R2 storage backends
  models/                    Pydantic domain models
  db/                        SQLAlchemy ORM, connection, queries
  prompts/                   Agent prompt templates + Manim reference
  examples/                  Few-shot Manim examples (incl. voiceover_*)
  tests/                     Offline unit suite (run: uv run pytest tests/)
```
