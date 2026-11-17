// The parent side of the model list: the Console's current document asks with
// embed.models.request {doc, requestId, model?}, and the bridge reads the person's list
// afresh and answers {type: "embed.models", doc, requestId, models,
// attestation?, runAuthorization?} to the frame's exact origin. Nothing is
// sent unasked.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createEmbedBridge } from "../bridge";
import {
  CONSOLE_MODELS_PATH,
  RUN_AUTHORIZATIONS_PATH,
  createHostModelsLoader,
} from "../host-models";

const CHILD = "http://127.0.0.1:4191";
const OTHER = "http://127.0.0.1:4192";
const DOC_A = "AAECAwQFBgcICQoLDA0ODw";
const DOC_B = "m4gLqkrE04O3BMKxGKKPFg";
const ATTESTATION =
  "eyJhbGciOiJSUzI1NiIsInR5cCI6Im9tZWdhLW1vZGVscytqd3QifQ.eyJzdWIiOiJ1In0.c2lnbmF0dXJl";
const NEWER_ATTESTATION =
  "eyJhbGciOiJSUzI1NiIsInR5cCI6Im9tZWdhLW1vZGVscytqd3QifQ.eyJzdWIiOiJ2In0.bmV3ZXI";
const TOKEN = "fe1r.c2VudGluZWwtY2Fub25pY2Fs.c2VudGluZWwtbWFj";
const ROWS = [
  { model: "gpt-4o", name: "GPT-4o", provider: "openai", top: true },
  { model: "claude-sonnet-4-5", name: "Claude", provider: "anthropic" },
];

const hello = (doc = DOC_A) => ({
  type: "embed.hello",
  protocol: "omega-embed/1",
  doc,
});
const gate = (state, doc = DOC_A) => ({ type: "embed.gate", state, doc });
const REQ = "req-1";
const request = (doc = DOC_A, model, requestId = REQ) => ({
  type: "embed.models.request",
  doc,
  requestId,
  ...(model === undefined ? {} : { model }),
});
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const listOf = (runAuthorization, attestation = ATTESTATION) => ({
  models: ROWS,
  attestation,
  ...(runAuthorization === undefined ? {} : { runAuthorization }),
});
const listAnswer = (result) => ({ data: { status: true, result } });
const minted = (run_authorization = TOKEN) => ({
  data: {
    status: true,
    result: { run_authorization, run_id: "run-1", exp: 1 },
  },
});

describe("parent embed.models", () => {
  let frame;
  let other;
  let posted;
  let events;
  let bridge;

  const start = (options = {}) => {
    bridge = createEmbedBridge({
      expectedOrigin: CHILD,
      getFrameWindow: () => frame.contentWindow,
      getToken: () => "tok_fixture",
      requireDoc: true,
      onEvent: (event) => events.push(event),
      ...options,
    });
  };
  const send = (data, { source = frame.contentWindow, origin = CHILD } = {}) =>
    window.dispatchEvent(new MessageEvent("message", { data, source, origin }));
  const connect = async (doc = DOC_A) => {
    send(hello(doc));
    await flush();
  };
  const ready = async (doc = DOC_A) => {
    send(gate("ready", doc));
    await flush();
  };
  const ask = async (data = request(), from = {}) => {
    send(data, from);
    await flush();
    await flush();
  };
  const modelPosts = () =>
    posted.filter((entry) => entry.message.type === "embed.models");
  const replies = () => modelPosts().map(({ message }) => message);
  const lastProbe = () =>
    posted.filter((entry) => entry.message.type === "embed.probe").at(-1)
      .message.challenge;

  beforeEach(() => {
    frame = document.createElement("iframe");
    other = document.createElement("iframe");
    document.body.append(frame, other);
    posted = [];
    events = [];
    vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(
      (message, targetOrigin) => posted.push({ message, targetOrigin }),
    );
    vi.spyOn(other.contentWindow, "postMessage").mockImplementation(() => {
      throw new Error("never post to another frame");
    });
  });

  afterEach(() => {
    bridge?.dispose();
    frame.remove();
    other.remove();
    vi.restoreAllMocks();
  });

  it("sends nothing unasked: a hello and a ready gate read no list", async () => {
    const getModels = vi.fn(async () => listOf(TOKEN));
    start({ getModels });

    await connect();
    await ready();
    await connect(); // a key refill
    await ready();

    expect(getModels).not.toHaveBeenCalled();
    expect(modelPosts()).toEqual([]);
  });

  it("answers a request without a model with the doc, a fresh list and its attestation", async () => {
    const getModels = vi.fn(async () => listOf());
    start({ getModels });
    await connect();

    await ask(request(DOC_A));

    expect(getModels).toHaveBeenCalledTimes(1);
    const [context] = getModels.mock.calls[0];
    expect(context.signal).toBeInstanceOf(AbortSignal);
    expect(context.document).toBeTypeOf("object");
    expect("model" in context).toBe(false);
    expect(modelPosts()).toEqual([
      {
        message: {
          type: "embed.models",
          doc: DOC_A,
          requestId: REQ,
          models: ROWS,
          attestation: ATTESTATION,
        },
        targetOrigin: CHILD,
      },
    ]);
  });

  it("answers a request with a model with the authorization the loader minted for it", async () => {
    const getModels = vi.fn(async () => listOf(TOKEN));
    start({ getModels });
    await connect();

    await ask(request(DOC_A, "claude-sonnet-4-5"));

    expect(getModels).toHaveBeenCalledTimes(1);
    expect(getModels.mock.calls[0][0].model).toBe("claude-sonnet-4-5");
    expect(replies()).toEqual([
      {
        type: "embed.models",
        doc: DOC_A,
        requestId: REQ,
        models: ROWS,
        attestation: ATTESTATION,
        runAuthorization: TOKEN,
      },
    ]);
  });

  it("never puts an authorization on the answer to a request without a model", async () => {
    start({ getModels: async () => listOf(TOKEN) });
    await connect();

    await ask(request(DOC_A));

    expect(replies()).toEqual([
      {
        type: "embed.models",
        doc: DOC_A,
        requestId: REQ,
        models: ROWS,
        attestation: ATTESTATION,
      },
    ]);
  });

  it("answers each request with its own id, so a start's answer is never a list refresh", async () => {
    start({ getModels: async () => listOf() });
    await connect();

    await ask(request(DOC_A, "gpt-4o", "start-1"));
    await ask(request(DOC_A, undefined, "open-1"));

    const [startAnswer, refresh] = replies();
    // A refused start is still known by its id: no token, the same request.
    expect(startAnswer.requestId).toBe("start-1");
    expect("runAuthorization" in startAnswer).toBe(false);
    expect(refresh.requestId).toBe("open-1");
    expect("model" in startAnswer || "model" in refresh).toBe(false);
  });

  it("reads the list again on every request of the same document, so a newer attestation replaces the old", async () => {
    const getModels = vi
      .fn()
      .mockResolvedValueOnce(listOf())
      .mockResolvedValueOnce(listOf(TOKEN, NEWER_ATTESTATION));
    start({ getModels });
    await connect();
    await ready();

    await ask(request(DOC_A));
    await ask(request(DOC_A, "gpt-4o"));

    expect(getModels).toHaveBeenCalledTimes(2);
    expect(replies().map(({ attestation }) => attestation)).toEqual([
      ATTESTATION,
      NEWER_ATTESTATION,
    ]);
    expect(replies()[1].runAuthorization).toBe(TOKEN);
    expect(replies().every(({ doc }) => doc === DOC_A)).toBe(true);
  });

  it("leaves a null attestation out of the reply", async () => {
    start({ getModels: async () => ({ models: ROWS, attestation: null }) });
    await connect();
    await ask();

    expect(replies()).toEqual([
      {
        type: "embed.models",
        doc: DOC_A,
        requestId: REQ,
        models: ROWS,
      },
    ]);
    expect(replies()[0]).not.toHaveProperty("attestation");
  });

  it("ignores a request from another origin, another window or for another document", async () => {
    const getModels = vi.fn(async () => listOf(TOKEN));
    start({ getModels });
    await connect();

    await ask(request(DOC_A, "gpt-4o"), { origin: OTHER });
    await ask(request(DOC_A, "gpt-4o"), { source: other.contentWindow });
    await ask(request(DOC_A, "gpt-4o"), { source: window });
    await ask(request(DOC_B, "gpt-4o"));

    expect(getModels).not.toHaveBeenCalled();
    expect(modelPosts()).toEqual([]);
    expect(events).toContainEqual(
      expect.objectContaining({ kind: "rejected", reason: "origin" }),
    );
    expect(events).toContainEqual(
      expect.objectContaining({ kind: "rejected", reason: "source" }),
    );
  });

  it("ignores a request for a document a newer one replaced", async () => {
    const getModels = vi.fn(async () => listOf(TOKEN));
    start({ getModels });
    await connect(DOC_A);
    await connect(DOC_B);

    await ask(request(DOC_A, "gpt-4o"));
    expect(getModels).not.toHaveBeenCalled();

    await ask(request(DOC_B));
    expect(replies().map(({ doc }) => doc)).toEqual([DOC_B]);
  });

  it("ignores a request before the handshake and while a loaded document is unproved", async () => {
    const getModels = vi.fn(async () => listOf(TOKEN));
    start({ getModels });

    await ask(request(DOC_A));
    expect(getModels).not.toHaveBeenCalled();

    await connect();
    bridge.documentLoaded();
    await ask(request(DOC_A, "gpt-4o"));
    expect(getModels).not.toHaveBeenCalled();

    send({
      ...hello(),
      type: "embed.proof",
      challenge: lastProbe(),
      state: "ready",
    });
    await flush();
    await ask(request(DOC_A));
    expect(getModels).toHaveBeenCalledTimes(1);
    expect(replies()).toHaveLength(1);
  });

  it.each([
    ["an extra field", { ...request(DOC_A), models: ["gpt-4o"] }],
    [
      "no doc",
      { type: "embed.models.request", requestId: REQ, model: "gpt-4o" },
    ],
    ["a model that is not a string", { ...request(DOC_A), model: 7 }],
    ["an empty model", { ...request(DOC_A), model: "" }],
  ])("ignores a request with %s", async (_label, data) => {
    const getModels = vi.fn(async () => listOf(TOKEN));
    start({ getModels });
    await connect();

    await ask(data);

    expect(getModels).not.toHaveBeenCalled();
    expect(modelPosts()).toEqual([]);
  });

  it("drops an answer for a document that loaded while it was read", async () => {
    let release;
    const getModels = vi.fn(
      () => new Promise((resolve) => (release = resolve)),
    );
    start({ getModels });
    await connect();
    await ask(request(DOC_A, "gpt-4o"));
    const { signal } = getModels.mock.calls[0][0];

    bridge.documentLoaded();
    expect(signal.aborted).toBe(true);
    release(listOf(TOKEN));
    await flush();

    expect(modelPosts()).toEqual([]);
  });

  it("drops an answer for a document a new one replaced while it was read", async () => {
    let release;
    start({
      getModels: () => new Promise((resolve) => (release = resolve)),
    });
    await connect();
    await ask(request(DOC_A, "gpt-4o"));
    const releaseFirst = release;
    await connect(DOC_B);

    releaseFirst(listOf(TOKEN));
    await flush();

    expect(modelPosts()).toEqual([]);
  });

  it("answers nothing after reset or disposal, even for a read already in flight", async () => {
    let release;
    const getModels = vi.fn(
      () => new Promise((resolve) => (release = resolve)),
    );
    start({ getModels });
    await connect();
    await ask(request(DOC_A, "gpt-4o"));
    bridge.reset();
    release(listOf(TOKEN));
    await flush();
    expect(modelPosts()).toEqual([]);

    await connect(DOC_B);
    await ask(request(DOC_B, "gpt-4o"));
    bridge.dispose();
    release(listOf(TOKEN));
    await flush();
    expect(modelPosts()).toEqual([]);

    const calls = getModels.mock.calls.length;
    await ask(request(DOC_B));
    expect(getModels).toHaveBeenCalledTimes(calls);
    expect(modelPosts()).toEqual([]);
  });

  it.each([
    ["no list", async () => null],
    [
      "rows that are not a list",
      async () => ({ models: {}, attestation: null }),
    ],
    [
      "a loader that throws",
      async () => {
        throw new Error("boom");
      },
    ],
  ])("answers nothing for %s", async (_label, getModels) => {
    start({ getModels });
    await connect();
    await ask(request(DOC_A, "gpt-4o"));

    expect(modelPosts()).toEqual([]);
  });

  it("stays silent without a loader or for a surface that names no document", async () => {
    start();
    await connect();
    await ask();
    expect(modelPosts()).toEqual([]);
    bridge.dispose();

    const getModels = vi.fn(async () => listOf(TOKEN));
    start({ getModels, requireDoc: false });
    send({ type: "embed.hello", protocol: "omega-embed/1" });
    await flush();
    await ask(request(DOC_A, "gpt-4o"));
    expect(getModels).not.toHaveBeenCalled();
    expect(modelPosts()).toEqual([]);
  });

  describe("with the host's own loader", () => {
    const loaderWith = (post, attestation = ATTESTATION) => {
      const get = vi.fn(async () => listAnswer({ models: ROWS, attestation }));
      return { get, getModels: createHostModelsLoader({ get, post }) };
    };

    it("mints nothing for a request without a model", async () => {
      const post = vi.fn(async () => minted());
      const { get, getModels } = loaderWith(post);
      start({ getModels });
      await connect();

      await ask(request(DOC_A));

      expect(get).toHaveBeenCalledTimes(1);
      expect(post).not.toHaveBeenCalled();
      expect(replies()).toEqual([
        {
          type: "embed.models",
          doc: DOC_A,
          requestId: REQ,
          models: ROWS,
          attestation: ATTESTATION,
        },
      ]);
    });

    it("mints exactly the picked model and answers with its authorization", async () => {
      const post = vi.fn(async () => minted());
      const { get, getModels } = loaderWith(post);
      start({ getModels });
      await connect();

      await ask(request(DOC_A, "claude-sonnet-4-5"));

      expect(get).toHaveBeenCalledWith(CONSOLE_MODELS_PATH, expect.anything());
      expect(post).toHaveBeenCalledTimes(1);
      expect(post).toHaveBeenCalledWith(
        RUN_AUTHORIZATIONS_PATH,
        { models: ["claude-sonnet-4-5"] },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
      expect(replies()).toEqual([
        {
          type: "embed.models",
          doc: DOC_A,
          requestId: REQ,
          models: ROWS,
          attestation: ATTESTATION,
          runAuthorization: TOKEN,
        },
      ]);
    });

    it.each([
      [
        "a picked model that is not one of the rows",
        "gpt-5-unlisted",
        ATTESTATION,
      ],
      ["a null attestation", "gpt-4o", null],
    ])(
      "mints nothing for %s and answers with the list alone",
      async (_label, model, attestation) => {
        const post = vi.fn(async () => minted());
        const { getModels } = loaderWith(post, attestation);
        start({ getModels });
        await connect();

        await ask(request(DOC_A, model));

        expect(post).not.toHaveBeenCalled();
        expect(replies()).toEqual([
          {
            type: "embed.models",
            doc: DOC_A,
            requestId: REQ,
            models: ROWS,
            ...(attestation === null ? {} : { attestation }),
          },
        ]);
        if (attestation === null) {
          expect(replies()[0]).not.toHaveProperty("attestation");
        }
      },
    );

    it.each([
      [
        "409 console_run_not_configured",
        () =>
          Promise.reject({
            status: false,
            code: "console_run_not_configured",
            statusCode: 409,
          }),
      ],
      [
        "403",
        () =>
          Promise.reject({
            status: false,
            code: "console_run_disabled",
            statusCode: 403,
          }),
      ],
      ["a network failure", () => Promise.reject({ message: "Network Error" })],
      ["a malformed authorization", () => Promise.resolve(minted("a b"))],
    ])("answers without an authorization on %s", async (_label, outcome) => {
      const post = vi.fn(outcome);
      const { getModels } = loaderWith(post);
      start({ getModels });
      await connect();

      await ask(request(DOC_A, "gpt-4o"));

      expect(post).toHaveBeenCalledTimes(1);
      expect(replies()).toEqual([
        {
          type: "embed.models",
          doc: DOC_A,
          requestId: REQ,
          models: ROWS,
          attestation: ATTESTATION,
        },
      ]);
    });

    it("answers nothing when the list cannot be read", async () => {
      const post = vi.fn(async () => minted());
      const get = vi.fn(async () => {
        throw { status: false, statusCode: 503 };
      });
      start({ getModels: createHostModelsLoader({ get, post }) });
      await connect();

      await ask(request(DOC_A, "gpt-4o"));

      expect(post).not.toHaveBeenCalled();
      expect(modelPosts()).toEqual([]);
    });

    it.each([["minted"], ["refused"]])(
      "keeps the authorization out of console output, storage, URLs and reported events when %s",
      async (label) => {
        const consoleCalls = [];
        for (const name of ["log", "info", "warn", "error", "debug", "trace"]) {
          vi.spyOn(console, name).mockImplementation((...args) =>
            consoleCalls.push(args),
          );
        }
        const pushState = vi.spyOn(window.history, "pushState");
        const replaceState = vi.spyOn(window.history, "replaceState");
        const hrefBefore = window.location.href;
        const post = vi.fn(
          label === "minted"
            ? async () => minted()
            : async () => {
                throw {
                  status: false,
                  code: "console_run_not_configured",
                  statusCode: 409,
                };
              },
        );
        const { get, getModels } = loaderWith(post);
        start({ getModels });

        await connect();
        await ask(request(DOC_A));
        await ask(request(DOC_A, "gpt-4o"));

        const posts = modelPosts();
        expect(posts).toHaveLength(2);
        expect("runAuthorization" in posts[0].message).toBe(false);
        expect("runAuthorization" in posts[1].message).toBe(label === "minted");
        // Only the one answer to the frame carries it.
        const carriers = posted.filter((entry) =>
          JSON.stringify(entry.message).includes(TOKEN),
        );
        expect(carriers).toHaveLength(label === "minted" ? 1 : 0);

        expect(JSON.stringify(consoleCalls)).not.toContain(TOKEN);
        expect(consoleCalls).toEqual([]);
        expect(JSON.stringify(events)).not.toContain(TOKEN);
        for (const storage of [window.localStorage, window.sessionStorage]) {
          for (let i = 0; i < storage.length; i += 1) {
            expect(storage.getItem(storage.key(i)) ?? "").not.toContain(TOKEN);
          }
        }
        expect(window.location.href).toBe(hrefBefore);
        expect(window.location.href).not.toContain(TOKEN);
        expect(pushState).not.toHaveBeenCalled();
        expect(replaceState).not.toHaveBeenCalled();
        for (const [url] of [...get.mock.calls, ...post.mock.calls]) {
          expect(url).not.toContain(TOKEN);
        }
      },
    );
  });

  it("never targets any origin but the frame's exact one", async () => {
    start({ getModels: async () => listOf(TOKEN) });
    await connect();
    await ready();
    await ask(request(DOC_A));
    await ask(request(DOC_A, "gpt-4o"));

    expect(modelPosts()).toHaveLength(2);
    expect(posted.length).toBeGreaterThan(2);
    for (const { targetOrigin } of posted) {
      expect(targetOrigin).toBe(CHILD);
      expect(targetOrigin).not.toBe("*");
    }
  });
});
