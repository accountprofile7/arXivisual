"""The paper pipeline as a durable Temporal workflow.

Why each Temporal feature is here (they all map to failures this project has
actually had, not to technology enthusiasm):

- Durable execution: a worker killed mid-run (redeploys did this twice) no
  longer strands the job at "processing" forever — the workflow resumes after
  the last completed activity, and the checkpointed generation result means the
  LLM spend is never paid twice.
- Workflow ID = ``paper-{arxiv_id}``: duplicate submissions become structurally
  impossible at the orchestrator (the API's in-memory/DB dedupe remains as a
  cheap first line, but this is the guarantee).
- Per-activity retry policies: transient render failures (LaTeX/CPU
  contention) retry once automatically; generation retries only for a dead
  worker (heartbeat timeout) and resumes from per-visualization checkpoints,
  so a retry never pays for finished work twice. A single failed render is a
  failed visualization, not a failed job.
- Orchestration state (completion counters) lives in the workflow, not in
  shared mutable state between concurrent tasks — progress writes are issued
  by the workflow as render results arrive.

This module must stay sandbox-clean: no I/O, no heavy imports — activities
carry all side effects.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temporal_app.activities import (
        FailInput,
        PipelineInput,
        ProgressUpdate,
        RenderInput,
        RenderResult,
        RepairInput,
        finalize_job,
        generate_visual_aids_for_paper,
        generate_visualizations_for_paper,
        ingest_paper,
        mark_job_failed,
        record_render_failure,
        render_visualization,
        repair_visualization_code,
        update_render_progress,
    )

TASK_QUEUE = "paper-pipeline"
RENDER_TASK_QUEUE = "paper-render"

# Transient infra faults (network, DB hiccup) get one automatic retry.
_INFRA_RETRY = RetryPolicy(maximum_attempts=2)
# A repair (or a repair re-render) is a single paid LLM/render step whose
# failure keeps the original video — never worth a second attempt.
_NO_RETRY = RetryPolicy(maximum_attempts=1)
# Generation retries once. A heartbeat timeout (dead worker) or the 40-min
# start-to-close timeout can trigger it; in the latter case attempt 1 may
# still be running, which is why checkpoint ids are INSERTed under a freshly
# read index (no overwrite possible) and why the retry only fills the slots
# this run has not checkpointed — it never pays for finished work twice.
_GENERATION_RETRY = RetryPolicy(maximum_attempts=2)


def _failure_reason(exc: BaseException) -> str:
    """Human-readable cause for the job row: the innermost Temporal cause
    (activity error message or timeout type) rather than the wrapper."""
    parts: list[str] = []
    cur: BaseException | None = exc
    while cur is not None and len(parts) < 4:
        msg = str(cur).strip()
        if msg and msg not in parts:
            parts.append(msg)
        cur = cur.__cause__
    return " <- ".join(parts)[:500]


@workflow.defn
class PaperPipelineWorkflow:
    """ingest → generate → parallel renders → honest finalize."""

    @workflow.run
    async def run(self, params: PipelineInput) -> str:
        try:
            # 15 min: a real full-length paper (5k words) goes through two LLM
            # passes (summarize + organize), each retried once in-function,
            # with 300s client timeouts. 5 min fit only the abstract-only
            # ingests that #69 eliminated; the first real paper after it timed
            # out twice and failed with no error line in the logs.
            await workflow.execute_activity(
                ingest_paper,
                params,
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=_INFRA_RETRY,
            )

            # 40 min: measured generation is ~2-8 min alone, but under
            # multi-paper load it queues behind renders on the shared CPUs —
            # 25 min timed out in production the first night. (Queue WAIT
            # doesn't count here; start_to_close covers execution only.)
            render_inputs: list[RenderInput] = await workflow.execute_activity(
                generate_visualizations_for_paper,
                params,
                start_to_close_timeout=timedelta(minutes=40),
                heartbeat_timeout=timedelta(minutes=8),
                retry_policy=_GENERATION_RETRY,
            )

            total = len(render_inputs)
            if total == 0:
                await self._finalize(params.job_id, succeeded=0, total=0)
                return "no-visualizations"

            # Silent looping visual aids. patched(): workflows already in
            # flight replay without this step, deterministically. The activity
            # owns the flag (env) decision and returns [] when off; it also
            # swallows its own failures, but a Temporal-level failure here
            # (timeout, retries exhausted) must not fail the job either.
            # Skipped when there is no narrated video (the early return above:
            # aids never rescue or fail a job). Aid generation runs CONCURRENTLY
            # with the narrated renders; aids render as soon as it returns.
            use_aids = workflow.patched("visual-aids")
            aid_inputs: list[RenderInput] = []
            kind_by_viz = {ri.viz_id: ri.kind for ri in render_inputs}

            # Start all renders; the render worker's concurrency cap does the
            # throttling (Temporal queues the surplus — no semaphore needed).
            # Each render is wrapped so ONE failed activity (timeout, worker
            # death past its retry) becomes a failed RenderResult instead of
            # aborting the workflow — which used to fail the whole job and
            # strand every unfinished visualization at 'pending'.
            # The terminal status is decided by NARRATED renders only (aids never
            # fail an otherwise-successful job); progress counts every render.
            succeeded = 0
            aids_succeeded = 0
            results: list[RenderResult] = []
            if not use_aids:
                render_tasks = [self._render_one(ri) for ri in render_inputs]
                for completed, task in enumerate(asyncio.as_completed(render_tasks), start=1):
                    result: RenderResult = await task
                    results.append(result)
                    if result.succeeded:
                        succeeded += 1
                    await workflow.execute_activity(
                        update_render_progress,
                        ProgressUpdate(job_id=params.job_id, completed=completed, total=total),
                        start_to_close_timeout=timedelta(minutes=1),
                        retry_policy=_INFRA_RETRY,
                    )
            else:
                render_futs = [asyncio.ensure_future(self._render_one(ri)) for ri in render_inputs]
                aid_fut = asyncio.ensure_future(self._generate_aids(params))
                ordered = [*render_futs, aid_fut]  # fixed order: deterministic replay
                pending = set(ordered)
                total_renders = total
                completed = 0
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for fut in [f for f in ordered if f in done]:
                        if fut is aid_fut:
                            aid_inputs = fut.result()
                            for ri in aid_inputs:
                                kind_by_viz[ri.viz_id] = ri.kind
                                new_fut = asyncio.ensure_future(self._render_one(ri))
                                ordered.append(new_fut)
                                pending.add(new_fut)
                            total_renders += len(aid_inputs)
                            continue
                        result = fut.result()
                        results.append(result)
                        completed += 1
                        if result.succeeded:
                            if kind_by_viz.get(result.viz_id) == "visual_aid":
                                aids_succeeded += 1
                            else:
                                succeeded += 1
                        # The activity keeps the stored progress monotone even
                        # though total_renders grows when aids arrive.
                        await workflow.execute_activity(
                            update_render_progress,
                            ProgressUpdate(job_id=params.job_id, completed=completed, total=total_renders),
                            start_to_close_timeout=timedelta(minutes=1),
                            retry_policy=_INFRA_RETRY,
                        )

            # Repair pass (one round per viz, activity-gated via env): the
            # judge's specific findings drive a targeted layout fix, then a
            # re-render overwrites the video in place. A failed repair keeps
            # the original video — defective beats absent.
            code_by_viz = {ri.viz_id: ri.manim_code for ri in [*render_inputs, *aid_inputs]}
            # Narrated only: aids are silent loops, never repaired.
            to_repair = [
                r for r in results
                if r.repair_recommended and kind_by_viz.get(r.viz_id) != "visual_aid"
            ]
            if to_repair:
                workflow.logger.info(
                    "Repairing %d/%d defective visualization(s)", len(to_repair), total
                )
                repaired_ok = await asyncio.gather(
                    *[self._repair_one(params.job_id, r, code_by_viz[r.viz_id]) for r in to_repair]
                )
                workflow.logger.info(
                    "Repair pass: %d/%d now defect-free", sum(repaired_ok), len(to_repair)
                )

            await self._finalize(params.job_id, succeeded=succeeded, total=total)
            defective = sum(
                1 for r in results if r.severity == "major" and kind_by_viz.get(r.viz_id) != "visual_aid"
            )
            summary = f"rendered {succeeded}/{total} ({defective} flagged, {len(to_repair)} repaired)"
            if aid_inputs:
                summary += f" + {aids_succeeded}/{len(aid_inputs)} visual aids"
            return summary
        except Exception as exc:
            # Mark the job failed so pollers see the truth — with the real
            # reason — then let Temporal record the workflow failure with full
            # history for debugging.
            await workflow.execute_activity(
                mark_job_failed,
                FailInput(job_id=params.job_id, arxiv_id=params.arxiv_id, reason=_failure_reason(exc)),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=_INFRA_RETRY,
            )
            raise

    async def _generate_aids(self, params: PipelineInput) -> list[RenderInput]:
        """Visual aid generation; never raises (aids must not fail the job)."""
        try:
            return await workflow.execute_activity(
                generate_visual_aids_for_paper,
                params,
                start_to_close_timeout=timedelta(minutes=40),
                heartbeat_timeout=timedelta(minutes=8),
                retry_policy=_GENERATION_RETRY,
            )
        except Exception as exc:
            workflow.logger.warning("Visual aid generation failed; continuing without aids: %s", exc)
            return []

    async def _render_one(self, ri: RenderInput) -> RenderResult:
        try:
            # 25 min: under multi-paper load (KEDA scaled out ten minutes into
            # a two-paper burst) two renders that were on track hit the old
            # 15-min ceiling and shipped as failed visualizations.
            return await workflow.execute_activity(
                render_visualization,
                ri,
                task_queue=RENDER_TASK_QUEUE,
                start_to_close_timeout=timedelta(minutes=25),
                retry_policy=_INFRA_RETRY,
            )
        except Exception as exc:
            workflow.logger.warning("Render activity failed for %s: %s", ri.viz_id, exc)
            # Best effort: the recorder keeps the row from stranding at
            # 'pending', but a recorder failure must never abort the job.
            try:
                await workflow.execute_activity(
                    record_render_failure,
                    ri,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=_INFRA_RETRY,
                )
            except Exception as rec_exc:
                workflow.logger.warning("Could not record render failure for %s: %s", ri.viz_id, rec_exc)
            return RenderResult(viz_id=ri.viz_id, succeeded=False)

    async def _repair_one(self, job_id: str, result: RenderResult, code: str) -> bool:
        """Repair one defective visualization; returns True if the re-render
        came back defect-free. Never raises — original video stays on failure."""
        try:
            fixed_code = await workflow.execute_activity(
                repair_visualization_code,
                RepairInput(
                    job_id=job_id,
                    viz_id=result.viz_id,
                    manim_code=code,
                    issues=result.issues,
                ),
                # 18 min: vision-grounded repair (frames + 16k reasoning budget)
                # measured slower than the text path — a 10-min cap timed out a
                # repair that was on track in the first production benchmark.
                start_to_close_timeout=timedelta(minutes=18),
                retry_policy=_NO_RETRY,  # a repair is one LLM call — don't double-spend
            )
            rerender: RenderResult = await workflow.execute_activity(
                render_visualization,
                RenderInput(
                    job_id=job_id,
                    viz_id=result.viz_id,
                    manim_code=fixed_code,
                    is_repair=True,
                ),
                task_queue=RENDER_TASK_QUEUE,
                start_to_close_timeout=timedelta(minutes=25),
                retry_policy=_NO_RETRY,  # original video already exists as fallback
            )
            return rerender.succeeded and rerender.severity != "major"
        except Exception as exc:
            workflow.logger.warning("Repair failed for %s: %s", result.viz_id, exc)
            return False

    async def _finalize(self, job_id: str, succeeded: int, total: int) -> None:
        await workflow.execute_activity(
            finalize_job,
            ProgressUpdate(job_id=job_id, completed=succeeded, total=total),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=_INFRA_RETRY,
        )
