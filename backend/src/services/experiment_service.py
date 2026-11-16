from fastapi import HTTPException
from ..models import Experiment, Dataset
from ..utils.serialize import record
from .audit_service import log
from .identity_service import ensure_baseline_version, _published, pin_version, _version_summary, pinned_version
from sqlalchemy.orm import Session


def _with_pin(db: Session, row: Experiment) -> dict:
    version = pinned_version(db, row.id)
    return {**record(row), "identity_version": _version_summary(db, version) if version else None}


def list_experiments(db: Session):
    return [_with_pin(db, x) for x in db.query(Experiment).order_by(Experiment.id.desc())]


def create_experiment(db: Session, data, user):
    if not db.get(Dataset, data.dataset_id): raise HTTPException(404, "数据集不存在")
    if db.query(Experiment).filter_by(name=data.name).first(): raise HTTPException(409, "实验名称已存在")
    row = Experiment(**data.model_dump(), owner=user.name)
    db.add(row)
    db.flush()
    # Pin the latest *published* identity version. A build in progress is
    # deliberately ignored so a new experiment never freezes half a regroup.
    ensure_baseline_version(db, data.dataset_id, user.email)
    version = _published(db, data.dataset_id)
    pin_version(db, row.id, version, user.email)
    log(db, user.email, "experiment.create", row.id)
    db.commit()
    return _with_pin(db, row)
