// Execute the actual stand-in inline script, not a second child implementation.
import { Buffer } from "node:buffer";
import { webcrypto } from "node:crypto";
import { TextEncoder } from "node:util";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

import fakeConsole from "../e2e/fake-console.cjs";

const ORIGIN = "http://localhost:3061";
const CHALLENGE = "_____________________w";
const start = ({ want = 4 } = {}) => {
  const html = fakeConsole.fakeConsoleHtml({ want });
  const source = html.slice(
    html.indexOf("<script>") + 8,
    html.lastIndexOf("</script>"),
  );
  const listeners = new Map();
  const parent = { postMessage: vi.fn() };
  const location = {
    origin: ORIGIN,
    hash: "",
    pathname: "/omega/console/",
    search: "",
  };
  const self = {
    parent,
    location,
    addEventListener: (type, handler) => listeners.set(type, handler),
  };
  const clearInterval = vi.fn();
  runInNewContext(source, {
    window: self,
    location,
    history: { replaceState: vi.fn() },
    document: {
      getElementById: () => ({ textContent: "" }),
      querySelectorAll: () => [],
      addEventListener: vi.fn(),
      documentElement: { dataset: {} },
    },
    crypto: webcrypto,
    TextEncoder,
    btoa: (text) => Buffer.from(text, "binary").toString("base64"),
    setInterval: vi.fn(() => 1),
    clearInterval,
  });
  const send = (data, overrides = {}) =>
    listeners.get("message")({
      data,
      source: parent,
      origin: ORIGIN,
      ...overrides,
    });
  const messages = (type) =>
    parent.postMessage.mock.calls.filter(([message]) => message.type === type);
  const hello = messages("embed.hello")[0][0];
  return { self, send, messages, hello, clearInterval };
};

describe("Console e2e stand-in probe/proof contract", () => {
  it("answers a closed probe before any key without inventing ready or stopping hello", async () => {
    const { self, send, messages, hello, clearInterval } = start();
    await send({ type: "embed.probe", challenge: CHALLENGE });
    expect(messages("embed.proof")).toEqual([
      [{ ...hello, type: "embed.proof", challenge: CHALLENGE }, ORIGIN],
    ]);
    expect(messages("embed.gate")).toEqual([]);
    expect(clearInterval).not.toHaveBeenCalled();
    expect(self.__console.keys).toBe(0);
    expect(JSON.stringify(self.__console)).not.toContain(hello.doc);
    expect(JSON.stringify(self.__console)).not.toContain(CHALLENGE);
  });

  it("reports only its actual gate in proof and does not fabricate another ready on refill", async () => {
    const { send, messages, hello } = start();
    await send({ type: "embed.key", token: "key_opening", doc: hello.doc });
    expect(messages("embed.gate")).toHaveLength(1);
    await send({ type: "embed.probe", challenge: CHALLENGE });
    expect(messages("embed.proof")).toEqual([
      [
        { ...hello, type: "embed.proof", challenge: CHALLENGE, state: "ready" },
        ORIGIN,
      ],
    ]);
    await send({ type: "embed.key", token: "key_refill", doc: hello.doc });
    expect(messages("embed.gate")).toHaveLength(1);
    expect(messages("embed.route")).toHaveLength(2);
  });

  it("ignores malformed probes, unknown fields, wrong origins and other windows", async () => {
    const { send, messages } = start();
    for (const data of [
      null,
      ["embed.probe"],
      { type: "embed.probe" },
      { type: "embed.probe", challenge: "bad" },
      { type: "embed.probe", challenge: `${CHALLENGE}\n` },
      { type: "embed.probe", challenge: CHALLENGE, state: "ready" },
    ]) {
      await send(data);
    }
    const probe = { type: "embed.probe", challenge: CHALLENGE };
    await send(probe, { source: {} });
    await send(probe, { origin: "http://localhost:3062" });
    expect(messages("embed.proof")).toEqual([]);
    await send(probe);
    expect(messages("embed.proof")).toHaveLength(1);
  });
});
