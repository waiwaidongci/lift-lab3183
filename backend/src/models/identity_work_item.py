from sqlalchemy import ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ..config.database import Base
from ..utils.clock import now


class IdentityWorkItem(Base):
    """One persisted batch of an identity version rebuild.

    The batch carries only the subjects it must re-attribute. Processing one
    item writes its assignments and marks the item done in a single
    transaction, so an interrupted worker resumes from the first pending item
    and never publishes a half-built version.
    """

    __tablename__ = "identity_work_items"
    __table_args__ = (UniqueConstraint("version_id", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("identity_versions.id"))
    sequence: Mapped[int]
    subjects: Mapped[list] = mapped_column(JSON, default=list)
    groups: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(default="pending")
    error: Mapped[str] = mapped_column(default="")
    changed_subjects: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[str] = mapped_column(default=now)
    completed_at: Mapped[str | None] = mapped_column(nullable=True)
