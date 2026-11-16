import os
import tempfile

os.environ.setdefault("SEED_DEMO", "false")
_DB = os.path.join(tempfile.mkdtemp(prefix="lift-test-"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config.database import Base, get_db
from src.models import Dataset, Observation, User, Experiment, AnalysisRun
from src.utils.security import hash_password, make_token
from src.main import app


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def seeded(db):
    admin = User(
        email="admin@test", name="管理员", role="admin",
        password_hash=hash_password("pw"),
    )
    analyst = User(
        email="analyst@test", name="分析师", role="analyst",
        password_hash=hash_password("pw"),
    )
    viewer = User(
        email="viewer@test", name="观察员", role="viewer",
        password_hash=hash_password("pw"),
    )
    db.add_all([admin, analyst, viewer])
    dataset = Dataset(name="ds", source="t.csv", description="test", checksum="x")
    db.add(dataset)
    db.flush()
    # 10 subjects: S0..S9, alternating arms per subject index.
    for i in range(10):
        db.add(
            Observation(
                dataset_id=dataset.id,
                subject=f"S{i}",
                arm="control" if i % 2 == 0 else "treatment",
                pre_value=float(i + 1),
                value=float(10 + i),
            )
        )
    db.commit()
    return {"db": db, "dataset": dataset, "admin": admin, "analyst": analyst, "viewer": viewer}


@pytest.fixture()
def client(seeded):
    db = seeded["db"]

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        c.db = db
        c.seeded = seeded
        c.admin_headers = {"Authorization": "Bearer " + make_token(seeded["admin"])}
        c.analyst_headers = {"Authorization": "Bearer " + make_token(seeded["analyst"])}
        c.viewer_headers = {"Authorization": "Bearer " + make_token(seeded["viewer"])}
        yield c
    app.dependency_overrides.clear()
