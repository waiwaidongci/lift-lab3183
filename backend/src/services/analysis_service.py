"""Run an experiment's statistics against its pinned identity version."""

from .identity_service import (
    _published,
    _version_summary,
    ensure_baseline_version,
    person_level_observations,
    pinned_version,
    pin_version,
    resolve_person_map,
)
from .statistics_service import analyze


def run_analysis(db, experiment, observations):
    version = pinned_version(db, experiment.id)
    if version is None:
        # Legacy experiment registered before pinning existed: freeze the
        # currently published version on its first analysis so later identity
        # updates cannot silently change its evidence.
        ensure_baseline_version(db, experiment.dataset_id, "worker")
        version = _published(db, experiment.dataset_id)
        pin_version(db, experiment.id, version, "worker")
        db.commit()
    person_map = resolve_person_map(db, experiment.dataset_id, version)
    rows, quarantine, stats = person_level_observations(observations, person_map)
    result = analyze(rows, experiment.alpha, experiment.cuped)
    result["identity"] = {
        **stats,
        "version_summary": _version_summary(db, version),
        "pinned": True,
    }
    return result
