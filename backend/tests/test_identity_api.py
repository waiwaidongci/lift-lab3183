from src.models import IdentityVersion
from src.services.identity_service import process_version


def _identity_url(dataset_id):
    return f"/datasets/{dataset_id}/identity"


def _link(client, dataset_id, a, b, batch_size=200, headers=None):
    return client.post(
        _identity_url(dataset_id) + "/links",
        json={"subject_a": a, "subject_b": b, "batch_size": batch_size},
        headers=headers or client.analyst_headers,
    )


def test_state_requires_auth_and_viewer_cannot_link(client):
    dataset_id = client.seeded["dataset"].id
    assert client.get(_identity_url(dataset_id)).status_code == 401
    assert client.get(_identity_url(dataset_id), headers=client.viewer_headers).status_code == 200
    denied = _link(client, dataset_id, "S0", "S2", headers=client.viewer_headers)
    assert denied.status_code == 403


def test_link_revoke_flow_through_api(client):
    dataset_id = client.seeded["dataset"].id
    created = _link(client, dataset_id, "S0", "S2")
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["link"]["subject_a"] == "S0"
    assert payload["version"]["number"] == 2
    assert payload["version"]["status"] == "building"
    assert payload["groups"] == [{"person_key": "S0", "subjects": ["S0", "S2"]}]

    process_version(client.db, payload["version"]["id"])
    state = client.get(_identity_url(dataset_id), headers=client.viewer_headers).json()
    assert state["published_version"]["number"] == 2
    assert state["building_version"] is None
    assert state["groups"][0]["subjects"] == ["S0", "S2"]
    assert state["groups"][0]["cross_arm"] is False

    revoked = client.post(
        _identity_url(dataset_id) + f"/links/{payload['link']['id']}/revoke",
        json={"batch_size": 200}, headers=client.analyst_headers,
    )
    assert revoked.status_code == 201, revoked.text
    process_version(client.db, revoked.json()["version"]["id"])

    state = client.get(_identity_url(dataset_id), headers=client.viewer_headers).json()
    assert state["active_link_count"] == 0
    assert state["groups"] == []
    assert len(state["links"]) == 1
    link = state["links"][0]
    assert link["revoked_at"]
    assert link["added_version_id"] == payload["version"]["id"]
    assert link["revoked_version_id"] == revoked.json()["version"]["id"]


def test_observation_preview_shows_merge_and_quarantine(client):
    dataset_id = client.seeded["dataset"].id
    payload = _link(client, dataset_id, "S0", "S2").json()
    process_version(client.db, payload["version"]["id"])
    # Cross-arm pair: S1 (treatment) + S3 (treatment) -> same arm, no quarantine.
    payload = _link(client, dataset_id, "S1", "S3").json()
    process_version(client.db, payload["version"]["id"])

    rows = client.get(f"/datasets/{dataset_id}/observations", headers=client.viewer_headers).json()
    by_subject = {row["subject"]: row for row in rows}
    assert by_subject["S0"]["person_key"] == "S0"
    assert by_subject["S2"]["person_key"] == "S0"
    assert by_subject["S0"]["merged"] is True
    assert by_subject["S0"]["identity_version"] == 3
    assert by_subject["S1"]["merged"] is True
    assert by_subject["S1"]["quarantined"] is False

    state = client.get(f"/datasets/{dataset_id}/observation-state", headers=client.viewer_headers).json()
    assert state["summary"]["people"] == 8
    assert state["summary"]["merged_people"] == 2
    assert state["summary"]["merged_observations"] == 2
    assert state["summary"]["quarantined_people"] == 0
    assert state["identity_version"] == 3


def test_cross_arm_quarantine_reported_with_reason(client):
    dataset_id = client.seeded["dataset"].id
    payload = _link(client, dataset_id, "S0", "S1").json()
    process_version(client.db, payload["version"]["id"])
    state = client.get(f"/datasets/{dataset_id}/observation-state", headers=client.viewer_headers).json()
    assert state["summary"]["quarantined_people"] == 1
    assert state["quarantine"][0]["subjects"] == ["S0", "S1"]
    assert "整人隔离" in state["quarantine"][0]["reason"]


def test_unknown_subject_rejected(client):
    dataset_id = client.seeded["dataset"].id
    response = _link(client, dataset_id, "S0", "NOPE")
    assert response.status_code == 404
    assert "NOPE" in response.json()["detail"]


def test_resume_endpoint_is_idempotent(client):
    dataset_id = client.seeded["dataset"].id
    payload = _link(client, dataset_id, "S0", "S1", batch_size=1).json()
    url = _identity_url(dataset_id) + f"/versions/{payload['version']['id']}/resume"
    assert client.post(url, headers=client.analyst_headers).status_code == 200
    assert client.post(url, headers=client.analyst_headers).status_code == 200
    version = client.db.get(IdentityVersion, payload["version"]["id"])
    assert version.status == "building"


def test_export_carries_identity_provenance(client):
    dataset_id = client.seeded["dataset"].id
    payload = _link(client, dataset_id, "S0", "S2").json()
    process_version(client.db, payload["version"]["id"])
    payload = _link(client, dataset_id, "S1", "S3").json()
    process_version(client.db, payload["version"]["id"])

    from src.models import AnalysisRun, Experiment
    from src.services.analysis_service import run_analysis

    experiment = Experiment(
        name="export", dataset_id=dataset_id, hypothesis="导出身份来源",
        metric="revenue", alpha=.05, cuped=False, owner="分析师",
    )
    client.db.add(experiment)
    client.db.flush()
    from src.services.identity_service import _published, pin_version

    pin_version(client.db, experiment.id, _published(client.db, dataset_id), "analyst@test")
    client.db.commit()
    from src.models import Observation

    result = run_analysis(
        client.db, experiment,
        client.db.query(Observation).filter_by(dataset_id=dataset_id).all(),
    )
    run = AnalysisRun(experiment_id=experiment.id, status="completed", result=result)
    client.db.add(run)
    client.db.commit()

    export = client.get(f"/runs/{run.id}/export", headers=client.viewer_headers)
    assert export.status_code == 200
    text = export.text
    assert "identity_version" in text
    assert "merged_observations" in text
    assert "quarantined_people" in text
    assert "merged_person" in text  # per-person merge trail
    assert "merged_people_detail" not in text
    assert run.result["identity"]["version_summary"]["number"] == 3
    assert text.count("S0 S2") == 1


def test_preview_flags_cross_arm_partner_outside_window(client):
    dataset_id = client.seeded["dataset"].id
    # Add 120 subjects so that a preview of 100 rows can split a merged pair.
    from src.models import Observation

    for i in range(120):
        client.db.add(Observation(
            dataset_id=dataset_id, subject=f"Z{i:03}",
            arm="control" if i % 2 == 0 else "treatment",
            pre_value=1.0, value=2.0,
        ))
    client.db.commit()
    # Z000 (control) + Z001 (treatment): both are the first rows added after S9.
    created = _link(client, dataset_id, "Z000", "Z001").json()
    process_version(client.db, created["version"]["id"])

    from src.services.dataset_service import observations

    rows = observations(client.db, dataset_id, limit=None)
    by_subject = {row["subject"]: row for row in rows}
    assert by_subject["Z000"]["person_key"] == by_subject["Z001"]["person_key"] == "Z000"
    assert by_subject["Z000"]["quarantined"] is True
    assert by_subject["Z001"]["quarantined"] is True
    assert by_subject["Z000"]["merged"] is True
