from fastapi import HTTPException
from sqlalchemy import func
from ..models import Dataset, Observation
from ..utils.serialize import record
from .identity_service import _published, resolve_person_map, person_level_observations


def _person_index(db, dataset_id):
    """subject -> (person key, group subjects, group arms) for the whole dataset.

    Built from the published identity version so the preview's merge/quarantine
    flags are correct even when a partner row sits outside the preview window.
    """
    version = _published(db, dataset_id)
    person_map = resolve_person_map(db, dataset_id, version)
    rows = db.query(Observation.subject, Observation.arm).filter_by(dataset_id=dataset_id).all()
    groups: dict[str, dict] = {}
    for subject, arm in rows:
        person = person_map.get(subject, subject)
        groups.setdefault(person, {"subjects": [], "arms": set()})
        groups[person]["subjects"].append(subject)
        groups[person]["arms"].add(arm)
    index = {}
    for person, group in groups.items():
        for subject in group["subjects"]:
            index[subject] = {
                "person_key": person,
                "group_subjects": sorted(group["subjects"]),
                "group_arms": sorted(group["arms"]),
            }
    return version, index


def list_datasets(db):
    return [dict(record(d), observations=db.query(Observation).filter_by(dataset_id=d.id).count()) for d in db.query(Dataset).order_by(Dataset.id)]


def observations(db, dataset_id, limit=None):
    if not db.get(Dataset,dataset_id): raise HTTPException(404,"数据集不存在")
    version, index = _person_index(db, dataset_id)
    rows = db.query(Observation).filter_by(dataset_id=dataset_id).order_by(Observation.id)
    if limit: rows = rows.limit(limit)
    out = []
    for row in rows:
        info = index.get(row.subject) or {
            "person_key": row.subject, "group_subjects": [row.subject], "group_arms": [row.arm]
        }
        out.append(dict(
            record(row),
            person_key=info["person_key"],
            identity_version=version.number if version else None,
            identity_version_id=version.id if version else None,
            merged=len(info["group_subjects"]) > 1,
            quarantined=len([a for a in info["group_arms"] if a in ("control", "treatment")]) > 1,
        ))
    return out


def observation_state(db, dataset_id):
    """Preview plus the merged/quarantined accounting of the whole dataset."""
    if not db.get(Dataset,dataset_id): raise HTTPException(404,"数据集不存在")
    version, person_map = _published(db, dataset_id), None
    if version:
        person_map = resolve_person_map(db, dataset_id, version)
    all_rows = db.query(Observation).filter_by(dataset_id=dataset_id).all()
    _, quarantine, stats = person_level_observations(all_rows, person_map or {})
    return {
        "dataset_id": dataset_id,
        "identity_version": version.number if version else None,
        "identity_version_id": version.id if version else None,
        "rows": observations(db, dataset_id, limit=100),
        "summary": stats,
        "quarantine": quarantine,
    }
