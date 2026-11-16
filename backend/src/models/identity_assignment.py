from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ..config.database import Base


class IdentityAssignment(Base):
    """A subject's person key as overridden by one identity version.

    Rows are append-only. A version only writes rows for subjects whose person
    key changed, so revoking a link never rewrites unaffected people. Resolving
    a version means taking the newest row at or below that version number,
    falling back to the subject itself (singleton) when no row exists.
    """

    __tablename__ = "identity_assignments"
    __table_args__ = (
        UniqueConstraint("version_id", "subject"),
        Index("identity_assignments_dataset_subject_idx", "dataset_id", "subject"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    subject: Mapped[str]
    person_key: Mapped[str]
    version_id: Mapped[int] = mapped_column(ForeignKey("identity_versions.id"))
