"""Anonymous identity linking, versioned publication and person-level grouping.

Core rules:
* Active links connect anonymous subjects into one person. Links are transitive.
* Every regrouping is written as an append-only identity version. A version is
  built in persisted batches; until all batches are done the previous published
  version stays authoritative. Publication flips the two versions atomically.
* Incremental mutations only touch the connected component they change. No other
  observation, assignment or link is scanned.
* Links are revoked, never deleted, and keep the version that added/removed them.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..constants.identity import IdentityVersionStatus as VersionStatus
from ..constants.identity import IdentityWorkStatus as WorkStatus
from ..models import (
    Dataset,
    IdentityAssignment,
    IdentityLink,
    IdentityVersion,
    IdentityWorkItem,
    Observation,
)
from ..utils.clock import now
from ..utils.serialize import record
from .audit_service import log

DEFAULT_BATCH_SIZE = 200


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #

def _published(db: Session, dataset_id: int) -> IdentityVersion | None:
    return (
        db.query(IdentityVersion)
        .filter_by(dataset_id=dataset_id, status=VersionStatus.PUBLISHED)
        .order_by(IdentityVersion.number.desc())
        .first()
    )


def _building(db: Session, dataset_id: int) -> IdentityVersion | None:
    return (
        db.query(IdentityVersion)
        .filter_by(dataset_id=dataset_id, status=VersionStatus.BUILDING)
        .order_by(IdentityVersion.number.desc())
        .first()
    )


def ensure_baseline_version(db: Session, dataset_id: int, actor: str = "system") -> IdentityVersion:
    """A published singleton-only baseline, created once per dataset."""
    existing = (
        db.query(IdentityVersion)
        .filter_by(dataset_id=dataset_id)
        .order_by(IdentityVersion.number.asc())
        .first()
    )
    if existing:
        return existing
    subjects = {
        subject
        for (subject,) in db.query(Observation.subject).filter_by(dataset_id=dataset_id)
    }
    version = IdentityVersion(
        dataset_id=dataset_id,
        number=1,
        status=VersionStatus.PUBLISHED,
        created_by=actor,
        published_at=now(),
        total_batches=0,
        completed_batches=0,
        summary={
            "people": len(subjects),
            "subjects": len(subjects),
            "changed_subjects": 0,
            "affected_subjects": 0,
            "note": "基线：每位匿名用户各自独立",
        },
    )
    try:
        # Concurrent registration/first analysis may race for v1; the savepoint
        # keeps the loser from poisoning the caller's transaction.
        with db.begin_nested():
            db.add(version)
            db.flush()
    except IntegrityError:
        version = (
            db.query(IdentityVersion)
            .filter_by(dataset_id=dataset_id, number=1)
            .one()
        )
        return version
    log(db, actor, "identity.version.published", f"{dataset_id}/v1")
    return version


def resolve_overrides(
    db: Session, dataset_id: int, version: IdentityVersion | None, subjects=None
) -> dict[str, str]:
    """Sparse subject -> person overrides recorded at or below ``version``.

    Only assignments belonging to a published (or since superseded) version
    count. A batch that is still building, or a build that failed halfway, must
    never leak into what readers see: the previous complete version stays
    authoritative until publication flips it atomically.
    """
    if not version:
        return {}
    query = (
        db.query(IdentityAssignment.subject, IdentityAssignment.person_key)
        .join(IdentityVersion, IdentityAssignment.version_id == IdentityVersion.id)
        .filter(
            IdentityAssignment.dataset_id == dataset_id,
            IdentityVersion.number <= version.number,
            IdentityVersion.status.in_(
                (VersionStatus.PUBLISHED, VersionStatus.SUPERSEDED)
            ),
        )
    )
    if subjects is not None:
        subjects = list(subjects)
        if not subjects:
            return {}
        query = query.filter(IdentityAssignment.subject.in_(subjects))
    rows = query.order_by(IdentityVersion.number.asc(), IdentityAssignment.id.asc()).all()
    return {subject: key for subject, key in rows}


def resolve_person_map(
    db: Session, dataset_id: int, version: IdentityVersion | None
) -> dict[str, str]:
    """Complete subject -> person map for every subject in the dataset."""
    if not version:
        return {}
    subjects = [
        subject
        for (subject,) in db.query(Observation.subject).filter_by(dataset_id=dataset_id)
    ]
    mapping = {subject: subject for subject in subjects}
    mapping.update(resolve_overrides(db, dataset_id, version))
    return mapping


def resolve_subject_map(
    db: Session,
    dataset_id: int,
    subjects,
    version: IdentityVersion | None,
) -> dict[str, str]:
    """Base person keys for the given subjects only (incremental rebuilds)."""
    if not version or not subjects:
        return {}
    return resolve_overrides(db, dataset_id, version, subjects)


def pinned_version(db: Session, experiment_id: int) -> IdentityVersion | None:
    from ..models import ExperimentIdentity

    pin = db.query(ExperimentIdentity).filter_by(experiment_id=experiment_id).first()
    if not pin:
        return None
    return db.get(IdentityVersion, pin.version_id)


def pin_version(db: Session, experiment_id: int, version: IdentityVersion | None, actor: str) -> None:
    from ..models import ExperimentIdentity

    if not version:
        return
    db.add(
        ExperimentIdentity(
            experiment_id=experiment_id,
            version_id=version.id,
            pinned_by=actor,
            pinned_at=now(),
        )
    )


# --------------------------------------------------------------------------- #
# Link graph (component-scoped queries)
# --------------------------------------------------------------------------- #

def _neighbours(db: Session, dataset_id: int, subjects, exclude_link_id: int | None = None):
    query = db.query(IdentityLink).filter(
        IdentityLink.dataset_id == dataset_id,
        IdentityLink.revoked_at.is_(None),
        or_(IdentityLink.subject_a.in_(list(subjects)), IdentityLink.subject_b.in_(list(subjects))),
    )
    if exclude_link_id is not None:
        query = query.filter(IdentityLink.id != exclude_link_id)
    adjacency = defaultdict(set)
    for link in query:
        adjacency[link.subject_a].add(link.subject_b)
        adjacency[link.subject_b].add(link.subject_a)
    return adjacency


def component_of(
    db: Session,
    dataset_id: int,
    start: str,
    exclude_link_id: int | None = None,
) -> set[str]:
    """BFS over links only; cost is bounded by the component, not the dataset."""
    seen = {start}
    frontier = {start}
    while frontier:
        adjacency = _neighbours(db, dataset_id, frontier, exclude_link_id)
        discovered = {
            other
            for node in frontier
            for other in adjacency.get(node, ())
            if other not in seen
        }
        if not discovered:
            break
        seen |= discovered
        frontier = discovered
    return seen


def _components_within(db: Session, dataset_id: int, subjects: set[str], exclude_link_id=None):
    adjacency = _neighbours(db, dataset_id, subjects, exclude_link_id)
    components = []
    unvisited = set(subjects)
    while unvisited:
        start = min(unvisited)
        stack = [start]
        component = {start}
        unvisited.discard(start)
        while stack:
            node = stack.pop()
            for other in adjacency.get(node, ()):
                if other in unvisited:
                    unvisited.discard(other)
                    component.add(other)
                    stack.append(other)
        components.append(component)
    return components


def active_groups(db: Session, dataset_id: int) -> list[dict]:
    """Current *published* people (only groups of two or more are returned).

    Derived from the published assignment map, not from the raw link table, so
    a link whose version is still building does not leak into the observation
    page until it is published.
    """
    version = _published(db, dataset_id)
    if not version:
        return []
    mapping = resolve_person_map(db, dataset_id, version)
    members: dict[str, set[str]] = defaultdict(set)
    for subject, key in mapping.items():
        members[key].add(subject)
    groups = []
    for key in sorted(members):
        subjects = members[key]
        if len(subjects) < 2:
            continue
        arms = sorted(
            {
                arm
                for (arm,) in db.query(Observation.arm).filter(
                    Observation.dataset_id == dataset_id,
                    Observation.subject.in_(list(subjects)),
                )
            }
        )
        cross_arm = len([arm for arm in arms if arm in ("control", "treatment")]) > 1
        groups.append(
            {
                "person_key": key,
                "subjects": sorted(subjects),
                "arms": arms,
                "cross_arm": cross_arm,
                "reason": QUARANTINE_REASON if cross_arm else None,
            }
        )
    return groups


# --------------------------------------------------------------------------- #
# Builds
# --------------------------------------------------------------------------- #

def _open_build(
    db: Session,
    dataset_id: int,
    actor: str,
    affected: set[str],
    key_for_subject,
    note: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> IdentityVersion:
    """Create the next identity version for exactly the affected subjects.

    Work items are persisted before the transaction commits, so an interrupted
    worker can resume. Zero-change builds publish immediately (there is no
    half-built data to expose).
    """
    ensure_baseline_version(db, dataset_id, actor)
    base = _published(db, dataset_id)
    base_keys = resolve_subject_map(db, dataset_id, affected, base)
    targets = {subject: key_for_subject(subject) for subject in affected}
    changed = {
        subject: key
        for subject, key in targets.items()
        if base_keys.get(subject, subject) != key
    }
    last_number = (
        db.query(IdentityVersion.number)
        .filter_by(dataset_id=dataset_id)
        .order_by(IdentityVersion.number.desc())
        .limit(1)
        .scalar()
        or 0
    )
    version = IdentityVersion(
        dataset_id=dataset_id,
        number=last_number + 1,
        status=VersionStatus.BUILDING,
        base_version_id=base.id if base else None,
        created_by=actor,
        batch_size=max(1, int(batch_size)),
        total_batches=0,
        completed_batches=0,
        summary={
            "note": note,
            "affected_subjects": len(affected),
            "changed_subjects": len(changed),
        },
    )
    db.add(version)
    db.flush()

    ordered = sorted(changed.items())
    size = version.batch_size
    chunks = [ordered[i : i + size] for i in range(0, len(ordered), size)]
    version.total_batches = len(chunks)
    for sequence, chunk in enumerate(chunks):
        db.add(
            IdentityWorkItem(
                version_id=version.id,
                sequence=sequence,
                subjects=[subject for subject, _ in chunk],
                groups=[[subject, key] for subject, key in chunk],
                status=WorkStatus.PENDING,
            )
        )
    if not chunks:
        _publish(db, version, base, actor)
    else:
        log(db, actor, "identity.version.building", f"{dataset_id}/v{version.number}")
    db.flush()
    return version


def _publish(db: Session, version: IdentityVersion, base: IdentityVersion | None, actor: str) -> None:
    if base and base.id != version.id and base.status == VersionStatus.PUBLISHED:
        base.status = VersionStatus.SUPERSEDED
        base.superseded_at = now()
    version.status = VersionStatus.PUBLISHED
    version.published_at = now()
    log(db, actor, "identity.version.published", f"{version.dataset_id}/v{version.number}")


def _require_dataset(db: Session, dataset_id: int) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    return dataset


def _commit_or_conflict(db: Session) -> None:
    """Unique indexes guard against racing writers; report that as a retryable 409."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "身份关联同时被其他会话修改，请刷新后重试")


def create_link(
    db: Session,
    dataset_id: int,
    subject_a: str,
    subject_b: str,
    actor: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    _require_dataset(db, dataset_id)
    a, b = sorted((subject_a.strip(), subject_b.strip()))
    if not a or not b:
        raise HTTPException(422, "两个匿名标识都不能为空")
    if a == b:
        raise HTTPException(422, "不能把匿名标识关联到自身")
    known = {
        subject
        for (subject,) in db.query(Observation.subject).filter(
            Observation.dataset_id == dataset_id,
            Observation.subject.in_([a, b]),
        )
    }
    missing = [s for s in (a, b) if s not in known]
    if missing:
        raise HTTPException(404, f"数据集中不存在匿名标识：{'、'.join(missing)}")
    if _building(db, dataset_id):
        raise HTTPException(409, "已有身份版本正在分批构建，请等待发布后再修改关联")

    duplicate = (
        db.query(IdentityLink)
        .filter_by(dataset_id=dataset_id, subject_a=a, subject_b=b)
        .filter(IdentityLink.revoked_at.is_(None))
        .first()
    )
    if duplicate:
        raise HTTPException(409, "这两个匿名标识已经关联")

    component_a = component_of(db, dataset_id, a)
    component_b = component_of(db, dataset_id, b)
    affected = component_a | component_b
    person_key = min(affected)
    link = IdentityLink(
        dataset_id=dataset_id,
        subject_a=a,
        subject_b=b,
        created_by=actor,
        created_at=now(),
    )
    db.add(link)
    db.flush()

    version = _open_build(
        db,
        dataset_id,
        actor,
        affected,
        lambda subject: person_key,
        f"关联 {a} 与 {b}；影响 {len(affected)} 位匿名标识",
        batch_size,
    )
    link.added_version_id = version.id
    log(db, actor, "identity.link.create", f"{dataset_id}/{a}+{b}")
    _commit_or_conflict(db)
    db.refresh(link)
    return {
        "link": record(link),
        "version": _version_summary(db, version),
        "affected_subjects": len(affected),
        "changed_subjects": int((version.summary or {}).get("changed_subjects") or 0),
        "groups": _group_payload({subject: person_key for subject in affected}),
    }


def revoke_link(db: Session, dataset_id: int, link_id: int, actor: str, batch_size: int = DEFAULT_BATCH_SIZE) -> dict:
    _require_dataset(db, dataset_id)
    link = db.get(IdentityLink, link_id)
    if not link or link.dataset_id != dataset_id:
        raise HTTPException(404, "关联不存在")
    if link.revoked_at:
        raise HTTPException(409, "该关联已经撤销")
    if _building(db, dataset_id):
        raise HTTPException(409, "已有身份版本正在分批构建，请等待发布后再修改关联")

    # Only the connected people that contain this edge are recomputed.
    old_component = component_of(db, dataset_id, link.subject_a)
    link.revoked_at = now()
    link.revoked_by = actor
    db.flush()

    targets: dict[str, str] = {}
    for component in _components_within(db, dataset_id, old_component, exclude_link_id=link.id):
        key = min(component)
        for subject in component:
            targets[subject] = key

    version = _open_build(
        db,
        dataset_id,
        actor,
        set(targets),
        targets.__getitem__,
        f"撤销 {link.subject_a} 与 {link.subject_b}；重算 {len(targets)} 位匿名标识",
        batch_size,
    )
    link.revoked_version_id = version.id
    log(db, actor, "identity.link.revoke", f"{dataset_id}/{link.subject_a}-{link.subject_b}")
    _commit_or_conflict(db)
    db.refresh(link)
    return {
        "link": record(link),
        "version": _version_summary(db, version),
        "affected_subjects": len(targets),
        "changed_subjects": int((version.summary or {}).get("changed_subjects") or 0),
        "groups": _group_payload(targets),
    }


def _group_payload(targets: dict[str, str]) -> list[dict]:
    """Grouping produced for the affected people only."""
    people = defaultdict(list)
    for subject, key in targets.items():
        people[key].append(subject)
    return [
        {"person_key": key, "subjects": sorted(subjects)}
        for key, subjects in sorted(people.items())
        if len(subjects) > 1
    ]


# --------------------------------------------------------------------------- #
# Batched processing
# --------------------------------------------------------------------------- #

def process_version(db: Session, version_id: int, limit: int | None = None) -> dict:
    """Process pending batches one by one; each batch commits atomically.

    Safe to call repeatedly: it resumes at the first pending item and publishes
    only after every batch is done.
    """
    version = db.get(IdentityVersion, version_id)
    if not version:
        return {"version_id": version_id, "status": "missing", "processed": 0}
    processed = 0
    while limit is None or processed < limit:
        db.refresh(version)
        if version.status != VersionStatus.BUILDING:
            break
        item = (
            db.query(IdentityWorkItem)
            .filter_by(version_id=version.id, status=WorkStatus.PENDING)
            .order_by(IdentityWorkItem.sequence.asc())
            .first()
        )
        if not item:
            base = db.get(IdentityVersion, version.base_version_id) if version.base_version_id else None
            _publish(db, version, base, "worker")
            db.commit()
            break
        try:
            # Claim the batch atomically inside the same transaction that writes
            # its rows: a concurrent worker gets rowcount 0 and skips it, while a
            # crash rolls the claim back with the rows. No half-applied batch.
            claimed = (
                db.query(IdentityWorkItem)
                .filter_by(id=item.id, status=WorkStatus.PENDING)
                .update({IdentityWorkItem.status: WorkStatus.DONE}, synchronize_session=False)
            )
            if not claimed:
                db.rollback()
                processed += 1
                continue
            for subject, person_key in item.groups or []:
                db.add(
                    IdentityAssignment(
                        dataset_id=version.dataset_id,
                        subject=subject,
                        person_key=person_key,
                        version_id=version.id,
                    )
                )
            item.completed_at = now()
            item.changed_subjects = len(item.groups or [])
            version.completed_batches = int(version.completed_batches or 0) + 1
            db.flush()
            if version.completed_batches >= version.total_batches:
                base = db.get(IdentityVersion, version.base_version_id) if version.base_version_id else None
                _publish(db, version, base, "worker")
            db.commit()
        except Exception as error:  # keep the old version published
            db.rollback()
            version = db.get(IdentityVersion, version_id)
            item = db.get(IdentityWorkItem, item.id)
            version.status = VersionStatus.FAILED
            version.error = str(error)
            item.status = WorkStatus.FAILED
            item.error = str(error)
            # Earlier batches of this build already committed; remove them so a
            # failed build can never be replayed by a later version number.
            db.query(IdentityAssignment).filter_by(version_id=version.id).delete()
            _rollback_links(db, version)
            log(db, "worker", "identity.version.failed", f"{version.dataset_id}/v{version.number}")
            db.commit()
            return {"version_id": version_id, "status": "failed", "processed": processed}
        processed += 1
    version = db.get(IdentityVersion, version_id)
    return {
        "version_id": version_id,
        "status": version.status if version else "missing",
        "processed": processed,
        "completed_batches": version.completed_batches if version else 0,
        "total_batches": version.total_batches if version else 0,
    }


def _rollback_links(db: Session, version: IdentityVersion) -> None:
    """Re-align the link graph with the still-published assignment map.

    A link created by the failed build is marked revoked (never deleted, so the
    attempt stays auditable); a revoke made by it is undone.
    """
    created = (
        db.query(IdentityLink)
        .filter_by(dataset_id=version.dataset_id, added_version_id=version.id)
        .all()
    )
    for link in created:
        if not link.revoked_at:
            link.revoked_at = now()
            link.revoked_by = "worker"
            link.revoked_version_id = version.id
    revoked = (
        db.query(IdentityLink)
        .filter_by(dataset_id=version.dataset_id, revoked_version_id=version.id)
        .filter(IdentityLink.added_version_id != version.id)
        .all()
    )
    for link in revoked:
        link.revoked_at = None
        link.revoked_by = None
        link.revoked_version_id = None


def enqueue_version(version_id: int) -> None:
    from .run_service import identity_queue

    identity_queue.rpush("lift:identity", version_id)


def resume_version(db: Session, dataset_id: int, version_id: int, actor: str) -> dict:
    _require_dataset(db, dataset_id)
    version = db.get(IdentityVersion, version_id)
    if not version or version.dataset_id != dataset_id:
        raise HTTPException(404, "身份版本不存在")
    if version.status == VersionStatus.BUILDING:
        # A nudge only; the worker also requeues every building version on boot,
        # so a Redis outage never strands a half-built version.
        try:
            enqueue_version(version.id)
        except Exception:
            pass
        log(db, actor, "identity.version.resume", f"{dataset_id}/v{version.number}")
        db.commit()
    return _version_summary(db, version)


# --------------------------------------------------------------------------- #
# State for the observation page
# --------------------------------------------------------------------------- #

def _version_summary(db: Session, version: IdentityVersion) -> dict:
    return {
        **record(version),
        "pending_batches": db.query(IdentityWorkItem)
        .filter_by(version_id=version.id, status=WorkStatus.PENDING)
        .count(),
        "is_current": version.status == VersionStatus.PUBLISHED,
    }


def identity_state(db: Session, dataset_id: int) -> dict:
    _require_dataset(db, dataset_id)
    published = _published(db, dataset_id)
    building = _building(db, dataset_id)
    links = (
        db.query(IdentityLink)
        .filter_by(dataset_id=dataset_id)
        .order_by(IdentityLink.id.asc())
        .all()
    )
    versions = (
        db.query(IdentityVersion)
        .filter_by(dataset_id=dataset_id)
        .order_by(IdentityVersion.number.desc())
        .all()
    )
    return {
        "dataset_id": dataset_id,
        "published_version": _version_summary(db, published) if published else None,
        "building_version": _version_summary(db, building) if building else None,
        "links": [record(link) for link in links],
        "active_link_count": len([link for link in links if not link.revoked_at]),
        "groups": active_groups(db, dataset_id),
        "versions": [_version_summary(db, version) for version in versions],
    }


# --------------------------------------------------------------------------- #
# Person-level analysis input
# --------------------------------------------------------------------------- #

@dataclass
class PersonObservation:
    subject: str
    arm: str
    pre_value: float
    value: float
    subjects: list[str] = field(default_factory=list)


QUARANTINE_REASON = "同一匿名身份同时出现在对照组与候选组，整人隔离且不参与合并"


def person_level_observations(observations, person_map: dict[str, str]):
    """Merge same-person same-arm income; quarantine any cross-arm person whole."""
    people: dict[str, list] = defaultdict(list)
    for observation in observations:
        people[person_map.get(observation.subject, observation.subject)].append(observation)

    rows: list[PersonObservation] = []
    quarantine: list[dict] = []
    merged: list[dict] = []
    merged_people = 0
    merged_observations = 0
    for person_key in sorted(people):
        group = people[person_key]
        arms = sorted({row.arm for row in group})
        subjects = sorted({row.subject for row in group})
        if len(arms) > 1:
            quarantine.append(
                {
                    "person_key": person_key,
                    "subjects": subjects,
                    "arms": arms,
                    "observations": len(group),
                    "reason": QUARANTINE_REASON,
                }
            )
            continue
        if len(subjects) > 1:
            merged_people += 1
        merged_observations += len(group) - 1
        person = PersonObservation(
            subject=person_key,
            arm=arms[0],
            pre_value=float(sum(row.pre_value for row in group)),
            value=float(sum(row.value for row in group)),
            subjects=subjects,
        )
        rows.append(person)
        if len(subjects) > 1:
            merged.append(
                {
                    "person_key": person_key,
                    "subjects": subjects,
                    "arm": person.arm,
                    "pre_value": person.pre_value,
                    "value": person.value,
                }
            )

    stats = {
        "observations": len(observations),
        "subjects": len({row.subject for row in observations}),
        "people": len(rows),
        "merged_people": merged_people,
        "merged_observations": merged_observations,
        "merged_people_detail": merged,
        "quarantined_people": len(quarantine),
        "quarantined_observations": sum(item["observations"] for item in quarantine),
        "quarantine": quarantine,
    }
    return rows, quarantine, stats
