"""Generic pager driven by `OPERATIONS[name].pagination`: no operation's parameters are
hard-coded. A page is requested with the moving parameter (cursor, page token, page number or
offset) and the size where the spec puts them (query or body); the next position comes from the
answer's `next_cursor` / `paginationToken` (or the operation's own cursor field) and the end
from `has_more` / `hasMore` / `has_next_page`, else from a short page; the list total from
`total_count` / `total` when the answer has one."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from trekk_harvester.checkpoint import Checkpoint, Cursor
from trekk_harvester.errors import RequestFailedError
from trekk_harvester.runner import Runner
from trekk_harvester.trigify import OPERATIONS, TrigifyPort
from trekk_harvester.trigify.operation import Pagination

MORE_FIELDS = ("has_more", "hasMore", "has_next_page")
TOKEN_FIELDS = ("next_cursor", "paginationToken")
TOTAL_FIELDS = ("total_count", "total")


@dataclass(frozen=True, slots=True)
class Page:
    """One page's items (JSON, extras kept), the position of the next page, whether this was
    the last one and the list total Trigify gave (None without one)."""

    items: list[Any]
    next: Cursor
    last: bool
    total: int | None = None


def _pagination(op_name: str) -> Pagination:
    pagination = OPERATIONS[op_name].pagination
    if pagination is None:
        raise ValueError(f"{op_name} is not paginated")
    return pagination


async def fetch_page(
    runner: Runner,
    client: TrigifyPort,
    op_name: str,
    position: Cursor,
    *,
    size: int | None = None,
    path: tuple[str, ...] = (),
    params: Mapping[str, Any] | None = None,
) -> Page:
    """Fetch the page at `position` (None: the first page). `params` are the operation's other
    arguments: Python names for query operations, body field names for body operations."""
    op = OPERATIONS[op_name]
    pagination = _pagination(op_name)
    if position is None and pagination.kind == "page":
        position = pagination.base
    if position is None and pagination.kind == "offset":
        position = 0
    paging: dict[str, Any] = {} if position is None else {pagination.param: position}
    if size is not None:
        if pagination.size_param is None:
            raise ValueError(f"{op_name} takes no page size")
        paging[pagination.size_param] = size
    if pagination.location == "query":
        py_names = {param.name: param.py_name for param in op.query_params}
        kwargs = {**(params or {}), **{py_names[name]: value for name, value in paging.items()}}
    else:
        if op.body is None:
            raise ValueError(f"{op_name} pages in a body it does not have")
        kwargs = {"body": op.body.model_validate({**(params or {}), **paging})}
    answer = await runner.call(getattr(client, op_name), *path, **kwargs)
    dump: dict[str, Any] = answer.model_dump(mode="json", by_alias=True, exclude_unset=True)
    items = _items(op_name, dump.get("data"))
    page = _next_page(pagination, dump, position, items, size or pagination.default_size)
    if pagination.kind in ("cursor", "page_token") and not page.last and page.next == position:
        raise RequestFailedError(op_name, "cursor did not advance")
    return page


def _items(op_name: str, data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    arrays = [value for value in (data or {}).values() if isinstance(value, list)]
    if len(arrays) != 1:
        raise ValueError(f"{op_name}: expected one item array in data, found {len(arrays)}")
    items: list[Any] = arrays[0]
    return items


def _field(dump: dict[str, Any], names: tuple[str, ...]) -> Any:
    """The first non-null paging field among the answer's root, `pagination` and `data`."""
    containers = [dump, dump.get("pagination"), dump.get("data")]
    for container in containers:
        if isinstance(container, dict):
            for name in names:
                if container.get(name) is not None:
                    return container[name]
    return None


def _next_page(
    pagination: Pagination, dump: dict[str, Any], position: Cursor, items: list[Any], size: int
) -> Page:
    more = _field(dump, MORE_FIELDS)
    total = _field(dump, TOTAL_FIELDS)
    has_more = bool(more) if more is not None else len(items) >= size
    match pagination.kind:
        case "cursor" | "page_token":
            token = _field(dump, (*TOKEN_FIELDS, pagination.param))
            following: Cursor = None if token is None else str(token)
            has_more = has_more and following is not None
        case "page":
            following = int(position or 0) + 1
        case "offset":
            following = int(position or 0) + len(items)
    if not items:
        has_more = False
    return Page(
        items, following if has_more else None, not has_more, None if total is None else int(total)
    )


async def crawl_pages(
    runner: Runner,
    client: TrigifyPort,
    checkpoint: Checkpoint,
    crawl: str,
    op_name: str,
    *,
    size: int | None = None,
    path: tuple[str, ...] = (),
    params: Mapping[str, Any] | None = None,
    on_page: Callable[[int], None] = lambda _: None,
) -> None:
    """Walk `op_name` from the crawl's committed position to the end, committing each page.
    `on_page` gets the number of records the crawl holds after each commit."""
    state = checkpoint.crawl(crawl)
    if state.done:
        return
    count = sum(1 for _ in checkpoint.records(crawl))
    position = state.cursor
    while True:
        page = await fetch_page(
            runner, client, op_name, position, size=size, path=path, params=params
        )
        checkpoint.commit(crawl, page.items, cursor=page.next, done=page.last, total=page.total)
        count += len(page.items)
        on_page(count)
        if page.last:
            return
        position = page.next
