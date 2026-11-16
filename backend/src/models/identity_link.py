from sqlalchemy import ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from ..config.database import Base
from ..utils.clock import now


class IdentityLink(Base):
    """An anonymous same-person link maintained from the observation page.

    Links are never deleted: revoking sets ``revoked_at`` so that the history,
    and the identity version that first applied or removed the link, stay
    traceable. The same ordered pair may be linked again after a revocation.
    """

    __tablename__ = "identity_links"
    __table_args__ = (
        Index("identity_links_dataset_idx", "dataset_id"),
        Index(
            "identity_links_active_pair_key",
            "dataset_id", "subject_a", "subject_b",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    subject_a: Mapped[str]
    subject_b: Mapped[str]
    created_by: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now)
    revoked_by: Mapped[str | None] = mapped_column(nullable=True)
    revoked_at: Mapped[str | None] = mapped_column(nullable=True)
    added_version_id: Mapped[int | None] = mapped_column(ForeignKey("identity_versions.id"), nullable=True)
    revoked_version_id: Mapped[int | None] = mapped_column(ForeignKey("identity_versions.id"), nullable=True)
