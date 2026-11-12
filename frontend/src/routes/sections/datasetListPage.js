// L7c g1 (url:owned-elsewhere): the dataset list's `?page=` (0-based) past
// the last page opens the last page with a one-line notice, never an empty
// list under an impossible pager range ("2476–9 of 9"). Pure.
import {
  readViewState,
  writeViewState,
} from "src/sections/common/ux/viewState";
import {
  LIST_URL_DEFAULTS,
  sortParamFrom,
  sortingFromParam,
} from "src/sections/develop/datasetList";

/** The list query's args for a search string, as DevelopView builds them. */
export function listQueryArgs(search) {
  const view = readViewState(search, LIST_URL_DEFAULTS);
  const sorting = sortingFromParam(view.sort);
  return {
    page: view.page,
    pageSize: view.size,
    search: view.q || null,
    sortBy: sorting[0] ? sortParamFrom(sorting).split(":")[0] : null,
    sortOrder: sorting[0]?.desc ? "desc" : "asc",
    filters: view.f,
    groupBy: view.group,
  };
}

/**
 * null while the page is in range (or unknown); else the search string on
 * the last page and the notice. `totalPages` is the list answer's.
 */
export function pagePastEnd(search, totalPages) {
  const view = readViewState(search, LIST_URL_DEFAULTS);
  const page = Number(view.page);
  if (!Number.isFinite(page) || page === 0) return null;
  if (!Number.isFinite(totalPages) || totalPages < 0) return null;
  if (Number.isInteger(page) && page > 0 && page < totalPages) return null;
  // Past the end (or a negative / fractional page): the last page.
  const last =
    page > 0 && page < totalPages
      ? Math.floor(page)
      : page < 0
        ? 0
        : Math.max(0, totalPages - 1);
  const pages = Math.max(1, totalPages);
  return {
    search: writeViewState(search, { ...view, page: last }, LIST_URL_DEFAULTS),
    notice: `This list has ${pages} page${pages === 1 ? "" : "s"}, so it opened on page ${last + 1}.`,
  };
}
