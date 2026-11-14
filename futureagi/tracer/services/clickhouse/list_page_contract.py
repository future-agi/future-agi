"""Exactness half of the published bounded list-page contract.

Every Observe list page already states whether its read *finished*
(``query_complete`` / ``query_status``).  Exactness is the other half of the
same statement: whether the published rows and their order are the requested
ordered prefix itself, or a qualified approximation of it.  Publishing only
completeness left ``query_exact`` absent from every successful list page, so a
client had no way to tell an exact page from an approximate one and had to
assume the worst on both.

``query_exact`` follows completeness because a finished bounded read publishes
exactly the ordered prefix it proved; an unfinished one cannot promise that.
The one thing completeness does not cover is the *source* of the candidate
order: an approximate ordering source (an insert-only rollup seed, say) is
inexact even when the read finishes, which is what ``ordering_source_exact``
carries.
"""

from typing import Any

__all__ = ["list_page_exactness"]


def list_page_exactness(
    *, complete: bool, ordering_source_exact: bool = True
) -> dict[str, Any]:
    """Return the exactness fields for one published list page.

    ``complete`` is the page's own published ``query_complete``.
    ``ordering_source_exact`` is ``False`` only when the candidate order came
    from an approximate source, which no amount of completeness repairs.
    """

    exact = bool(complete) and bool(ordering_source_exact)
    return {"query_exact": exact, "ordering_exact": exact}
