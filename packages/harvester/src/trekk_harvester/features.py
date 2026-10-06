"""The per-feature gate: when the first top-level request of a harvest step answers 403 or 404,
the workspace never used that feature (or the route is not offered to it), which is not an
error. The step's crawl is marked done without records, its kinds go to the manifest's
`not_used` (`NOT_USED`) or its route path to `not_available` (`NOT_AVAILABLE`). A 403/404 after
the crawl committed a page stays a top-level failure."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from trekk_archive import KINDS, Kind
from trekk_harvester.checkpoint import Checkpoint
from trekk_harvester.errors import ForbiddenError, NotFoundError

NOT_USED = "not_used"
NOT_AVAILABLE = "not_available"

# crawl -> the kinds it holds (what a feature never used leaves out)
type Features = Mapping[str, tuple[Kind, ...]]


@contextmanager
def gate(checkpoint: Checkpoint, crawl: str, failure: str) -> Iterator[None]:
    """Run a step's first top-level request(s) for `crawl`: a 403/404 before any page was
    committed marks the crawl done with `failure`; any other error, or one after a committed
    page, is raised."""
    try:
        yield
    except ForbiddenError, NotFoundError:
        if checkpoint.crawl(crawl).pages:
            raise
        checkpoint.mark(crawl, failure)


def not_used(checkpoint: Checkpoint, features: Features) -> list[Kind]:
    """Kinds of the crawls marked never used, in canonical order."""
    kinds = {
        kind
        for crawl, crawl_kinds in features.items()
        if checkpoint.crawl(crawl).failure == NOT_USED
        for kind in crawl_kinds
    }
    return sorted(kinds, key=KINDS.index)
