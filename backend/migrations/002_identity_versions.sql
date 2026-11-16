-- Anonymous identity linking, versioned publication and experiment pinning.
-- Bootstrap creates these tables from SQLAlchemy metadata; this migration is
-- the reviewed equivalent for an existing deployment. Run inside one
-- transaction after a backup; it is additive and does not rewrite observations.

CREATE TABLE IF NOT EXISTS identity_versions (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets (id),
    number INTEGER NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'building',
    base_version_id INTEGER REFERENCES identity_versions (id),
    created_by VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    published_at VARCHAR,
    superseded_at VARCHAR,
    total_batches INTEGER NOT NULL DEFAULT 0,
    completed_batches INTEGER NOT NULL DEFAULT 0,
    batch_size INTEGER NOT NULL DEFAULT 200,
    summary JSON NOT NULL DEFAULT '{}',
    error VARCHAR NOT NULL DEFAULT '',
    CONSTRAINT identity_versions_dataset_number_key UNIQUE (dataset_id, number)
);

-- One row per subject whose person key changed at that version. Append-only so
-- a revocation can be attributed to the version that applied it.
CREATE TABLE IF NOT EXISTS identity_assignments (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets (id),
    subject VARCHAR NOT NULL,
    person_key VARCHAR NOT NULL,
    version_id INTEGER NOT NULL REFERENCES identity_versions (id),
    CONSTRAINT identity_assignments_version_subject_key UNIQUE (version_id, subject)
);
CREATE INDEX IF NOT EXISTS identity_assignments_dataset_subject_idx
    ON identity_assignments (dataset_id, subject);

-- Links are revoked, never deleted; the version columns record which build
-- added or removed each edge.
CREATE TABLE IF NOT EXISTS identity_links (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets (id),
    subject_a VARCHAR NOT NULL,
    subject_b VARCHAR NOT NULL,
    created_by VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    revoked_by VARCHAR,
    revoked_at VARCHAR,
    added_version_id INTEGER REFERENCES identity_versions (id),
    revoked_version_id INTEGER REFERENCES identity_versions (id)
);
CREATE INDEX IF NOT EXISTS identity_links_dataset_idx ON identity_links (dataset_id);
CREATE UNIQUE INDEX IF NOT EXISTS identity_links_active_pair_key
    ON identity_links (dataset_id, subject_a, subject_b)
    WHERE revoked_at IS NULL;

-- Each row is a persisted batch. A worker interruption resumes at the first
-- pending row and the old published version stays authoritative until the last
-- batch flips the status in one transaction.
CREATE TABLE IF NOT EXISTS identity_work_items (
    id SERIAL PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES identity_versions (id),
    sequence INTEGER NOT NULL,
    subjects JSON NOT NULL DEFAULT '[]',
    groups JSON NOT NULL DEFAULT '[]',
    status VARCHAR NOT NULL DEFAULT 'pending',
    error VARCHAR NOT NULL DEFAULT '',
    changed_subjects INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR NOT NULL,
    completed_at VARCHAR,
    CONSTRAINT identity_work_items_version_sequence_key UNIQUE (version_id, sequence)
);

-- The identity version frozen when an experiment was registered.
CREATE TABLE IF NOT EXISTS experiment_identity_pins (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL UNIQUE REFERENCES experiments (id),
    version_id INTEGER NOT NULL REFERENCES identity_versions (id),
    pinned_by VARCHAR NOT NULL,
    pinned_at VARCHAR NOT NULL
);
