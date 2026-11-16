from pydantic import BaseModel, Field


class LinkInput(BaseModel):
    subject_a: str = Field(min_length=1, max_length=120)
    subject_b: str = Field(min_length=1, max_length=120)
    batch_size: int = Field(default=200, ge=1, le=5000)


class VersionBuildInput(BaseModel):
    batch_size: int = Field(default=200, ge=1, le=5000)
