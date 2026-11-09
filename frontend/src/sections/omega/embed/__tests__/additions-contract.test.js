// The omega-embed/1 additions (CP P7, P8, P15, P19) as the host reads them.
// omega-embed-1.additions.json is a byte copy of the EE Console's file
// (apps/console/src/shared/embed/ at ee feat/oc-embed-doc-nonce-1101 1894db4e90); the digest
// below is that file's, so a change on either side fails here until both
// copies move together.
import { createHash } from "node:crypto";
import { Buffer } from "node:buffer";
import { describe, expect, it } from "vitest";

import additions from "../omega-embed-1.additions.json";
import additionsBytes from "../omega-embed-1.additions.json?raw";
import {
  isRouteTemplate,
  keysWanted,
  LEFT_MAX,
  TELEMETRY_ERROR_NAMES,
  TELEMETRY_VITAL_NAMES,
  WANT_MAX,
  WANT_MIN,
} from "../additions";
import {
  documentProbeMessage,
  keyMessage,
  parseChildMessage,
} from "../protocol";

const EE_ADDITIONS_SHA256 =
  "54f6be7970c34e9a85a9f415dd5f2f7deb55d6b1c05a4e8cf7791b62296a7461";
const telemetry = additions.messages["embed.telemetry"].variants;
const ERROR = telemetry.error.example;
const VITAL = telemetry.vital.example;

// Discover examples, including variants and exampleWith*/exampleWithout*.
// A new published example cannot silently fall outside a hand-picked list.
const examplesIn = (value, path = []) =>
  Object.entries(value).flatMap(([key, entry]) => {
    if (key.startsWith("example")) {
      return [{ path: [...path, key], example: entry }];
    }
    return entry && typeof entry === "object"
      ? examplesIn(entry, [...path, key])
      : [];
  });
const publishedExamples = Object.entries(additions.messages).flatMap(
  ([type, contract]) =>
    examplesIn(contract).map(({ path, example }) => ({
      label: [type, ...path].join("."),
      type,
      direction: contract.direction,
      example,
    })),
);
const parentBuilders = {
  "embed.key": (example) =>
    keyMessage(example.token, example.left, example.doc),
  "embed.probe": (example) => documentProbeMessage(example.challenge),
};

describe("omega-embed/1 additions contract", () => {
  it("is byte-identical to the EE Console's published copy", () => {
    const digest = createHash("sha256")
      .update(Buffer.from(additionsBytes, "utf8"))
      .digest("hex");
    expect(digest).toBe(EE_ADDITIONS_SHA256);
    expect(additions.protocol).toBe("omega-embed/1");
  });

  it("discovers examples for every published message and variant", () => {
    expect(new Set(publishedExamples.map(({ type }) => type))).toEqual(
      new Set(Object.keys(additions.messages)),
    );
    expect(publishedExamples).toHaveLength(12);
    expect(publishedExamples.map(({ label }) => label)).toContain(
      "embed.proof.exampleWithoutState",
    );
  });

  it.each(publishedExamples)(
    "$direction $label is checked exactly as published (or explicitly ignored)",
    ({ type, direction, example }) => {
      expect(example.type).toBe(type);
      expect(["child-to-parent", "parent-to-child"]).toContain(direction);
      if (type === "embed.score") {
        // The fixture explicitly allows a non-scoring host: it MUST send no
        // answer. FA has no viewer-attributed score writer in this bridge;
        // do not parse/forward the viewer's free text or pretend it was saved.
        expect(direction).toBe("child-to-parent");
        expect(additions.messages[type].rule).toContain(
          "A host that ignores it sends no answer",
        );
        expect(parseChildMessage(example)).toBeNull();
      } else if (direction === "child-to-parent") {
        expect(parseChildMessage(example)).toEqual(example);
      } else {
        expect(parseChildMessage(example)).toBeNull();
        if (type === "embed.scored") {
          // The response to the deliberately ignored score is not sent by
          // this host. It is parent-only, never a child admission message.
          expect(direction).toBe("parent-to-child");
          expect(additions.messages[type].rule).toContain(
            "The host's answer to one embed.score",
          );
          expect(parentBuilders[type]).toBeUndefined();
        } else {
          expect(parentBuilders[type]).toBeTypeOf("function");
          expect(parentBuilders[type](example)).toEqual(example);
        }
      }
    },
  );

  it("takes names and bounds from the file, not from hand-written copies", () => {
    expect(TELEMETRY_ERROR_NAMES).toEqual(telemetry.error.fields.name);
    expect(TELEMETRY_VITAL_NAMES).toEqual(telemetry.vital.fields.name);
    expect([WANT_MIN, WANT_MAX]).toEqual([2, 8]);
    expect(LEFT_MAX).toBe(7);
    for (const code of telemetry.error.fields.code.or) {
      expect(parseChildMessage({ ...ERROR, code })).toMatchObject({ code });
    }
  });
});

describe("hello additions", () => {
  const base = { type: "embed.hello", protocol: "omega-embed/1" };

  it("keeps a valid build and want, and drops anything else", () => {
    expect(
      parseChildMessage({ ...base, build: "r9-2803ad68", want: 4 }),
    ).toEqual({ ...base, build: "r9-2803ad68", want: 4 });
    for (const build of ["", "-x", "a b", "x".repeat(65), 7, "a/b"]) {
      expect(parseChildMessage({ ...base, build })).toEqual(base);
    }
    for (const want of [0, 1, 9, 2.5, -2, "4", null, Number.NaN]) {
      expect(parseChildMessage({ ...base, want })).toEqual(base);
    }
  });

  it("asks for one key unless want is a count of 2-8", () => {
    expect(keysWanted(base)).toBe(1);
    expect(keysWanted({ ...base, want: 8 })).toBe(8);
    expect(keysWanted({ ...base, want: 9 })).toBe(1);
    expect(keysWanted(null)).toBe(1);
  });
});

describe("embed.key left", () => {
  it("carries left 1-7 and leaves it out on the last key", () => {
    expect(keyMessage("t", 7)).toEqual({
      type: "embed.key",
      token: "t",
      left: 7,
    });
    for (const left of [0, 8, -1, 1.5, undefined, "2"]) {
      expect(keyMessage("t", left)).toEqual({ type: "embed.key", token: "t" });
    }
  });
});

describe("embed.telemetry", () => {
  it("drops a product event and any kind the contract does not publish (CONS2)", () => {
    expect(
      parseChildMessage({ type: "embed.telemetry", kind: "event", name: "x" }),
    ).toBeNull();
    expect(parseChildMessage({ type: "embed.telemetry" })).toBeNull();
  });

  it("drops every field outside the closed vocabulary", () => {
    const extra = parseChildMessage({
      ...ERROR,
      message: "Cannot read properties of undefined (reading 'name')",
      stack: "at Agent (agents.tsx:1)",
      path: "/agents/agt-7",
      email: "a@example.com",
    });
    expect(extra).toEqual(ERROR);
    expect(parseChildMessage({ ...ERROR, name: "crash" })).toBeNull();
    expect(parseChildMessage({ ...ERROR, code: "TypeError" })).toBeNull();
    expect(parseChildMessage({ ...ERROR, code: "http_600" })).toBeNull();
    expect(parseChildMessage({ ...ERROR, release: "a b" })).toBeNull();
    // A request id that is not one goes; the report stays.
    expect(parseChildMessage({ ...ERROR, requestId: "short" })).toEqual({
      ...ERROR,
      requestId: undefined,
    });
    expect(parseChildMessage({ ...VITAL, valueMs: 600001 })).toBeNull();
    expect(parseChildMessage({ ...VITAL, valueMs: 1.5 })).toBeNull();
    expect(parseChildMessage({ ...VITAL, name: "lcp" })).toBeNull();
  });

  it("accepts page templates only, never a concrete path", () => {
    for (const template of [
      "*",
      "/",
      "/agents",
      "/agents/:agentId",
      "/get-started",
      "/settings/:section/members",
    ]) {
      expect(isRouteTemplate(template)).toBe(true);
    }
    for (const path of [
      "/agents/agt-7",
      "/agents/3f2a9c1e-0b7d-4c3b-8e5f-6f1c2b9e0a7d",
      "/chat/c1",
      "agents",
      "/agents/",
      "/agents//x",
      "/agents?tab=1",
      "/Agents",
      `/${"a/".repeat(9)}a`,
      42,
    ]) {
      expect(isRouteTemplate(path)).toBe(false);
      expect(parseChildMessage({ ...ERROR, routeTemplate: path })).toBeNull();
    }
  });
});

describe("embed.badge", () => {
  it("reads a count of 0-9999 and nothing else", () => {
    expect(parseChildMessage({ type: "embed.badge", inbox: 0 })).toEqual({
      type: "embed.badge",
      inbox: 0,
    });
    expect(parseChildMessage({ type: "embed.badge", inbox: 9999 })).toEqual({
      type: "embed.badge",
      inbox: 9999,
    });
    for (const inbox of [-1, 10000, 1.5, "3", null]) {
      expect(parseChildMessage({ type: "embed.badge", inbox })).toBeNull();
    }
  });
});
