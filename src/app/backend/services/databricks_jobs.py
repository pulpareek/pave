"""Trigger + monitor the PAVE provisioning Job (the SoD-hardened engine path).

Used when PROVISION_MODE=job: the app SP submits a run of the provisioning Job,
which runs as the privileged provisioner SP. Adapted from the rwe_studio jobs
service. Synchronous SDK calls are wrapped via asyncio.to_thread.
"""
import asyncio
import logging
from typing import Optional

from .. import config

logger = logging.getLogger("pave.jobs")

_client = None


def _client_():
    global _client
    if _client is None:
        from databricks.sdk import WorkspaceClient
        _client = WorkspaceClient()
    return _client


def _resolve_job_id() -> Optional[str]:
    if config.PROVISIONING_JOB_ID:
        return config.PROVISIONING_JOB_ID
    try:  # find by name if the id wasn't injected
        for job in _client_().jobs.list(name="pave_provisioning_job"):
            return str(job.job_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not resolve provisioning job id by name: %s", e)
    return None


async def trigger_provisioning_job(request_id: str, action: str = "provision") -> str:
    job_id = _resolve_job_id()
    if not job_id:
        raise RuntimeError("PROVISIONING_JOB_ID not configured and job not found by name")

    def _run():
        resp = _client_().jobs.run_now(
            job_id=int(job_id),
            job_parameters={"action": action, "request_id": request_id},
        )
        return str(resp.run_id)

    run_id = await asyncio.to_thread(_run)
    logger.info("triggered provisioning job run %s (%s, request %s)", run_id, action, request_id)
    return run_id


async def get_run_status(run_id: str) -> dict:
    def _get():
        run = _client_().jobs.get_run(run_id=int(run_id))
        state = run.state
        return {
            "run_id": run_id,
            "state": state.life_cycle_state.value if state and state.life_cycle_state else "UNKNOWN",
            "result_state": state.result_state.value if state and state.result_state else None,
            "message": state.state_message if state else None,
        }
    return await asyncio.to_thread(_get)
