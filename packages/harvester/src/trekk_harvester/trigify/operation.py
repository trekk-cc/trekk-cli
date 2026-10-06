"""Static description of one Trigify operation, shared by the generated client and the mock."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

type PaginationKind = Literal["cursor", "page", "offset", "page_token"]
type ParamLocation = Literal["query", "body"]


@dataclass(frozen=True, slots=True)
class Pagination:
    """How one operation pages: which request parameter moves, where it lives, its base and size.

    `base` is the first page number (0 or 1) for `page`; `default_size` is what the API returns
    when no size parameter is sent (or when the operation has none).
    """

    kind: PaginationKind
    param: str
    size_param: str | None
    location: ParamLocation
    base: int
    default_size: int


@dataclass(frozen=True, slots=True)
class QueryParam:
    name: str
    py_name: str
    required: bool


@dataclass(frozen=True, slots=True)
class Operation:
    name: str
    method: str
    path: str
    path_params: tuple[str, ...]
    query_params: tuple[QueryParam, ...]
    body: type[BaseModel] | None
    body_required: bool
    response: type[BaseModel]
    status: int
    pagination: Pagination | None
