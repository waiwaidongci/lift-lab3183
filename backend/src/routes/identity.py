from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..controllers.dependencies import current_user, roles
from ..constants.roles import Role
from ..schemas.identity import LinkInput, VersionBuildInput
from ..services.identity_service import (
    identity_state, create_link, revoke_link, resume_version, enqueue_version,
)
router = APIRouter(prefix="/datasets/{dataset_id}/identity", tags=["identity"])


def _queue_build(version: dict):
    """Hand a building version to the worker; leave it resumable if Redis is down."""
    if version.get("status") != "building":
        return
    try:
        enqueue_version(version["id"])
    except Exception:
        pass


@router.get("")
def state(dataset_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    return identity_state(db, dataset_id)


@router.post("/links", status_code=201)
def link(dataset_id: int, data: LinkInput, db: Session = Depends(get_db),
         user=Depends(roles(Role.ADMIN, Role.ANALYST))):
    result = create_link(db, dataset_id, data.subject_a, data.subject_b, user.email, data.batch_size)
    _queue_build(result["version"])
    return result


@router.post("/links/{link_id}/revoke", status_code=201)
def revoke(dataset_id: int, link_id: int, data: VersionBuildInput | None = None,
           db: Session = Depends(get_db),
           user=Depends(roles(Role.ADMIN, Role.ANALYST))):
    batch_size = data.batch_size if data else 200
    result = revoke_link(db, dataset_id, link_id, user.email, batch_size)
    _queue_build(result["version"])
    return result


@router.post("/versions/{version_id}/resume")
def resume(dataset_id: int, version_id: int, db: Session = Depends(get_db),
           user=Depends(roles(Role.ADMIN, Role.ANALYST))):
    version = resume_version(db, dataset_id, version_id, user.email)
    _queue_build(version)
    return version


@router.get("/versions/{version_id}")
def version(dataset_id: int, version_id: int, db: Session = Depends(get_db),
            user=Depends(current_user)):
    state_payload = identity_state(db, dataset_id)
    for item in state_payload["versions"]:
        if item["id"] == version_id:
            return item
    raise HTTPException(404, "身份版本不存在")
