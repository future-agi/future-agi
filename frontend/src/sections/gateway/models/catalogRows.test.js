import { describe, expect, it } from "vitest";

import {
  ACCESS,
  PRICE_OPTIONS,
  TYPE_OPTIONS,
  buildCatalogRows,
  comparePaidPrice,
  filterRows,
  managedVerdict,
  modelType,
  ownKeyRoutes,
  plainDescription,
  retirement,
  typeEndpoint,
} from "./catalogRows";
import {
  DEMO_CATALOG_MODELS,
  DEMO_PROVIDERS,
} from "./test-fixtures/demoCatalog";

const byId = Object.fromEntries(DEMO_CATALOG_MODELS.map((m) => [m.id, m]));
const ON = { available: true, enabled: true, markup_percent: 5 };

const rows = (status = ON, providers = DEMO_PROVIDERS.providers) =>
  buildCatalogRows(DEMO_CATALOG_MODELS, { providers, managedStatus: status });
const rowOf = (list, id) => list.find((r) => r.id === id);

describe("model type", () => {
  it("reads the catalog's endpoint type", () => {
    expect(modelType(byId["anthropic/claude-haiku-5.5"])).toBe("chat");
    expect(modelType(byId["black-forest-labs/flux-3-image"])).toBe("images");
    expect(modelType(byId["alibaba/happyhorse-1.0"])).toBe("video");
    expect(modelType(byId["openai/text-embedding-3-small"])).toBe("embeddings");
  });

  it("falls back to the output modality, then chat", () => {
    expect(modelType({ output_modalities: ["embeddings"] })).toBe("embeddings");
    expect(modelType({ output_modalities: ["speech"] })).toBe("audio_speech");
    expect(modelType({ output_modalities: ["text"] })).toBe("chat");
    expect(modelType({})).toBe("chat");
  });

  it("names each type and the gateway route that serves it", () => {
    expect(TYPE_OPTIONS.map((t) => t.label)).toEqual([
      "Chat",
      "Image",
      "Embeddings",
      "Speech",
      "Transcription",
      "Video",
      "Rerank",
      "Decision",
    ]);
    expect(typeEndpoint("chat")).toBe("/chat/completions");
    expect(typeEndpoint("embeddings")).toBe("/embeddings");
    expect(typeEndpoint("images")).toBe("/images/generations");
  });
});

// Managed chat serves chat models its catalog can bill; everything else is
// listed but never billed to managed credit. A gateway that marks a model
// managed_allowed: false is believed.
describe("whether managed credits can buy a model", () => {
  it("sells priced chat models with text output, free ones included", () => {
    expect(managedVerdict(byId["anthropic/claude-haiku-5.5"]).sold).toBe(true);
    expect(managedVerdict(byId["cohere/north-mini-code:free"]).sold).toBe(true);
    // Deprecated, retiring on 2026-10-20: sold until that day.
    const beforeRetirement = { now: Date.parse("2026-10-19T23:59:59Z") };
    expect(
      managedVerdict(byId["google/gemini-2.5-flash"], beforeRetirement).sold,
    ).toBe(true);
    expect(
      managedVerdict(byId["google/gemini-2.5-flash"], {
        now: Date.parse("2026-10-20T00:00:00Z"),
      }).sold,
    ).toBe(false);
  });

  it("refuses other endpoints and models the gateway does not offer, with the reason", () => {
    const image = managedVerdict(byId["black-forest-labs/flux-3-image"]);
    expect(image.sold).toBe(false);
    expect(image.reason).toMatch(/image model/i);
    expect(image.reason).toMatch(/chat models only/i);

    const video = managedVerdict(byId["alibaba/happyhorse-1.0"]);
    expect(video.sold).toBe(false);
    expect(video.reason).toMatch(/does not serve video/i);

    const notOffered = managedVerdict(byId["google/gemini-2.5-flash-image"]);
    expect(notOffered.sold).toBe(false);
    expect(notOffered.reason).toMatch(/does not offer this model/i);
  });

  it("follows the gateway's rule when an older catalog has no managed_allowed", () => {
    const { managed_allowed: _drop, ...older } =
      byId["black-forest-labs/flux-3-image"];
    expect(managedVerdict(older).sold).toBe(false);
    const { managed_allowed: _keep, ...chat } =
      byId["anthropic/claude-haiku-5.5"];
    expect(managedVerdict(chat).sold).toBe(true);
    // Dynamic router pricing is not billable on credits, even in an older
    // catalogue without an explicit managed_allowed field.
    const { managed_allowed: _dynamic, ...dynamic } = byId["openrouter/auto"];
    expect(managedVerdict(dynamic).sold).toBe(false);
    expect(managedVerdict(dynamic).reason).toMatch(/Not available on Future AGI credits/);
  });
});

// This gateway's catalog (GET /-/catalog) sends no endpoint_type and no
// managed_allowed. Managed chat sells a model its catalog can bill
// (catalog.go Sellable: not unpriced and not stale_price; a dynamic price is
// billed at the upstream's reported cost) on chat completions only
// (server/managed_endpoints.go), and a model is an image model only when it
// outputs images and no text (catalog/modelinfo.go). These models are cut
// from the gateway's bundled catalog (catalog/openrouter_snapshot.json).
describe("on a catalog with no endpoint type or managed_allowed", () => {
  const model = (fields) => ({
    vendor: "acme",
    provider: "openrouter",
    input_modalities: ["text"],
    output_modalities: ["text"],
    prices: { prompt: 0.000001, completion: 0.000002 },
    input_per_mtok: 1,
    output_per_mtok: 2,
    billed_input_per_mtok: 1.05,
    billed_output_per_mtok: 2.1,
    state: "active",
    ...fields,
  });
  // google/gemini-nano-banana-2.1: answers in text and images.
  const textAndImage = model({
    id: "google/gemini-nano-banana-2.1",
    vendor: "google",
    input_modalities: ["image", "text"],
    output_modalities: ["image", "text"],
  });
  // A model a plain /v1/models list names without a price: listed at 0.
  const unpriced = model({
    id: "acme/listed-without-price",
    unpriced: true,
    prices: { prompt: 0, completion: 0 },
    input_per_mtok: 0,
    output_per_mtok: 0,
    billed_input_per_mtok: 0,
    billed_output_per_mtok: 0,
  });
  // A price-card model whose card is too old to bill by.
  const stale = model({ id: "acme/old-price-card", stale_price: true });
  // openrouter/auto-beta: priced per request by what it routes to.
  const dynamic = model({
    id: "openrouter/auto-beta",
    vendor: "openrouter",
    output_modalities: ["text", "image"],
    dynamic_price: true,
    prices: { prompt: 0, completion: 0 },
    input_per_mtok: undefined,
    output_per_mtok: undefined,
    billed_input_per_mtok: undefined,
    billed_output_per_mtok: undefined,
  });
  const imageOnly = model({
    id: "acme/image-only",
    output_modalities: ["image"],
  });
  const list = [textAndImage, unpriced, stale, dynamic, imageOnly];
  const catalogRows = (status = ON) =>
    buildCatalogRows(list, { providers: [], managedStatus: status });

  it("types a model that answers in text as chat, whatever else it outputs", () => {
    expect(modelType(textAndImage)).toBe("chat");
    expect(modelType(dynamic)).toBe("chat");
    expect(modelType(imageOnly)).toBe("images");
    expect(typeEndpoint(modelType(textAndImage))).toBe("/chat/completions");
  });

  it("sells a chat model that also outputs images", () => {
    expect(managedVerdict(textAndImage)).toEqual({ sold: true, reason: "" });
  });

  it("never sells an unpriced model, and never shows it as free", () => {
    const verdict = managedVerdict(unpriced);
    expect(verdict.sold).toBe(false);
    expect(verdict.reason).toMatch(/Not available on Future AGI credits: unpriced/i);
    const row = rowOf(catalogRows(), "acme/listed-without-price");
    expect(row.access).toBe(ACCESS.NONE);
    expect(row.input).toBeNull();
    expect(row.output).toBeNull();
    expect(
      filterRows(catalogRows(), { price: "free" }).map((r) => r.id),
    ).not.toContain("acme/listed-without-price");
  });

  it("never sells a model whose catalog price is out of date", () => {
    const verdict = managedVerdict(stale);
    expect(verdict.sold).toBe(false);
    expect(verdict.reason).toMatch(/out of date/i);
    expect(rowOf(catalogRows(), "acme/old-price-card").access).toBe(
      ACCESS.NONE,
    );
  });

  it("refuses a dynamic-priced model, with no credit price to show", () => {
    expect(managedVerdict(dynamic).sold).toBe(false);
    const row = rowOf(catalogRows(), "openrouter/auto-beta");
    expect(row.access).toBe(ACCESS.NONE);
    expect(row.input).toBeNull();
    expect(row.output).toBeNull();
    expect(row.managedOn).toBe(false);
    expect(row.accessReason).toMatch(/Not available on Future AGI credits/);
  });

  // The gateway's catalog eligibility check (routing/eligibility/rules_catalog.go)
  // refuses a model from its expiration date on, with 410.
  it("never sells a model past its expiration date, and still sells one before it", () => {
    const at = { now: Date.parse("2026-10-24T12:00:00Z") };
    const expired = model({
      id: "acme/retired",
      expiration_date: "2026-10-24",
    });
    const verdict = managedVerdict(expired, at);
    expect(verdict.sold).toBe(false);
    expect(verdict.reason).toMatch(/retired on 2026-10-24/);
    const retiring = model({
      id: "acme/retiring",
      expiration_date: "2026-10-25",
    });
    expect(managedVerdict(retiring, at).sold).toBe(true);
    const rows = buildCatalogRows([expired, retiring], {
      providers: [],
      managedStatus: ON,
      ...at,
    });
    expect(rowOf(rows, "acme/retired").access).toBe(ACCESS.NONE);
    expect(rowOf(rows, "acme/retiring").access).toBe(ACCESS.MANAGED);
  });

  it("still refuses a model that writes no text", () => {
    const verdict = managedVerdict(imageOnly);
    expect(verdict.sold).toBe(false);
    expect(verdict.reason).toMatch(/chat models only/i);
  });
});

describe("own-key routes", () => {
  it("matches a provider named for a catalog vendor to the models it lists", () => {
    const routes = ownKeyRoutes(DEMO_PROVIDERS.providers, DEMO_CATALOG_MODELS);
    expect(routes.get("openai/gpt-4o")).toEqual([
      {
        providerId: "openai",
        providerName: "OpenAI (local mock LLM)",
        sendAs: "gpt-4o",
      },
    ]);
    expect(routes.get("openai/text-embedding-3-small")).toHaveLength(1);
    // flaky-upstream names no vendor, so the console does not guess which
    // catalog model its "gpt-4o" is.
    expect(
      [...routes.values()]
        .flat()
        .some((r) => r.providerId === "flaky-upstream"),
    ).toBe(false);
  });
});

describe("catalog rows", () => {
  it("classify access: managed, your key, both, or not callable", () => {
    const list = rows();
    expect(rowOf(list, "anthropic/claude-haiku-5.5").access).toBe(
      ACCESS.MANAGED,
    );
    expect(rowOf(list, "openai/gpt-4o").access).toBe(ACCESS.BOTH);
    expect(rowOf(list, "openai/text-embedding-3-small").access).toBe(
      ACCESS.OWN_KEY,
    );
    const flux = rowOf(list, "black-forest-labs/flux-3-image");
    expect(flux.access).toBe(ACCESS.NONE);
    expect(flux.accessReason).toMatch(/image model/i);
  });

  it("carry the price the org pays: managed price for managed rows, none otherwise", () => {
    const list = rows();
    const haiku = rowOf(list, "anthropic/claude-haiku-5.5");
    expect(haiku.input).toBe(0.105);
    expect(haiku.output).toBe(0.525);
    expect(rowOf(list, "cohere/north-mini-code:free").input).toBe(0);
    expect(rowOf(list, "openai/text-embedding-3-small").input).toBeNull();
    expect(rowOf(list, "black-forest-labs/flux-3-image").input).toBeNull();
  });

  it("treat managed as unavailable on a deployment without it", () => {
    const list = rows({ available: false, enabled: false });
    expect(rowOf(list, "anthropic/claude-haiku-5.5").access).toBe(ACCESS.NONE);
    expect(rowOf(list, "anthropic/claude-haiku-5.5").accessReason).toMatch(
      /does not offer Future AGI models/i,
    );
    expect(rowOf(list, "openai/gpt-4o").access).toBe(ACCESS.OWN_KEY);
  });

  it("mark managed rows as off while the org has not opted in", () => {
    const list = rows({ available: true, enabled: false });
    const haiku = rowOf(list, "anthropic/claude-haiku-5.5");
    expect(haiku.access).toBe(ACCESS.MANAGED);
    expect(haiku.managedOn).toBe(false);
  });

  it("flag retiring models with their date", () => {
    expect(retirement(byId["google/gemini-2.5-flash"])).toEqual({
      deprecated: true,
      date: "2026-10-20",
    });
    expect(retirement(byId["openai/gpt-4o"])).toBeNull();
    expect(
      retirement({ expiration_date: "2026-11-11", state: "active" }),
    ).toEqual({ deprecated: false, date: "2026-11-11" });
  });
});

describe("filtering rows", () => {
  const list = rows();
  const ids = (filters) => filterRows(list, filters).map((r) => r.id);

  it("by type", () => {
    expect(ids({ type: "images" })).toEqual(["black-forest-labs/flux-3-image"]);
  });

  it("by access, where managed and your key each include both", () => {
    expect(ids({ access: ACCESS.OWN_KEY }).sort()).toEqual([
      "openai/gpt-4o",
      "openai/gpt-4o-mini",
      "openai/text-embedding-3-small",
    ]);
    expect(ids({ access: ACCESS.MANAGED })).toContain("openai/gpt-4o");
    expect(ids({ access: ACCESS.MANAGED })).not.toContain(
      "openai/text-embedding-3-small",
    );
    expect(ids({ access: ACCESS.NONE })).toContain("alibaba/happyhorse-1.0");
  });

  it("by the input price paid, leaving out rows with no price", () => {
    expect(PRICE_OPTIONS.map((o) => o.value)).toEqual([
      "",
      "free",
      "0.5",
      "2",
      "10",
    ]);
    expect(ids({ price: "free" })).toEqual(["cohere/north-mini-code:free"]);
    const cheap = ids({ price: "0.5" });
    expect(cheap).toContain("anthropic/claude-haiku-5.5");
    expect(cheap).not.toContain("openai/gpt-4o");
    expect(cheap).not.toContain("openai/text-embedding-3-small");
  });

  it("by vendor, context, capability and every search term", () => {
    expect(ids({ vendor: "google" }).sort()).toEqual([
      "google/gemini-2.5-flash",
      "google/gemini-2.5-flash-image",
    ]);
    expect(ids({ minContext: 1_000_000 })).toContain("google/gemini-2.5-flash");
    expect(ids({ minContext: 1_000_000 })).not.toContain("openai/gpt-4o");
    expect(ids({ capabilities: ["vision", "tools"] })).toContain(
      "openai/gpt-4o",
    );
    expect(ids({ search: "haiku anthropic" })).toEqual([
      "anthropic/claude-haiku-5.5",
    ]);
  });
});

describe("sorting by price", () => {
  it("keeps unpriced rows last in both directions", () => {
    const priced = { input: 1 };
    const cheaper = { input: 0.5 };
    const none = { input: null };
    const asc = [none, priced, cheaper].sort((a, b) =>
      comparePaidPrice(a.input, b.input, null, null, false),
    );
    expect(asc).toEqual([cheaper, priced, none]);
    // AG Grid reverses the comparator for a descending sort.
    const desc = [none, cheaper, priced]
      .sort((a, b) => comparePaidPrice(a.input, b.input, null, null, true))
      .reverse();
    expect(desc).toEqual([priced, cheaper, none]);
  });
});

describe("descriptions", () => {
  it("drop markdown link syntax but keep the words", () => {
    expect(
      plainDescription(
        "It keeps the intelligence of [GPT-4 Turbo](/models/openai/gpt-4-turbo) while being twice as fast.",
      ),
    ).toBe(
      "It keeps the intelligence of GPT-4 Turbo while being twice as fast.",
    );
    expect(plainDescription("Uses the `deepseek-flash` name.")).toBe(
      "Uses the deepseek-flash name.",
    );
    expect(plainDescription(undefined)).toBe("");
  });
});
