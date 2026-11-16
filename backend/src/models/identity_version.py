from sqlalchemy import ForeignKey, Index, JSON, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from ..config.database import Base
from ..utils.clock import now


class IdentityVersion(Base):
    """Snapshot of how anonymous subjects are grouped into people.

    A version is built in batches against its base version. Until every batch
    has completed it stays ``building`` and the previous ``published`` version
    remains authoritative. Publication flips the two rows in one transaction.
    """

    __tablename__ = "identity_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "number"),
        # At most one build per dataset: two concurrent requests can never
        # interleave into a half-merged state.
        Index(
            "identity_versions_one_building",
            "dataset_id",
            unique=True,
            postgresql_where=text("status = 'building'"),
            sqlite_where=text("status = 'building'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    number: Mapped[int]
    status: Mapped[str] = mapped_column(default="building")
    base_version_id: Mapped[int | None] = mapped_column(ForeignKey("identity_versions.id"), nullable=True)
    created_by: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now)
    published_at: Mapped[str | None] = mapped_column(nullable=True)
    superseded_at: Mapped[str | None] = mapped_column(nullable=True)
    total_batches: Mapped[int] = mapped_column(default=0)
    completed_batches: Mapped[int] = mapped_column(default=0)
    batch_size: Mapped[int] = mapped_column(default=200)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(default="")
