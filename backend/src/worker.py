from .constants.status import RunStatus
from .constants.identity import IdentityVersionStatus
import json, time, traceback
from pathlib import Path
from .config.database import SessionLocal,engine,Base
from .models import AnalysisRun,Experiment,Observation,IdentityVersion
from .services.run_service import queue,identity_queue
from .services.identity_service import process_version
from .services.analysis_service import run_analysis
from .services.audit_service import log
Base.metadata.create_all(engine)
Path("/artifacts").mkdir(exist_ok=True)
IDENTITY_BATCH_LIMIT=20

def _resume_unfinished():
    """Requeue builds that were interrupted while the worker was down."""
    with SessionLocal() as db:
        pending=[v.id for v in db.query(IdentityVersion).filter_by(status=IdentityVersionStatus.BUILDING).order_by(IdentityVersion.id)]
    for version_id in pending:
        identity_queue.rpush("lift:identity",version_id)

def _run_job(db, raw_job_id):
    run=db.get(AnalysisRun,int(raw_job_id))
    if not run or run.status!=RunStatus.QUEUED: return
    run.status=RunStatus.RUNNING; db.commit()
    try:
        exp=db.get(Experiment,run.experiment_id)
        obs=db.query(Observation).filter_by(dataset_id=exp.dataset_id).all()
        run.result=run_analysis(db,exp,obs)
        run.status=RunStatus.COMPLETED
        Path(f"/artifacts/run-{run.id}.json").write_text(json.dumps(run.result,ensure_ascii=False))
        log(db,"worker","analysis.completed",run.id)
    except Exception as error:
        run.status=RunStatus.FAILED; run.error=str(error); traceback.print_exc()
    db.commit()

_resume_unfinished()
while True:
    Path("/artifacts/heartbeat").write_text(str(time.time()))
    identity_job=identity_queue.lpop("lift:identity")
    if identity_job:
        with SessionLocal() as db:
            outcome=process_version(db,int(identity_job),limit=IDENTITY_BATCH_LIMIT)
        # Yield between chunks so analysis jobs still drain; the build stays
        # resumable and unpublished until its final batch commits.
        if outcome.get("status")==IdentityVersionStatus.BUILDING:
            identity_queue.rpush("lift:identity",int(identity_job))
        # Take one analysis job per chunk instead of waiting for the build, so a
        # long regroup never starves a queued analysis.
        job=queue.lpop("lift:jobs")
        if not job: continue
    else:
        job=queue.blpop("lift:jobs",timeout=2)
        if not job: continue
    with SessionLocal() as db:
        _run_job(db,job[1])
