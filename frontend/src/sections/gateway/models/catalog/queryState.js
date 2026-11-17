import { useCallback, useEffect, useMemo, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { saveColumnState } from "../../components/frame/column-state";
import { modelPath } from "../modelLinks";
import { CAPABILITIES, CONTEXT_OPTIONS } from "../catalog";
import { ACCESS_OPTIONS, PRICE_OPTIONS, TYPE_OPTIONS, validMetadataFacet } from "../catalogRows";

export const CATALOG_PATH = "/dashboard/gateway/models";
export const DEFAULT_FILTERS = {
  search: "",
  type: [],
  vendor: [],
  access: [],
  price: "",
  minContext: 0,
  capabilities: [],
  parameters: [],
  inputModalities: [],
};

// Only facts already present in the catalog; no popularity or speed estimates.
export const SORT_PRESETS = [
  {
    value: "usable",
    label: "Usable first",
    columns: [
      ["accessLabel", "asc"],
      ["released", "desc"],
    ],
  },
  { value: "newest", label: "Newest", columns: [["released", "desc"]] },
  {
    value: "price_asc",
    label: "Input price: low to high",
    columns: [["input", "asc"]],
  },
  {
    value: "price_desc",
    label: "Input price: high to low",
    columns: [["input", "desc"]],
  },
  {
    value: "context_desc",
    label: "Largest context",
    columns: [["context", "desc"]],
  },
];
const SORTABLE = new Set([
  "name",
  "vendorName",
  "typeLabel",
  "accessLabel",
  "context",
  "input",
  "output",
  "maxOutput",
  "released",
]);
const encodeColumns = (pairs) =>
  pairs.map(([id, direction]) => `${id}:${direction}`).join(",");

function validSort(value) {
  if (SORT_PRESETS.some((preset) => preset.value === value) || value === "none")
    return value;
  const pairs = String(value || "")
    .split(",")
    .map((part) => part.split(":"));
  const ids = new Set(pairs.map(([id]) => id));
  return ids.size === pairs.length &&
    pairs.every(
      ([id, direction, extra]) =>
        SORTABLE.has(id) &&
        ["asc", "desc"].includes(direction) &&
        extra === undefined,
    )
    ? value
    : "usable";
}

export function sortColumnState(sort) {
  const value = validSort(sort);
  if (value === "none") return [];
  const preset = SORT_PRESETS.find((item) => item.value === value);
  const pairs = preset
    ? preset.columns
    : value.split(",").map((part) => part.split(":"));
  return pairs.map(([colId, direction], sortIndex) => ({
    colId,
    sort: direction,
    sortIndex,
  }));
}

export function sortFromColumns(columns) {
  const pairs = columns
    .filter(
      (col) => SORTABLE.has(col.colId) && ["asc", "desc"].includes(col.sort),
    )
    .sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0))
    .map((col) => [col.colId, col.sort]);
  if (!pairs.length) return "none";
  const encoded = encodeColumns(pairs);
  return (
    SORT_PRESETS.find((preset) => encodeColumns(preset.columns) === encoded)
      ?.value || encoded
  );
}

const unique = (values) => [...new Set(values)];
const optionValues = (options) => options.map((option) => String(option.value));

export function parseCatalogQuery(search = "") {
  const query = new URLSearchParams(search);
  const many = (key, allowed) =>
    unique(query.getAll(key).filter((value) => allowed.includes(value)));
  const one = (key, allowed, fallback) =>
    allowed.includes(query.get(key)) ? query.get(key) : fallback;
  return {
    filters: {
      search: query.get("q") || "",
      type: many("type", optionValues(TYPE_OPTIONS)),
      // Provider identifiers are catalog-driven, not a hard-coded vendor list.
      vendor: unique(
        query
          .getAll("provider")
          .filter((value) => /^[a-z0-9][a-z0-9_-]{0,79}$/.test(value)),
      ),
      access: many("access", optionValues(ACCESS_OPTIONS)),
      price: one("price", optionValues(PRICE_OPTIONS), ""),
      minContext: Number(one("context", optionValues(CONTEXT_OPTIONS), "0")),
      capabilities: many(
        "capability",
        CAPABILITIES.map((capability) => capability.key),
      ),
      // Exact catalog-driven facts; retain well-formed future names in shared
      // links even if this catalog does not currently list them (zero matches).
      parameters: unique(query.getAll("parameter").filter(validMetadataFacet)),
      inputModalities: unique(query.getAll("input").filter(validMetadataFacet)),
    },
    sort: validSort(query.get("sort")),
  };
}

export function serializeCatalogQuery({ filters, sort }) {
  const query = new URLSearchParams();
  if (filters.search) query.set("q", filters.search);
  for (const [key, parameter] of [
    ["type", "type"],
    ["vendor", "provider"],
    ["access", "access"],
    ["capabilities", "capability"],
    ["parameters", "parameter"],
    ["inputModalities", "input"],
  ]) {
    for (const value of unique(filters[key] || []))
      query.append(parameter, value);
  }
  if (filters.price) query.set("price", filters.price);
  if (filters.minContext) query.set("context", String(filters.minContext));
  query.set("sort", validSort(sort));
  return `?${query}`;
}

// BP-06 consumes location.state.gatewayModelBrowse. Keep the query bytes
// intact for Back, but never turn arbitrary location state into a destination.
export function catalogBrowseState(location, scrollTop = 0) {
  const isCatalog = location?.pathname === CATALOG_PATH;
  return {
    pathname: CATALOG_PATH,
    search:
      isCatalog &&
      typeof location.search === "string" &&
      location.search.startsWith("?")
        ? location.search
        : "",
    scrollTop:
      isCatalog && Number.isFinite(scrollTop) && scrollTop >= 0 ? scrollTop : 0,
  };
}

// Bind the pure codec to this browser's history and the existing grid API.
// Column layout stays in its existing per-viewer store; queries stay portable.
export function useCatalogBrowse(layoutKey, rowHeight) {
  const navigate = useNavigate();
  const location = useLocation();
  const currentLocation = useRef(location);
  currentLocation.current = location;
  const { filters, sort } = useMemo(
    () => parseCatalogQuery(location.search),
    [location.search],
  );
  const gridApi = useRef(null);
  const appliedSort = useRef(null);
  const scrollTop = useRef(0);
  const restoredLocation = useRef(null);

  const changeQuery = useCallback(
    (nextFilters, nextSort, replace = false) => {
      scrollTop.current = 0;
      navigate(
        {
          pathname: location.pathname,
          search: serializeCatalogQuery({
            filters: nextFilters,
            sort: nextSort,
          }),
        },
        {
          replace,
          state: { ...location.state, gatewayModelBrowse: undefined },
        },
      );
    },
    [navigate, location.pathname, location.state],
  );
  const applySort = useCallback(
    (api) => {
      if (!api || api.isDestroyed?.()) return;
      appliedSort.current = sort;
      // Clear all previous precedence, never widths, order, pins or visibility.
      api.applyColumnState({
        state: sortColumnState(sort),
        defaultState: { sort: null, sortIndex: null },
      });
    },
    [sort],
  );
  useEffect(() => {
    if (appliedSort.current !== sort) applySort(gridApi.current);
  }, [sort, applySort]);

  const onSortChanged = (event) => {
    if (event.source !== "uiColumnSorted") return;
    const state = event.api.getColumnState();
    saveColumnState(layoutKey, state);
    const nextSort = sortFromColumns(state);
    appliedSort.current = nextSort;
    if (nextSort !== sort) changeQuery(filters, nextSort);
  };
  const restoreScroll = (event) => {
    const saved = location.state?.gatewayModelBrowse;
    if (
      !saved ||
      saved.pathname !== location.pathname ||
      saved.search !== location.search ||
      restoredLocation.current === location.key
    )
      return;
    const count = event.api.getDisplayedRowCount();
    if (!count) return;
    const browse = catalogBrowseState(saved, saved.scrollTop);
    event.api.ensureIndexVisible(
      Math.min(count - 1, Math.floor(browse.scrollTop / rowHeight)),
      "top",
    );
    restoredLocation.current = location.key;
    scrollTop.current = browse.scrollTop;
  };
  const openModel = useCallback(
    (event) => {
      if (event?.event?.target?.closest?.("a")) return;
      const path = modelPath(event?.data?.id);
      if (!path) return;
      const here = currentLocation.current;
      const top =
        gridApi.current?.getVerticalPixelRange?.()?.top ?? scrollTop.current;
      const gatewayModelBrowse = catalogBrowseState(here, top);
      // Save the origin too so browser Back returns to the same position.
      navigate(
        { pathname: here.pathname, search: here.search },
        { replace: true, state: { ...here.state, gatewayModelBrowse } },
      );
      navigate(path, { state: { gatewayModelBrowse } });
    },
    [navigate],
  );

  return {
    filters,
    sort,
    openModel,
    setFilter: (key, value) =>
      changeQuery({ ...filters, [key]: value }, sort, key === "search"),
    clearFilters: () => changeQuery(DEFAULT_FILTERS, sort),
    setSort: (value) => changeQuery(filters, value),
    gridEvents: {
      onGridReady: (event) => {
        gridApi.current = event.api;
        applySort(event.api);
      },
      onGridPreDestroyed: () => {
        gridApi.current = null;
        appliedSort.current = null;
      },
      onSortChanged,
      onBodyScroll: (event) => {
        scrollTop.current = event.top;
      },
      onFirstDataRendered: restoreScroll,
      onRowDataUpdated: restoreScroll,
    },
  };
}
