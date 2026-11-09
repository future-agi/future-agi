import { Buffer } from "node:buffer";
import { createHash, randomBytes } from "node:crypto";
import { describe, expect, it } from "vitest";

import fixture from "../omega-embed-1.json";
import hostAdditions from "../omega-embed-1.host-additions.json";
import hostAdditionsBytes from "../omega-embed-1.host-additions.json?raw";
import {
  EMBED_GATE_STATES,
  EMBED_MESSAGE,
  EMBED_PROTOCOL,
  EMBED_THEME_MODES,
  keyMessage,
  navigateMessage,
  normalizeEmbedPath,
  parseChildMessage,
  sameOriginUrl,
  themeMessage,
} from "../protocol";

// Document nonces (RL 1275A): the Console's published example, and another.
const DOC = "AAECAwQFBgcICQoLDA0ODw";
const OTHER_DOC = "m4gLqkrE04O3BMKxGKKPFg";
import {
  buildFrameSrc,
  childPathFromParent,
  parentPathFromChild,
} from "../route-sync";

// Built at runtime so the linter does not read it as code.
const SCRIPT_URL = ["javascript", "alert(1)"].join(":");

describe("omega-embed/1 protocol", () => {
  it("names every message in the shared fixture", () => {
    expect(EMBED_PROTOCOL).toBe("omega-embed/1");
    expect(Object.values(EMBED_MESSAGE).sort()).toEqual(
      [
        ...new Set(
          [...fixture.childToParent, ...fixture.parentToChild].map(
            ({ type }) => type,
          ),
        ),
      ].sort(),
    );
    expect(EMBED_GATE_STATES).toEqual(["needs-key", "refused", "ready"]);
    expect(EMBED_THEME_MODES).toEqual(["light", "dark"]);
    fixture.childToParent.forEach((example) => {
      expect(parseChildMessage(example)).toEqual(example);
    });
    const parentBuilders = {
      [EMBED_MESSAGE.key]: (example) => keyMessage(example.token),
      [EMBED_MESSAGE.navigate]: (example) => navigateMessage(example.path),
      [EMBED_MESSAGE.theme]: (example) => themeMessage(example.mode),
    };
    fixture.parentToChild.forEach((example) => {
      expect(parseChildMessage(example)).toBeNull();
      expect(parentBuilders[example.type]).toBeTypeOf("function");
      expect(parentBuilders[example.type](example)).toEqual(example);
    });
    expect(themeMessage("system").mode).toBe("light");
  });

  it("drops unknown fields and unknown or parent-only types", () => {
    expect(
      parseChildMessage({
        type: "embed.hello",
        protocol: "omega-embed/1",
        token: "x",
      }),
    ).toEqual({ type: "embed.hello", protocol: "omega-embed/1" });
    expect(parseChildMessage({ type: "embed.key", token: "x" })).toBeNull();
    expect(
      parseChildMessage({ type: "embed.navigate", path: "/a" }),
    ).toBeNull();
    expect(parseChildMessage({ type: "embed.gate", state: "open" })).toBeNull();
    expect(parseChildMessage(["embed.hello"])).toBeNull();
  });

  it("accepts only single-slash relative paths", () => {
    expect(normalizeEmbedPath("/runs/1?tab=trace")).toBe("/runs/1?tab=trace");
    [
      "",
      "runs",
      "//evil.example",
      "/\\evil.example",
      "https://evil.example",
      "/a\nb",
      `/${"x".repeat(3000)}`,
      42,
    ].forEach((bad) => expect(normalizeEmbedPath(bad)).toBeNull());
  });

  it("retains the host's path and href bounds outside the canonical fixture", () => {
    expect(hostAdditions.protocol).toBe(EMBED_PROTOCOL);
    expect(hostAdditions.path.maxLength).toBe(2048);
    const path = `/${"x".repeat(2047)}`;
    expect(normalizeEmbedPath(path)).toBe(path);
    expect(normalizeEmbedPath(`${path}x`)).toBeNull();
    expect(parseChildMessage({ type: "embed.route", path })).toEqual({
      type: "embed.route",
      path,
    });
    expect(
      parseChildMessage({ type: "embed.route", path: `${path}x` }),
    ).toBeNull();
    expect(parseChildMessage({ type: "embed.open", href: path })).toEqual({
      type: "embed.open",
      href: path,
    });
    for (const href of ["", `${path}x`, 42, null]) {
      expect(parseChildMessage({ type: "embed.open", href })).toBeNull();
    }
    for (const bad of ["/a\\b", "/a\u0000b", "/a\u007fb", "//evil.example"]) {
      expect(normalizeEmbedPath(bad)).toBeNull();
      expect(parseChildMessage({ type: "embed.route", path: bad })).toBeNull();
    }
    for (const state of ["open", "READY", "", null, 0]) {
      expect(parseChildMessage({ type: "embed.gate", state })).toBeNull();
    }
    expect(
      parseChildMessage({ type: "embed.hello", protocol: "omega-embed/2" }),
    ).toBeNull();
    expect(parseChildMessage({ type: "embed.unknown" })).toBeNull();
    expect(parseChildMessage(null)).toBeNull();
  });

  it("keeps the doc bounds in the host additions: base64url of 16 bytes (RL 1275A)", () => {
    expect(hostAdditions.doc).toMatchObject({
      type: "string",
      bytes: 16,
      // The EE Console's DOC_PATTERN (apps/console/src/shared/embed/protocol.ts).
      pattern: "^[A-Za-z0-9_-]{21}[AQgw]$",
      on: ["embed.hello", "embed.gate", "embed.key", "embed.proof"],
      admission: "eager-hello-load-probe",
      retirement: "bridge-lifetime",
    });
    const pattern = new RegExp(hostAdditions.doc.pattern);
    // The Console's published example is bytes 0..15.
    expect(Buffer.from(DOC, "base64url")).toEqual(
      Buffer.from([...Array(16).keys()]),
    );
    // Every 16-byte draw encodes to a doc, and only 16-byte draws do.
    for (let i = 0; i < 64; i += 1) {
      const bytes = randomBytes(16);
      expect(pattern.test(bytes.toString("base64url"))).toBe(true);
    }
    expect(pattern.test(randomBytes(15).toString("base64url"))).toBe(false);
    expect(pattern.test(randomBytes(17).toString("base64url"))).toBe(false);
  });

  it("pins the host contract and describes eager replacement and live load revalidation", () => {
    expect(
      createHash("sha256").update(hostAdditionsBytes, "utf8").digest("hex"),
    ).toBe("bedd1f4910615b0a792ea53b98ad037ca407e6f4a6c9fcbdd30c4bfb73936e0f");
    const { rule } = hostAdditions.doc;
    for (const fact of [
      "non-retired hello replaces the current document immediately",
      "receives keys without waiting for load",
      "load suspends readiness without retiring the candidate nonce",
      "invalidates any prior proof or challenge",
      "fresh, one-use embed.probe challenge answered by embed.proof",
      "frame's own window at its exact expected origin",
      "queued hello/gate cannot resume it",
      "ready is retained across failed remints",
      "only after a successful remint",
      "newer gates update that verdict during and between attempts",
      "retires its predecessor for the bridge lifetime, with no eviction",
      "Studio does not participate in probe/proof",
    ]) {
      expect(rule).toContain(fact);
    }
    expect(rule).not.toContain("early hello must repeat");
    expect(rule).not.toContain("mismatched hello never replaces");
    expect(rule).not.toContain("Every iframe load starts a generation");
  });

  it("keeps a valid doc on hello and gate, and reads it on no other base message", () => {
    const hello = { type: "embed.hello", protocol: "omega-embed/1", doc: DOC };
    expect(parseChildMessage(hello)).toEqual(hello);
    expect(
      parseChildMessage({ ...hello, build: "r9-2803ad68", want: 4 }),
    ).toEqual({ ...hello, build: "r9-2803ad68", want: 4 });
    const gate = { type: "embed.gate", state: "ready", doc: OTHER_DOC };
    expect(parseChildMessage(gate)).toEqual(gate);
    // Studio sends no doc yet: a hello or gate without one stays as it was.
    expect(
      parseChildMessage({ type: "embed.gate", state: "ready", doc: undefined }),
    ).toEqual({ type: "embed.gate", state: "ready" });
    // No other message carries a doc; one that does just loses the field.
    expect(
      parseChildMessage({ type: "embed.route", path: "/a", doc: DOC }),
    ).toEqual({ type: "embed.route", path: "/a" });
    expect(
      parseChildMessage({ type: "embed.badge", inbox: 1, doc: DOC }),
    ).toEqual({ type: "embed.badge", inbox: 1 });
  });

  it("makes a hello or gate with a malformed doc invalid (strict parse)", () => {
    const bad = [
      DOC.slice(1), // 21 characters
      `${DOC}A`, // 23 characters
      `${DOC}\n`, // RegExp '$' alone also matches before a final newline
      `${DOC}\r\n`,
      `${DOC.slice(0, 21)}B`, // a last character that 16 bytes cannot end on
      `${DOC.slice(0, 20)}+A`, // standard base64, not base64url
      `${DOC.slice(0, 20)}/A`,
      `${DOC}==`, // padded
      `${DOC.slice(0, 21)}=`,
      ` ${DOC.slice(1)}`,
      `${DOC.slice(0, 21)}\n`,
      "",
      7,
      null,
      true,
      {},
      [DOC],
    ];
    for (const doc of bad) {
      expect(
        parseChildMessage({
          type: "embed.hello",
          protocol: "omega-embed/1",
          doc,
        }),
      ).toBeNull();
      expect(
        parseChildMessage({ type: "embed.gate", state: "ready", doc }),
      ).toBeNull();
    }
  });

  it("fails closed on unknown fields or malformed additions in a doc-bearing handshake", () => {
    const hello = { type: "embed.hello", protocol: "omega-embed/1", doc: DOC };
    const gate = { type: "embed.gate", state: "ready", doc: DOC };
    for (const extra of [
      { token: "x" },
      { generation: 7 },
      { state: "ready" },
    ]) {
      expect(parseChildMessage({ ...hello, ...extra })).toBeNull();
    }
    for (const extra of [
      { token: "x" },
      { protocol: "omega-embed/1" },
      { want: 2 },
    ]) {
      expect(parseChildMessage({ ...gate, ...extra })).toBeNull();
    }
    for (const extra of [
      { build: 7 },
      { build: "" },
      { want: "2" },
      { want: 0 },
      { want: 9 },
    ]) {
      expect(parseChildMessage({ ...hello, ...extra })).toBeNull();
    }
    expect(
      parseChildMessage({ ...hello, build: "r9-2803ad68", want: 2 }),
    ).toEqual({ ...hello, build: "r9-2803ad68", want: 2 });
    // Studio's legacy strip-unknown-fields behavior is intentionally unchanged.
    expect(
      parseChildMessage({
        type: "embed.hello",
        protocol: "omega-embed/1",
        extra: 1,
      }),
    ).toEqual({ type: "embed.hello", protocol: "omega-embed/1" });
  });

  it("echoes a doc on the key, on every key of an answer", () => {
    expect(keyMessage("t", 3, DOC)).toEqual({
      type: "embed.key",
      token: "t",
      left: 3,
      doc: DOC,
    });
    expect(keyMessage("t", 0, DOC)).toEqual({
      type: "embed.key",
      token: "t",
      doc: DOC,
    });
    // No doc to echo (Studio), or one that is not a doc: the key as before.
    expect(keyMessage("t", 0)).toEqual({ type: "embed.key", token: "t" });
    expect(keyMessage("t", 0, "not-a-doc")).toEqual({
      type: "embed.key",
      token: "t",
    });
  });

  it("opens only same-origin http(s) URLs", () => {
    const own = "https://app.futureagi.com";
    expect(sameOriginUrl("/dashboard/observe", own)?.href).toBe(
      `${own}/dashboard/observe`,
    );
    expect(sameOriginUrl("https://evil.example/", own)).toBeNull();
    expect(sameOriginUrl(SCRIPT_URL, own)).toBeNull();
    expect(sameOriginUrl("//evil.example/x", own)).toBeNull();
    expect(sameOriginUrl("http://127.0.0.1:4190/", own)).toBeNull();
  });
});

describe("route sync", () => {
  const base = "/dashboard/studio";

  it("maps the parent URL under the base path to the child route", () => {
    expect(childPathFromParent("/dashboard/studio", "", base)).toBe("/");
    expect(childPathFromParent("/dashboard/studio/", "", base)).toBe("/");
    expect(
      childPathFromParent("/dashboard/studio/runs/1", "?tab=t", base),
    ).toBe("/runs/1?tab=t");
    expect(childPathFromParent("/dashboard/studios", "", base)).toBe("/");
    expect(childPathFromParent("/elsewhere", "", base)).toBe("/");
  });

  it("maps a child route back to the parent URL", () => {
    expect(parentPathFromChild(base, "/")).toBe("/dashboard/studio");
    expect(parentPathFromChild(`${base}/`, "/runs/1?tab=t")).toBe(
      "/dashboard/studio/runs/1?tab=t",
    );
    expect(parentPathFromChild(base, "//evil.example")).toBe(
      "/dashboard/studio",
    );
  });

  it("round-trips", () => {
    ["/", "/runs/1", "/runs/1?tab=trace&x=1"].forEach((route) => {
      const parent = parentPathFromChild(base, route);
      const [pathname, search = ""] = parent.split(/(?=\?)/);
      expect(childPathFromParent(pathname, search, base)).toBe(route);
    });
  });

  it("builds the first frame URL for hash and path children", () => {
    expect(
      buildFrameSrc("http://127.0.0.1:4190/?embed=1", "/runs/1", "hash"),
    ).toBe("http://127.0.0.1:4190/?embed=1#/runs/1");
    expect(buildFrameSrc("http://127.0.0.1:4190/?embed=1", "/", "hash")).toBe(
      "http://127.0.0.1:4190/?embed=1",
    );
    expect(
      buildFrameSrc("/omega/console/?embed=1", "/agents/a1?tab=runs", "path"),
    ).toBe(
      `${window.location.origin}/omega/console/agents/a1?embed=1&tab=runs`,
    );
  });
});
