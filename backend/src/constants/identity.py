from enum import StrEnum


class IdentityVersionStatus(StrEnum):
    BUILDING = "building"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class IdentityWorkStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
