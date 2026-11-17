// The Console's model list as Future AGI reads it with the person's own
// session (the app's axios instance) when the frame asks, and shapes it for
// the frame: {type: "embed.models", doc, models, attestation?,
// runAuthorization?}. An authorization is minted only for the model a request
// names, and only when that model is one of the attested rows.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CONSOLE_MODELS_PATH,
  EMBED_MODELS_MESSAGE,
  EMBED_MODELS_REQUEST_MESSAGE,
  MODEL_ID_MAX_LENGTH,
  RUN_AUTHORIZATIONS_PATH,
  RUN_AUTHORIZATION_MAX_LENGTH,
  createHostModelsLoader,
  loadHostModels,
  modelsMessage,
  readModelsRequest,
} from "../host-models";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({ default: http }));

const DOC = "AAECAwQFBgcICQoLDA0ODw";
const ATTESTATION =
  "eyJhbGciOiJSUzI1NiIsInR5cCI6Im9tZWdhLW1vZGVscytqd3QifQ.eyJzdWIiOiJ1In0.c2lnbmF0dXJl";
const TOKEN = "fe1r.c2VudGluZWwtY2Fub25pY2Fs.c2VudGluZWwtbWFj";

const row = (model, extra = {}) => ({
  model,
  name: model.toUpperCase(),
  provider: "openai",
  vendor: "openai",
  context_length: 128000,
  top: false,
  top_rank: null,
  ...extra,
});
const ROWS = [
  row("gpt-4o", { top: true, top_rank: 1, field_from_a_newer_server: [1] }),
  row("claude-sonnet-4-5", { provider: "anthropic", vendor: "anthropic" }),
];
const listAnswer = (result) => ({ data: { status: true, result } });
const RESULT = {
  sources: { byok: { available: true, providers: ["openai"], error: null } },
  models: ROWS,
  total: ROWS.length,
  truncated: false,
  attestation: ATTESTATION,
};
const minted = (run_authorization = TOKEN) => ({
  data: {
    status: true,
    result: { run_authorization, run_id: "run-1", exp: 1700000000 },
  },
});
// The app's axios instance rejects with the body plus statusCode.
const refusal = (statusCode, code) => ({
  status: false,
  code,
  message: "A fixed sentence.",
  statusCode,
});

const CONSOLE_METHODS = ["log", "info", "warn", "error", "debug"];

describe("the person's model list for the Console frame", () => {
  let consoleSpies;

  beforeEach(() => {
    http.get.mockReset();
    http.post.mockReset();
    consoleSpies = CONSOLE_METHODS.map((name) =>
      vi.spyOn(console, name).mockImplementation(() => {}),
    );
  });

  afterEach(() => {
    for (const spy of consoleSpies) expect(spy).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  it("reads a fresh list through the app's axios instance and mints nothing without a picked model", async () => {
    http.get.mockResolvedValue(listAnswer(RESULT));
    const { signal } = new AbortController();

    const answer = await loadHostModels({ signal });

    expect(http.get).toHaveBeenCalledTimes(1);
    expect(http.get).toHaveBeenCalledWith(CONSOLE_MODELS_PATH, { signal });
    expect(http.post).not.toHaveBeenCalled();
    expect(answer).toEqual({ models: ROWS, attestation: ATTESTATION });
    expect("runAuthorization" in answer).toBe(false);
    // The attested rows go on exactly as the server sent them.
    expect(answer.models).toBe(ROWS);
  });

  it("mints for exactly the picked model with the same session", async () => {
    http.get.mockResolvedValue(listAnswer(RESULT));
    http.post.mockResolvedValue(minted());
    const { signal } = new AbortController();

    const answer = await loadHostModels({ signal, model: "claude-sonnet-4-5" });

    expect(http.get).toHaveBeenCalledWith(CONSOLE_MODELS_PATH, { signal });
    expect(http.post).toHaveBeenCalledTimes(1);
    expect(http.post).toHaveBeenCalledWith(
      RUN_AUTHORIZATIONS_PATH,
      { models: ["claude-sonnet-4-5"] },
      { signal },
    );
    expect(answer).toEqual({
      models: ROWS,
      attestation: ATTESTATION,
      runAuthorization: TOKEN,
    });
    expect(answer.models).toBe(ROWS);
  });

  it("reads the list before it mints, in that order", async () => {
    const order = [];
    http.get.mockImplementation(async () => {
      order.push("list");
      return listAnswer(RESULT);
    });
    http.post.mockImplementation(async () => {
      order.push("mint");
      return minted();
    });

    await loadHostModels({ model: "gpt-4o" });

    expect(order).toEqual(["list", "mint"]);
  });

  it("mints for a picked model far down a long list", async () => {
    const many = Array.from({ length: 12 }, (_, index) =>
      row(`m-${index + 1}`),
    );
    http.get.mockResolvedValue(listAnswer({ ...RESULT, models: many }));
    http.post.mockResolvedValue(minted());

    const answer = await loadHostModels({ model: "m-10" });

    expect(http.post).toHaveBeenCalledTimes(1);
    expect(http.post.mock.calls[0][1]).toEqual({ models: ["m-10"] });
    expect(answer.runAuthorization).toBe(TOKEN);
    expect(answer.models).toBe(many);
  });

  it.each([
    ["is not one of the rows", "gpt-5-unlisted"],
    ["differs from a row only in case", "GPT-4O"],
    ["is empty", ""],
    ["is not a string", 42],
  ])(
    "mints nothing when the picked model %s, and still offers the list",
    async (_label, model) => {
      http.get.mockResolvedValue(listAnswer(RESULT));

      const answer = await loadHostModels({ model });

      expect(http.post).not.toHaveBeenCalled();
      expect(answer).toEqual({ models: ROWS, attestation: ATTESTATION });
    },
  );

  it("echoes the request's id on the answer, and never a model", () => {
    expect(
      modelsMessage({
        doc: DOC,
        requestId: "start-1",
        models: ROWS,
        attestation: ATTESTATION,
      }),
    ).toEqual({
      type: EMBED_MODELS_MESSAGE,
      doc: DOC,
      requestId: "start-1",
      models: ROWS,
      attestation: ATTESTATION,
    });
    expect(
      "model" in
        modelsMessage({
          doc: DOC,
          requestId: "start-1",
          model: "gpt-4o",
          models: ROWS,
          attestation: ATTESTATION,
        }),
    ).toBe(false);
  });

  it("builds exactly the agreed message shape, echoing the doc", async () => {
    http.get.mockResolvedValue(listAnswer(RESULT));
    http.post.mockResolvedValue(minted());

    const message = modelsMessage({
      doc: DOC,
      ...(await loadHostModels({ model: "gpt-4o" })),
    });

    expect(message).toEqual({
      type: EMBED_MODELS_MESSAGE,
      doc: DOC,
      models: ROWS,
      attestation: ATTESTATION,
      runAuthorization: TOKEN,
    });
    expect(EMBED_MODELS_MESSAGE).toBe("embed.models");
    expect(Object.keys(message).sort()).toEqual(
      ["attestation", "doc", "models", "runAuthorization", "type"].sort(),
    );
  });

  it.each([
    ["is missing", undefined],
    ["is null", null],
    ["is not a string", 42],
    ["is empty", ""],
    ["has one part", "header"],
    ["has two parts", "header.payload"],
    ["has an extra part", "header.payload.signature.extra"],
    ["has an empty first part", ".payload.signature"],
    ["has an empty middle part", "header..signature"],
    ["has an empty last part", "header.payload."],
    ["contains padding", "header.payload.signature="],
    ["contains a non-base64url character", "header.pay/oad.signature"],
    ["contains whitespace", "header.pay load.signature"],
    ["ends with a line break", `${ATTESTATION}\n`],
    ["is too long", `a.b.${"c".repeat(4093)}`],
  ])("leaves out an attestation that %s", (_label, attestation) => {
    const message = modelsMessage({
      doc: DOC,
      requestId: "start-1",
      models: ROWS,
      attestation,
      runAuthorization: TOKEN,
    });

    expect(message).toEqual({
      type: EMBED_MODELS_MESSAGE,
      doc: DOC,
      requestId: "start-1",
      models: ROWS,
      runAuthorization: TOKEN,
    });
    expect(message).not.toHaveProperty("attestation");
    expect(message.models).toBe(ROWS);
  });

  it("keeps valid attestations unchanged through the length limit", () => {
    for (const attestation of [
      "a.b.c",
      "A_0.b-9.C_z",
      ATTESTATION,
      `a.b.${"c".repeat(4092)}`,
    ]) {
      const message = modelsMessage({
        doc: DOC,
        requestId: "start-1",
        models: ROWS,
        attestation,
        runAuthorization: TOKEN,
      });

      expect(message).toEqual({
        type: EMBED_MODELS_MESSAGE,
        doc: DOC,
        requestId: "start-1",
        models: ROWS,
        attestation,
        runAuthorization: TOKEN,
      });
      expect(message.models).toBe(ROWS);
    }
  });

  it.each([
    [
      "409 console_run_not_configured",
      () => refusal(409, "console_run_not_configured"),
    ],
    ["403 model_not_in_list", () => refusal(403, "model_not_in_list")],
    ["403 console_run_disabled", () => refusal(403, "console_run_disabled")],
    [
      "a network failure",
      () => ({
        message: "Network Error",
        statusCode: undefined,
        transportCode: "ERR_NETWORK",
      }),
    ],
  ])("offers the list alone on %s", async (_label, error) => {
    http.get.mockResolvedValue(listAnswer(RESULT));
    http.post.mockRejectedValue(error());

    const answer = await loadHostModels({ model: "gpt-4o" });

    expect(http.post).toHaveBeenCalledTimes(1);
    expect(answer).toEqual({ models: ROWS, attestation: ATTESTATION });
    expect("runAuthorization" in answer).toBe(false);
    expect("runAuthorization" in modelsMessage({ doc: DOC, ...answer })).toBe(
      false,
    );
  });

  it("offers the list without an authorization when the mint throws before it is sent", async () => {
    const loader = createHostModelsLoader({
      get: () => Promise.resolve(listAnswer(RESULT)),
      post: () => {
        throw new TypeError("not a function");
      },
    });

    const answer = await loader({ model: "gpt-4o" });

    expect(answer).toEqual({ models: ROWS, attestation: ATTESTATION });
  });

  it.each([
    ["contains whitespace", "fe1r.abc def.ghi"],
    ["ends with a line break", `${TOKEN}\n`],
    ["is too long", "a".repeat(RUN_AUTHORIZATION_MAX_LENGTH + 1)],
    ["is empty", ""],
    ["is not a string", 42],
  ])("leaves out an authorization that %s", async (_label, token) => {
    http.get.mockResolvedValue(listAnswer(RESULT));
    http.post.mockResolvedValue(minted(token));

    const answer = await loadHostModels({ model: "gpt-4o" });

    expect(answer).toEqual({ models: ROWS, attestation: ATTESTATION });
  });

  it("keeps an authorization of exactly the longest accepted length", async () => {
    const longest = "a".repeat(RUN_AUTHORIZATION_MAX_LENGTH);
    http.get.mockResolvedValue(listAnswer(RESULT));
    http.post.mockResolvedValue(minted(longest));

    expect((await loadHostModels({ model: "gpt-4o" })).runAuthorization).toBe(
      longest,
    );
  });

  it.each([
    ["null", null],
    ["missing", undefined],
    ["empty", ""],
    ["not a string", { alg: "none" }],
  ])(
    "leaves a %s attestation out of the message and mints nothing for a picked model",
    async (_label, attestation) => {
      const result = { ...RESULT, attestation };
      if (attestation === undefined) delete result.attestation;
      http.get.mockResolvedValue(listAnswer(result));

      const answer = await loadHostModels({ model: "gpt-4o" });

      expect(answer).toEqual({ models: ROWS, attestation: null });
      const message = modelsMessage({ doc: DOC, ...answer });
      expect(message).toEqual({
        type: "embed.models",
        doc: DOC,
        models: ROWS,
      });
      expect(message).not.toHaveProperty("attestation");
      expect(Object.keys(message).sort()).toEqual(["doc", "models", "type"]);
      expect(http.post).not.toHaveBeenCalled();
    },
  );

  it("mints nothing for an empty list, and still offers it", async () => {
    http.get.mockResolvedValue(listAnswer({ ...RESULT, models: [], total: 0 }));

    expect(await loadHostModels({ model: "gpt-4o" })).toEqual({
      models: [],
      attestation: ATTESTATION,
    });
    expect(http.post).not.toHaveBeenCalled();
  });

  it.each([
    ["401", () => Promise.reject(refusal(401, "not_authenticated"))],
    ["503", () => Promise.reject(refusal(503, "console_models_unavailable"))],
    ["no answer", () => Promise.reject({ message: "Network Error" })],
    ["no result", () => Promise.resolve({ data: { status: true } })],
    [
      "rows that are not a list",
      () => Promise.resolve(listAnswer({ ...RESULT, models: {} })),
    ],
  ])("offers no list and mints nothing on %s", async (_label, answer) => {
    http.get.mockImplementation(answer);

    expect(await loadHostModels({ model: "gpt-4o" })).toBeNull();
    expect(http.post).not.toHaveBeenCalled();
  });

  it("asks nothing for a document that already went away", async () => {
    const controller = new AbortController();
    controller.abort();

    expect(
      await loadHostModels({ signal: controller.signal, model: "gpt-4o" }),
    ).toBeNull();
    expect(http.get).not.toHaveBeenCalled();
    expect(http.post).not.toHaveBeenCalled();
  });

  it("mints nothing when the document goes away while the list is read", async () => {
    const controller = new AbortController();
    http.get.mockImplementation(async () => {
      controller.abort();
      return listAnswer(RESULT);
    });

    expect(
      await loadHostModels({ signal: controller.signal, model: "gpt-4o" }),
    ).toBeNull();
    expect(http.post).not.toHaveBeenCalled();
  });

  it("drops a minted authorization when the document goes away during the mint", async () => {
    const controller = new AbortController();
    http.get.mockResolvedValue(listAnswer(RESULT));
    http.post.mockImplementation(async () => {
      controller.abort();
      return minted();
    });

    expect(
      await loadHostModels({ signal: controller.signal, model: "gpt-4o" }),
    ).toBeNull();
  });
});

describe("the frame's embed.models.request", () => {
  it("reads a request without a model and one with a model", () => {
    expect(EMBED_MODELS_REQUEST_MESSAGE).toBe("embed.models.request");
    expect(
      readModelsRequest({
        type: "embed.models.request",
        doc: DOC,
        requestId: "r1",
      }),
    ).toEqual({ type: "embed.models.request", doc: DOC, requestId: "r1" });
    expect(
      readModelsRequest({
        type: "embed.models.request",
        doc: DOC,
        requestId: "r1",
        model: "gpt-4o",
      }),
    ).toEqual({
      type: "embed.models.request",
      doc: DOC,
      requestId: "r1",
      model: "gpt-4o",
    });
    expect(
      readModelsRequest({
        type: "embed.models.request",
        doc: DOC,
        requestId: "r".repeat(64),
        model: "m".repeat(MODEL_ID_MAX_LENGTH),
      }),
    ).not.toBeNull();
  });

  it.each([
    ["another type", { type: "embed.models", doc: DOC, requestId: "r1" }],
    ["no doc", { type: "embed.models.request", requestId: "r1" }],
    [
      "a doc that is not a document nonce",
      { type: "embed.models.request", doc: "abc", requestId: "r1" },
    ],
    [
      "an extra field",
      {
        type: "embed.models.request",
        doc: DOC,
        requestId: "r1",
        models: ["gpt-4o"],
      },
    ],
    [
      "a field the host would echo",
      {
        type: "embed.models.request",
        doc: DOC,
        requestId: "r1",
        runAuthorization: "x",
      },
    ],
    [
      "an empty model",
      { type: "embed.models.request", doc: DOC, requestId: "r1", model: "" },
    ],
    [
      "a model that is not a string",
      {
        type: "embed.models.request",
        doc: DOC,
        requestId: "r1",
        model: ["gpt-4o"],
      },
    ],
    [
      "a model that is too long",
      {
        type: "embed.models.request",
        doc: DOC,
        requestId: "r1",
        model: "m".repeat(MODEL_ID_MAX_LENGTH + 1),
      },
    ],
    ["no requestId", { type: "embed.models.request", doc: DOC }],
    [
      "an empty requestId",
      { type: "embed.models.request", doc: DOC, requestId: "" },
    ],
    [
      "a requestId that is too long",
      { type: "embed.models.request", doc: DOC, requestId: "r".repeat(65) },
    ],
    [
      "a requestId with a space",
      { type: "embed.models.request", doc: DOC, requestId: "r 1" },
    ],
    [
      "a requestId that is not a string",
      { type: "embed.models.request", doc: DOC, requestId: 7 },
    ],
    ["not a record", "embed.models.request"],
    ["null", null],
  ])("reads nothing from a request with %s", (_label, data) => {
    expect(readModelsRequest(data)).toBeNull();
  });
});
