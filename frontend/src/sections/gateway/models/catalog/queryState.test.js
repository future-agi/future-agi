import { describe, expect, it } from "vitest";
import {
  CATALOG_PATH,
  DEFAULT_FILTERS,
  SORT_PRESETS,
  catalogBrowseState,
  parseCatalogQuery,
  serializeCatalogQuery,
  sortColumnState,
  sortFromColumns,
} from "./queryState";

describe("portable catalog browse state", () => {
  it("round-trips search, multi-value facets, context, price and every preset", () => {
    const filters = {
      search: "fast & small / code",
      type: ["chat", "embeddings"],
      vendor: ["openai", "google"],
      access: ["own_key", "managed"],
      capabilities: ["tools", "structured"],
      parameters: ["logprobs", "seed"],
      inputModalities: ["audio", "text"],
      minContext: 128000,
      price: "2",
    };
    for (const { value: sort } of SORT_PRESETS) {
      expect(
        parseCatalogQuery(serializeCatalogQuery({ filters, sort })),
      ).toEqual({ filters, sort });
    }
  });

  it("ignores malformed facets and falls back to usable first", () => {
    expect(
      parseCatalogQuery(
        "?type=nope&access=admin&price=-1&context=Infinity&capability=nope&provider=%3Cbad%3E&sort=made-up",
      ),
    ).toEqual({ filters: DEFAULT_FILTERS, sort: "usable" });
    expect(parseCatalogQuery("?context=128001&sort=__proto__").sort).toBe(
      "usable",
    );
    expect(parseCatalogQuery("?context=128001").filters.minContext).toBe(0);
  });

  it("deduplicates valid facets without dropping other selections", () => {
    const { filters } = parseCatalogQuery(
      "?provider=google&provider=google&provider=openai&capability=tools&capability=nope&capability=vision&type=chat&type=nope",
    );
    expect(filters.vendor).toEqual(["google", "openai"]);
    expect(filters.capabilities).toEqual(["tools", "vision"]);
    expect(filters.type).toEqual(["chat"]);
  });

  it("preserves exact metadata names and Not reported, but discards malformed values", () => {
    const { filters } = parseCatalogQuery("?parameter=seed&parameter=seed&parameter=vendor.feature&parameter=%3Cinvalid%3E&input=audio&input=__not_reported__&input=");
    expect(filters.parameters).toEqual(["seed", "vendor.feature"]);
    expect(filters.inputModalities).toEqual(["audio", "__not_reported__"]);
    expect(parseCatalogQuery(serializeCatalogQuery({ filters, sort: "newest" })).filters).toEqual(filters);
  });

  it("preserves advanced grid sorts, their precedence, and an unsorted grid", () => {
    const columns = [
      { colId: "input", sort: "desc", sortIndex: 1 },
      { colId: "name", sort: "asc", sortIndex: 0 },
      { colId: "vendorName", width: 210 },
    ];
    const sort = sortFromColumns(columns);
    expect(sort).toBe("name:asc,input:desc");
    expect(
      sortColumnState(
        parseCatalogQuery(
          serializeCatalogQuery({ filters: DEFAULT_FILTERS, sort }),
        ).sort,
      ),
    ).toEqual([
      { colId: "name", sort: "asc", sortIndex: 0 },
      { colId: "input", sort: "desc", sortIndex: 1 },
    ]);
    expect(sortFromColumns([])).toBe("none");
    expect(sortColumnState("none")).toEqual([]);
    expect(sortFromColumns(sortColumnState("newest"))).toBe("newest");
    expect(parseCatalogQuery("?sort=name:desc,name:asc").sort).toBe("usable");
    expect(parseCatalogQuery("?sort=capabilities:asc").sort).toBe("usable");
  });

  it("carries an exact browse query and finite scroll position only on Models", () => {
    const search = "?provider=google&q=fast%20model&sort=newest";
    expect(
      catalogBrowseState({ pathname: CATALOG_PATH, search }, 1040),
    ).toEqual({ pathname: CATALOG_PATH, search, scrollTop: 1040 });
    for (const pathname of [
      "//elsewhere",
      "https://example.test",
      "/dashboard/gateway/keys",
    ]) {
      expect(catalogBrowseState({ pathname, search }, -1)).toEqual({
        pathname: CATALOG_PATH,
        search: "",
        scrollTop: 0,
      });
    }
    expect(catalogBrowseState({ pathname: CATALOG_PATH }, NaN).scrollTop).toBe(
      0,
    );
  });
});
