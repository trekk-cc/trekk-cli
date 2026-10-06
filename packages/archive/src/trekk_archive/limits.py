"""Reader safety caps (typed tunables; the backend overrides them via `TREKK_ARCHIVE_*`)."""

from pydantic import BaseModel, ConfigDict, Field

GIB = 2**30


class ReaderLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_total_bytes: int = Field(default=20 * GIB, gt=0)
    max_entries: int = Field(default=1000, gt=0)
    max_entry_bytes: int = Field(default=4 * GIB, gt=0)
    max_compression_ratio: float = Field(default=250.0, gt=0)
