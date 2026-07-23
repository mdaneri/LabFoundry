from __future__ import annotations

import json
import logging
import signal
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from labfoundry.app.adapters.system import SystemAdapter
from labfoundry.app.config import get_settings
from labfoundry.app.database import SessionLocal, init_db
from labfoundry.app.models import AuditEvent, AutomationScriptRevision, Job, JobStatus, utcnow
from labfoundry.app.services.appliance_update import APPLIANCE_UPDATE_FINALIZER_PATH
from labfoundry.app.services.automation import enqueue_due_schedules, json_object


LOGGER = logging.getLogger("labfoundry.worker")
POLL_SECONDS = 5
AUTOMATION_STAGE_DIR = Path("/var/lib/labfoundry/automation/scripts")
WORKER_JOB_TYPES = {"appliance-update", "vcf-depot-download", "managed-script"}
_stop_requested = False


def _request_stop(_signum: int, _frame: object) -> None:
    global _stop_requested
    _stop_requested = True


def _job_config(job: Job) -> dict[str, Any]:
    return json_object(job.task_config_json, label="Job configuration")


def claim_next_job(db: Session) -> Job | None:
    job = db.execute(
        select(Job)
        .where(Job.status == JobStatus.PENDING.value, Job.type.in_(WORKER_JOB_TYPES))
        .order_by(Job.created_at, Job.id)
    ).scalars().first()
    if job is None:
        return None
    job.status = JobStatus.RUNNING.value
    job.started_at = utcnow()
    job.progress_percent = max(1, int(job.progress_percent or 0))
    db.add(job)
    db.commit()
    return job


def _release_finalizer() -> dict[str, Any]:
    try:
        payload = json.loads(Path(APPLIANCE_UPDATE_FINALIZER_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def recover_interrupted_worker_jobs(db: Session) -> int:
    jobs = db.execute(
        select(Job).where(Job.type.in_(WORKER_JOB_TYPES), Job.status == JobStatus.RUNNING.value)
    ).scalars().all()
    now = utcnow()
    finalizer = _release_finalizer()
    for job in jobs:
        definitive = (
            finalizer
            if job.type == "appliance-update" and str(finalizer.get("job_id") or "") == job.id
            else {}
        )
        finalizer_status = str(definitive.get("status") or "")
        recovered = finalizer_status in {JobStatus.SUCCEEDED.value, JobStatus.FAILED.value}
        job.status = finalizer_status if recovered else JobStatus.FAILED.value
        job.finished_at = now
        job.progress_percent = 100
        job.error = (
            None
            if recovered and job.status == JobStatus.SUCCEEDED.value
            else str(definitive.get("error") or "The LabFoundry worker restarted while this task was running. The task was not rerun automatically.")
        )
        try:
            result = json.loads(job.result or "{}")
        except json.JSONDecodeError:
            result = {}
        result.update(
            {
                "status": job.status,
                "success": job.status == JobStatus.SUCCEEDED.value,
                "release_transaction": definitive,
                "worker_recovery": "root_finalizer" if recovered else "interrupted",
            }
        )
        if job.error:
            result["error"] = job.error
        job.result = json.dumps(result, indent=2, sort_keys=True)
        db.add(job)
    if jobs:
        db.commit()
    return len(jobs)


def _fail_job(db: Session, job: Job, exc: Exception) -> None:
    job.status = JobStatus.FAILED.value
    job.finished_at = utcnow()
    job.progress_percent = 100
    job.error = str(exc)
    try:
        result = json.loads(job.result or "{}")
    except json.JSONDecodeError:
        result = {}
    result.update({"status": JobStatus.FAILED.value, "success": False, "error": str(exc)})
    job.result = json.dumps(result, indent=2, sort_keys=True)
    db.add(job)
    db.commit()


def _run_appliance_update(job_id: str) -> None:
    from labfoundry.app.ui import (
        appliance_update_exception_result,
        appliance_update_settings,
        complete_appliance_update_task,
        execute_appliance_update_job,
    )
    from labfoundry.app.services.update_sources import update_source_credentials

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        config = _job_config(job)
        selected = [str(value) for value in config.get("selected_streams", [])]
        settings = config.get("settings") if isinstance(config.get("settings"), dict) else appliance_update_settings(db)
        mode = str(config.get("mode") or "check")
        actor = job.created_by
        credentials = update_source_credentials(db)
    try:
        update_result = execute_appliance_update_job(
            selected_stream_ids=selected,
            settings=settings,
            actor=actor,
            mode=mode,
            job_id=job_id,
            credentials=credentials,
        )
    except Exception as exc:  # noqa: BLE001 - workers must persist a terminal job state.
        LOGGER.exception("Appliance update job %s failed before helper completion", job_id)
        update_result = appliance_update_exception_result(
            selected_stream_ids=selected,
            settings=settings,
            actor=actor,
            mode=mode,
            exc=exc,
        )
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        complete_appliance_update_task(db, job=job, update_result=update_result)


def _automation_stage_path(job_id: str, interpreter: str) -> Path:
    suffix = {"bash": ".sh", "python": ".py", "powershell": ".ps1"}[interpreter]
    if get_settings().environment != "appliance":
        return Path("data") / "automation" / "scripts" / f"{job_id}{suffix}"
    return AUTOMATION_STAGE_DIR / f"{job_id}{suffix}"


def _run_managed_script(db: Session, job: Job) -> None:
    config = _job_config(job)
    revision_id = int(config.get("revision_id") or 0)
    revision = db.get(AutomationScriptRevision, revision_id)
    if revision is None or not revision.enabled:
        raise ValueError("The scheduled managed script revision is missing or disabled.")
    arguments = config.get("arguments", [])
    if not isinstance(arguments, list) or any(not isinstance(argument, str) for argument in arguments):
        raise ValueError("The scheduled managed script arguments are invalid.")
    stage_path = _automation_stage_path(job.id, revision.interpreter)
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    stage_path.write_text(revision.content, encoding="utf-8")
    stage_path.chmod(0o640)
    try:
        result = SystemAdapter().run_automation_script(str(stage_path), revision.interpreter, revision.timeout_seconds, arguments)
    finally:
        stage_path.unlink(missing_ok=True)
    payload = {
        "status": JobStatus.SUCCEEDED.value if result.returncode == 0 else JobStatus.FAILED.value,
        "success": result.returncode == 0,
        "revision_id": revision.id,
        "script_id": revision.script_id,
        "interpreter": revision.interpreter,
        "arguments_count": len(arguments),
        "content_sha256": revision.content_sha256,
        "dry_run": result.dry_run,
        "command": result.command,
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-8000:],
    }
    job.status = payload["status"]
    job.finished_at = utcnow()
    job.progress_percent = 100
    job.error = None if payload["success"] else (result.stderr[-2000:] or "Managed script failed.")
    job.result = json.dumps(payload, indent=2, sort_keys=True)
    db.add(job)
    db.add(
        AuditEvent(
            actor=job.created_by,
            action="execute_managed_script",
            resource_type="job",
            resource_id=job.id,
            success=bool(payload["success"]),
            detail=f"revision_id={revision.id}; sha256={revision.content_sha256}; returncode={result.returncode}",
        )
    )
    db.commit()


def run_worker_once() -> str | None:
    with SessionLocal() as db:
        enqueue_due_schedules(db)
        job = claim_next_job(db)
        if job is None:
            return None
        job_id = job.id
        job_type = job.type
    try:
        if job_type == "appliance-update":
            _run_appliance_update(job_id)
        elif job_type == "vcf-depot-download":
            from labfoundry.app.ui import run_vcf_depot_download_job
            from labfoundry.app.models import VcfDepotDownloadProfile

            with SessionLocal() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return job_id
                config = _job_config(job)
                if not config:
                    config = json_object(job.result or "{}", label="VCF job configuration")
                profile_id = int(config.get("profile_id") or 0)
                profile = db.get(VcfDepotDownloadProfile, profile_id)
                if profile is None or not profile.enabled:
                    raise ValueError("Enable the scheduled VCF Offline Depot profile before running it.")
            run_vcf_depot_download_job(job_id, profile_id)
        elif job_type == "managed-script":
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                if job is not None:
                    _run_managed_script(db, job)
        else:
            raise ValueError(f"No worker handler is registered for job type {job_type}.")
    except Exception as exc:  # noqa: BLE001 - the worker must survive individual job failures.
        LOGGER.exception("Job %s failed", job_id)
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job is not None and job.status in {JobStatus.PENDING.value, JobStatus.RUNNING.value}:
                _fail_job(db, job, exc)
    return job_id


def main() -> int:
    global _stop_requested
    _stop_requested = False
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    init_db()
    with SessionLocal() as db:
        recovered = recover_interrupted_worker_jobs(db)
        if recovered:
            LOGGER.warning("Marked %s interrupted worker jobs failed", recovered)
    LOGGER.info("LabFoundry worker started")
    while not _stop_requested:
        handled = run_worker_once()
        if handled is None:
            time.sleep(POLL_SECONDS)
    LOGGER.info("LabFoundry worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
