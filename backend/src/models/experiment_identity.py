from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from ..config.database import Base
from ..utils.clock import now


class ExperimentIdentity(Base):
    """The identity version pinned when an experiment was registered.

    Kept in its own table so existing experiment rows need no destructive
    schema change. Experiments registered while a newer version is still
    building pin the latest *published* version.
    """

    __tablename__ = "experiment_identity_pins"

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), unique=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("identity_versions.id"))
    pinned_by: Mapped[str]
    pinned_at: Mapped[str] = mapped_column(default=now)
