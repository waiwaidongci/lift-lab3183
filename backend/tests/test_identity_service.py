import pytest

from src.models import (
    IdentityAssignment,
    IdentityLink,
    IdentityVersion,
    IdentityWorkItem,
)
from src.services import identity_service as ids
from src.services.analysis_service import run_analysis
from src.services.identity_service import (
    active_groups,
    create_link,
    process_version,
    resolve_person_map,
    revoke_link,
)
from src.models import Observation


def _publish_all(db, version):
    result = process_version(db, version["id"])
    assert result["status"] == "published"
    return db.get(IdentityVersion, version["id"])


def _link(db, dataset, a, b, batch_size=200):
    return create_link(db, dataset.id, a, b, "analyst@test", batch_size)


def _family(db, dataset, version_too=True):
    created = _link(db, dataset, "S0", "S1")
    if version_too:
        _publish_all(db, created["version"])
    return created


def _map(db, dataset):
    version = ids._published(db, dataset.id)
    return resolve_person_map(db, dataset.id, version)


# --------------------------------------------------------------------------- #
# Transitive merging and person-level aggregation
# --------------------------------------------------------------------------- #

def test_links_are_transitive(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S1")["version"])
    _publish_all(db, _link(db, dataset, "S1", "S2")["version"])
    mapping = _map(db, dataset)
    assert mapping["S0"] == mapping["S1"] == mapping["S2"] == "S0"

    created = _link(db, dataset, "S2", "S3")
    # Only S3 actually changes; S0..S2 keep their person key.
    assert created["affected_subjects"] == 4
    assert created["changed_subjects"] == 1
    assert created["version"]["total_batches"] == 1
    _publish_all(db, created["version"])
    mapping = _map(db, dataset)
    assert mapping["S0"] == mapping["S1"] == mapping["S2"] == mapping["S3"] == "S0"
    assert active_groups(db, dataset.id)[0]["subjects"] == ["S0", "S1", "S2", "S3"]


def test_same_person_same_arm_income_is_summed(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S2")["version"])  # both control
    version = ids._published(db, dataset.id)
    observations = db.query(Observation).filter_by(dataset_id=dataset.id).all()
    person_map = resolve_person_map(db, dataset.id, version)
    rows, quarantine, stats = ids.person_level_observations(observations, person_map)

    merged = [row for row in rows if row.subject == "S0"][0]
    assert merged.arm == "control"
    assert merged.value == pytest.approx(10.0 + 12.0)
    assert merged.pre_value == pytest.approx(1.0 + 3.0)
    assert merged.subjects == ["S0", "S2"]
    assert stats["people"] == 9
    assert stats["merged_people"] == 1
    assert stats["merged_observations"] == 1
    assert quarantine == []
    assert [row.subject for row in rows].count("S0") == 1
    assert "S2" not in [row.subject for row in rows]


def test_cross_arm_person_is_quarantined_whole(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    # S0 control, S1 treatment -> cross-arm
    result = _link(db, dataset, "S0", "S1", batch_size=1)
    _publish_all(db, result["version"])
    version = ids._published(db, dataset.id)
    observations = db.query(Observation).filter_by(dataset_id=dataset.id).all()
    rows, quarantine, stats = ids.person_level_observations(
        observations, resolve_person_map(db, dataset.id, version)
    )
    assert stats["quarantined_people"] == 1
    assert stats["quarantined_observations"] == 2
    assert quarantine[0]["subjects"] == ["S0", "S1"]
    assert quarantine[0]["arms"] == ["control", "treatment"]
    assert "整人隔离" in quarantine[0]["reason"]
    assert stats["people"] == 8
    assert {"S0", "S1"}.isdisjoint({row.subject for row in rows})
    assert stats["merged_observations"] == 0

    groups = active_groups(db, dataset.id)
    assert groups[0]["cross_arm"] is True
    assert groups[0]["reason"]


# --------------------------------------------------------------------------- #
# Revocation semantics
# --------------------------------------------------------------------------- #

def test_revoking_cycle_edge_keeps_grouping_and_writes_no_assignments(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    first = _link(db, dataset, "S0", "S1")
    ab = first["link"]
    _publish_all(db, first["version"])
    _publish_all(db, _link(db, dataset, "S1", "S2")["version"])
    # Close the cycle S0-S1-S2-S0.
    cycle = _link(db, dataset, "S2", "S0")
    _publish_all(db, cycle["version"])
    mapping = _map(db, dataset)
    assert mapping["S0"] == mapping["S1"] == mapping["S2"] == "S0"

    before_assignments = db.query(IdentityAssignment).count()
    result = revoke_link(db, dataset.id, ab["id"], "analyst@test")
    version = db.get(IdentityVersion, result["version"]["id"])
    process_version(db, version.id)
    db.refresh(version)
    assert version.status == "published"
    assert version.summary["changed_subjects"] == 0
    assert db.query(IdentityAssignment).count() == before_assignments
    after = _map(db, dataset)
    assert after["S0"] == after["S1"] == after["S2"] == "S0"
    assert active_groups(db, dataset.id)[0]["subjects"] == ["S0", "S1", "S2"]
    assert db.get(IdentityLink, ab["id"]).revoked_at is not None
    assert db.get(IdentityLink, ab["id"]).revoked_version_id == version.id


def test_revoking_bridge_splits_the_people(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S2")["version"])  # both control
    bridge = _link(db, dataset, "S2", "S4")["link"]  # S4 control too
    process_version(db, db.query(IdentityVersion).order_by(IdentityVersion.number.desc()).first().id)
    assert _map(db, dataset)["S0"] == _map(db, dataset)["S4"] == "S0"

    result = revoke_link(db, dataset.id, bridge["id"], "analyst@test")
    process_version(db, result["version"]["id"])
    mapping = _map(db, dataset)
    assert mapping["S0"] == mapping["S2"] == "S0"
    assert mapping["S4"] == "S4"
    version = db.get(IdentityVersion, result["version"]["id"])
    assert version.summary["changed_subjects"] == 1  # S4 reverts; S0/S2 unchanged
    assert version.summary["affected_subjects"] == 3


def test_revoke_only_rewrites_the_affected_component(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    # Two disjoint people: S0+S1 and S4+S5.
    _publish_all(db, _link(db, dataset, "S0", "S1")["version"])
    _publish_all(db, _link(db, dataset, "S4", "S5")["version"])
    untouched_version = ids._published(db, dataset.id)
    untouched = db.query(IdentityAssignment).filter_by(version_id=untouched_version.id).all()
    untouched_subjects = {row.subject for row in untouched}
    assert untouched_subjects == {"S5"}

    other = _link(db, dataset, "S2", "S3")["version"]
    process_version(db, other["id"])
    target = db.query(IdentityLink).filter_by(subject_a="S2", subject_b="S3").first()
    before = db.query(IdentityAssignment).count()
    result = revoke_link(db, dataset.id, target.id, "analyst@test")
    version = db.get(IdentityVersion, result["version"]["id"])
    process_version(db, version.id)
    written = db.query(IdentityAssignment).filter_by(version_id=version.id).all()
    # Only S3 reverts to itself; S2 stays the person key and needs no rewrite.
    assert {row.subject for row in written} == {"S3"}
    assert db.query(IdentityAssignment).count() == before + 1
    assert untouched_subjects == {"S5"}
    # The unrelated pair still maps to its own person.
    mapping = _map(db, dataset)
    assert mapping["S4"] == mapping["S5"] == "S4"
    assert mapping["S2"] == "S2" and mapping["S3"] == "S3"


def test_cannot_revoke_twice_or_link_duplicate(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    created = _link(db, dataset, "S0", "S1")
    process_version(db, created["version"]["id"])
    with pytest.raises(Exception) as duplicate:
        _link(db, dataset, "S1", "S0")
    assert "已经关联" in str(duplicate.value)
    revoke_link(db, dataset.id, created["link"]["id"], "analyst@test")
    with pytest.raises(Exception) as twice:
        revoke_link(db, dataset.id, created["link"]["id"], "analyst@test")
    assert "已经撤销" in str(twice.value)


# --------------------------------------------------------------------------- #
# Batched builds, interruption and publication
# --------------------------------------------------------------------------- #

def test_interrupted_build_keeps_old_version_until_resumed(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S2")["version"])
    _publish_all(db, _link(db, dataset, "S4", "S6")["version"])
    old = ids._published(db, dataset.id)
    assert resolve_person_map(db, dataset.id, old)["S4"] == "S4"

    # Bridging the two people rewrites S4 and S6, one subject per batch.
    result = _link(db, dataset, "S2", "S4", batch_size=1)
    version_id = result["version"]["id"]
    assert result["version"]["status"] == "building"
    assert result["version"]["total_batches"] == 2

    # Simulate a worker interruption after the first batch.
    first = process_version(db, version_id, limit=1)
    assert first["status"] == "building"
    assert first["processed"] == 1
    building = db.get(IdentityVersion, version_id)
    assert building.completed_batches == 1

    # Until every batch is done, readers still see the complete old version.
    published = ids._published(db, dataset.id)
    assert published.id == old.id
    assert resolve_person_map(db, dataset.id, published)["S4"] == "S4"
    assert resolve_person_map(db, dataset.id, published)["S6"] == "S4"

    # Resume completes the remaining batches and only then flips the version.
    final = process_version(db, version_id)
    assert final["status"] == "published"
    db.refresh(building)
    assert building.completed_batches == building.total_batches
    assert ids._published(db, dataset.id).id == version_id
    assert resolve_person_map(db, dataset.id, ids._published(db, dataset.id))["S4"] == "S0"


def test_signalling_while_building_is_rejected(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S1")["version"])
    _link(db, dataset, "S1", "S2", batch_size=1)  # stays building
    with pytest.raises(Exception) as error:
        _link(db, dataset, "S3", "S4")
    assert "正在分批构建" in str(error.value)


def test_zero_change_build_publishes_immediately(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S2")["version"])
    _publish_all(db, _link(db, dataset, "S2", "S4")["version"])
    base = ids._published(db, dataset.id)
    # Closing the cycle changes no person key, so there is nothing to batch.
    result = _link(db, dataset, "S4", "S0")
    assert result["changed_subjects"] == 0
    assert result["version"]["status"] == "published"
    assert ids._published(db, dataset.id).id != base.id


# --------------------------------------------------------------------------- #
# Experiment pinning and analysis
# --------------------------------------------------------------------------- #

def test_experiment_pins_version_at_registration(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S1")["version"])
    pinned = ids._published(db, dataset.id)

    from src.models import Experiment
    from src.schemas.experiment import ExperimentInput
    from src.services.experiment_service import create_experiment

    experiment = create_experiment(
        db,
        ExperimentInput(name="pinned", dataset_id=dataset.id, hypothesis="至少八个字的假设内容", cuped=False),
        seeded["analyst"],
    )
    assert experiment["identity_version"]["id"] == pinned.id
    assert ids.pinned_version(db, experiment["id"]).id == pinned.id


def test_experiment_registered_during_build_pins_published_version(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S1")["version"])
    published = ids._published(db, dataset.id)
    building = _link(db, dataset, "S1", "S2", batch_size=1)["version"]
    assert building["status"] == "building"

    from src.schemas.experiment import ExperimentInput
    from src.services.experiment_service import create_experiment

    experiment = create_experiment(
        db,
        ExperimentInput(name="mid-build", dataset_id=dataset.id, hypothesis="构建期间登记的假设", cuped=False),
        seeded["analyst"],
    )
    assert experiment["identity_version"]["id"] == published.id
    assert experiment["identity_version"]["status"] == "published"


def test_analysis_uses_pinned_version_not_newer_published(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S2")["version"])

    from src.models import Experiment
    from src.services.experiment_service import create_experiment
    from src.schemas.experiment import ExperimentInput

    experiment_payload = create_experiment(
        db,
        ExperimentInput(name="stable", dataset_id=dataset.id, hypothesis="固定版本后新增关联", cuped=False),
        seeded["analyst"],
    )
    experiment = db.get(Experiment, experiment_payload["id"])

    # A newer publish happens after registration.
    _publish_all(db, _link(db, dataset, "S4", "S6")["version"])
    assert ids._published(db, dataset.id).number > ids.pinned_version(db, experiment.id).number

    observations = db.query(Observation).filter_by(dataset_id=dataset.id).all()
    result = run_analysis(db, experiment, observations)
    identity = result["identity"]
    # The experiment still sees S4 and S6 as separate people.
    assert identity["people"] == 9
    assert identity["merged_people"] == 1
    assert identity["version_summary"]["id"] == ids.pinned_version(db, experiment.id).id

    # Person counts feed the statistics directly.
    assert result["raw"]["n_control"] + result["raw"]["n_treatment"] == identity["people"]


# --------------------------------------------------------------------------- #
# Failed builds never leak partial data
# --------------------------------------------------------------------------- #

def test_partial_batches_are_invisible_before_publish(seeded):
    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S2")["version"])
    _publish_all(db, _link(db, dataset, "S4", "S6")["version"])
    result = _link(db, dataset, "S2", "S4", batch_size=1)
    version_id = result["version"]["id"]
    process_version(db, version_id, limit=1)  # S4 now written, build unfinished

    written = db.query(IdentityAssignment).filter_by(version_id=version_id).all()
    assert len(written) == 1
    assert written[0].subject == "S4"

    # The half-written assignment is not visible to readers.
    mapping = resolve_person_map(db, dataset.id, ids._published(db, dataset.id))
    assert mapping["S4"] == "S4"
    assert mapping["S6"] == "S4"


def test_failed_build_keeps_old_version_and_discards_partial_rows(seeded, monkeypatch):
    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S2")["version"])
    old = ids._published(db, dataset.id)
    result = _link(db, dataset, "S2", "S4", batch_size=1)
    version_id = result["version"]["id"]

    calls = {"n": 0}
    real_now = ids.now

    def flaky_now():
        calls["n"] += 1
        if calls["n"] == 2:  # first batch commits, second batch fails
            raise RuntimeError("磁盘写入失败")
        return real_now()

    monkeypatch.setattr(ids, "now", flaky_now)
    outcome = process_version(db, version_id)
    assert outcome["status"] == "failed"

    version = db.get(IdentityVersion, version_id)
    assert version.status == "failed"
    assert "磁盘写入失败" in version.error
    # The first batch had committed, but the failed build's rows are removed so
    # a later version number can never replay them.
    assert db.query(IdentityAssignment).filter_by(version_id=version_id).count() == 0
    published = ids._published(db, dataset.id)
    assert published.id == old.id
    mapping = resolve_person_map(db, dataset.id, published)
    assert mapping["S0"] == mapping["S2"] == "S0"
    assert mapping["S4"] == "S4"


def test_revoke_does_not_scan_observations_or_other_people(seeded):
    """Revocation is scoped to the affected component's links/assignments."""
    from sqlalchemy import event

    db, dataset = seeded["db"], seeded["dataset"]
    _publish_all(db, _link(db, dataset, "S0", "S2")["version"])
    _publish_all(db, _link(db, dataset, "S4", "S6")["version"])
    target = db.query(IdentityLink).filter_by(subject_a="S4", subject_b="S6").first()

    statements = []
    engine = db.get_bind()

    def listen(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", listen)
    try:
        result = revoke_link(db, dataset.id, target.id, "analyst@test")
    finally:
        event.remove(engine, "before_cursor_execute", listen)

    touched_observations = [s for s in statements if "observations" in s]
    assert touched_observations == []
    # Assignments are read/written only for the affected subjects.
    for statement in statements:
        if "identity_assignments" in statement and statement.startswith("select"):
            assert " in " in statement
    version = db.get(IdentityVersion, result["version"]["id"])
    process_version(db, version.id)
    mapping = _map(db, dataset)
    assert mapping["S0"] == mapping["S2"] == "S0"  # other people untouched
    assert mapping["S4"] == "S4" and mapping["S6"] == "S6"  # bridge removed


def test_failed_build_restores_link_graph(seeded, monkeypatch):
    db, dataset = seeded["db"], seeded["dataset"]
    first = _link(db, dataset, "S0", "S2")
    _publish_all(db, first["version"])
    _publish_all(db, _link(db, dataset, "S4", "S6")["version"])

    # A bridging link succeeds structurally but its build fails.
    result = _link(db, dataset, "S2", "S4", batch_size=1)
    version_id = result["version"]["id"]
    link_id = result["link"]["id"]
    calls = {"n": 0}
    real_now = ids.now

    def flaky_now():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("构建中断")
        return real_now()

    monkeypatch.setattr(ids, "now", flaky_now)
    entry = db.query(IdentityLink).filter_by(id=link_id).first()
    process_version(db, version_id)
    db.expire_all()
    entry = db.query(IdentityLink).filter_by(id=link_id).first()
    assert entry.revoked_at is not None  # attempt stays auditable
    assert entry.revoked_version_id == version_id
    mapping = resolve_person_map(db, dataset.id, ids._published(db, dataset.id))
    assert mapping["S0"] == mapping["S2"] == "S0"
    assert mapping["S4"] == mapping["S4"] and mapping["S6"] == "S4"
    # No stale "already linked" block: the pair can be linked again.
    monkeypatch.setattr(ids, "now", real_now)
    retry = _link(db, dataset, "S2", "S4")
    assert retry["link"]["id"] != link_id
    _publish_all(db, retry["version"])
    mapping = resolve_person_map(db, dataset.id, ids._published(db, dataset.id))
    assert mapping["S4"] == "S0" and mapping["S6"] == "S0"
