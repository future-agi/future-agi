import { describe, expect, it } from "vitest";

import {
  LINK_PROBLEM,
  normalizeStudioServer,
  parseStudioLink,
  serverDisplayName,
  studioDirectHref,
  studioFrameSrc,
} from "../connect-link";

// Deterministic, format-valid test fixture; not a generated access token.
const TOKEN = "studio-fixture-".padEnd(43, "0");

describe("parseStudioLink", () => {
  it("reads the 'Open in Future AGI' link omega dev prints (server unencoded)", () => {
    expect(
      parseStudioLink(
        `http://localhost:3031/dashboard/studio#connect=http://127.0.0.1:4191&t=${TOKEN}`,
      ),
    ).toEqual({
      ok: true,
      url: "http://127.0.0.1:4191",
      origin: "http://127.0.0.1:4191",
      token: TOKEN,
    });
  });

  it("reads an encoded server and token too", () => {
    const link = `https://app.futureagi.com/dashboard/studio#connect=${encodeURIComponent(
      "http://127.0.0.1:4191/",
    )}&t=${encodeURIComponent(TOKEN)}`;
    expect(parseStudioLink(link)).toMatchObject({
      ok: true,
      url: "http://127.0.0.1:4191",
      token: TOKEN,
    });
  });

  it("reads the local link omega dev prints", () => {
    expect(parseStudioLink(`  http://127.0.0.1:4191/#t=${TOKEN}\n`)).toEqual({
      ok: true,
      url: "http://127.0.0.1:4191",
      origin: "http://127.0.0.1:4191",
      token: TOKEN,
    });
  });

  it("takes a bare address, with or without a scheme, and no key", () => {
    expect(parseStudioLink("127.0.0.1:4191")).toEqual({
      ok: true,
      url: "http://127.0.0.1:4191",
      origin: "http://127.0.0.1:4191",
      token: null,
    });
    expect(parseStudioLink("http://localhost:4300/")).toMatchObject({
      ok: true,
      url: "http://localhost:4300",
      token: null,
    });
    expect(parseStudioLink("http://[::1]:4191")).toMatchObject({
      ok: true,
      origin: "http://[::1]:4191",
    });
  });

  it("refuses servers that are not on this computer", () => {
    for (const text of [
      `http://localhost:3031/dashboard/studio#connect=https://evil.example&t=${TOKEN}`,
      `https://studio.example.com/#t=${TOKEN}`,
      "http://192.168.1.20:4191",
      "http://127.0.0.1.evil.example:4191",
    ]) {
      expect(parseStudioLink(text)).toEqual({
        ok: false,
        problem: LINK_PROBLEM.notLocal,
      });
    }
  });

  it("refuses empty and malformed input", () => {
    expect(parseStudioLink("   ")).toEqual({
      ok: false,
      problem: LINK_PROBLEM.empty,
    });
    expect(parseStudioLink(["javascript", "alert(1)"].join(":"))).toEqual({
      ok: false,
      problem: LINK_PROBLEM.invalid,
    });
    expect(
      parseStudioLink("http://localhost:3031/dashboard/studio#connect=&t=x"),
    ).toEqual({ ok: false, problem: LINK_PROBLEM.invalid });
    expect(parseStudioLink("http://user:pw@127.0.0.1:4191")).toEqual({
      ok: false,
      problem: LINK_PROBLEM.invalid,
    });
  });

  it("reads the whole line omega dev printed, link and words around it", () => {
    // omega dev in a terminal with --no-open (studio/one-build, dev-human-print.ts).
    expect(
      parseStudioLink(
        `Studio is ready at http://127.0.0.1:4191/#t=${TOKEN}  (open this link; press o to open it in your browser)`,
      ),
    ).toEqual({
      ok: true,
      url: "http://127.0.0.1:4191",
      origin: "http://127.0.0.1:4191",
      token: TOKEN,
    });
    // Its listing outside a terminal (dev-print.ts), Future AGI link line.
    expect(
      parseStudioLink(
        `Open in Future AGI: https://app.futureagi.com/dashboard/studio#connect=${encodeURIComponent(
          "http://127.0.0.1:4193",
        )}&t=${TOKEN}`,
      ),
    ).toMatchObject({ ok: true, url: "http://127.0.0.1:4193", token: TOKEN });
    // A sentence's closing punctuation is not part of the link.
    expect(
      parseStudioLink(`Open (http://127.0.0.1:4191/#t=${TOKEN}).`),
    ).toMatchObject({ ok: true, token: TOKEN });
    expect(parseStudioLink("see http://[::1]:4191")).toMatchObject({
      ok: true,
      origin: "http://[::1]:4191",
    });
  });

  it("takes the link that carries a key when several lines were pasted", () => {
    const listing = [
      "Omega Studio · running on this machine",
      "Omega Studio listening at http://127.0.0.1:4193",
      `Open http://127.0.0.1:4193/#t=${TOKEN}`,
      "  The link carries this start's key.",
      "API http://127.0.0.1:4193/studio/v1 (Authorization: Bearer <key>) · docs http://127.0.0.1:4193/docs",
    ].join("\n");
    expect(parseStudioLink(listing)).toEqual({
      ok: true,
      url: "http://127.0.0.1:4193",
      origin: "http://127.0.0.1:4193",
      token: TOKEN,
    });
  });

  it("calls text that is not an address 'not a link', not 'not on this computer'", () => {
    // AUDIT 2.6 row 21: 'hello world' got the not-local message in Chromium,
    // whose URL parser accepts a space in a host.
    for (const text of [
      "hello world",
      "hello",
      "studio",
      "omega dev",
      "not a link at all",
    ]) {
      expect(parseStudioLink(text)).toEqual({
        ok: false,
        problem: LINK_PROBLEM.invalid,
      });
    }
    // An address that is real but elsewhere is still 'not on this computer'.
    for (const text of ["example.com", "studio.example.com:4191", "10.0.0.5"]) {
      expect(parseStudioLink(text)).toEqual({
        ok: false,
        problem: LINK_PROBLEM.notLocal,
      });
    }
    expect(parseStudioLink("localhost")).toMatchObject({
      ok: true,
      url: "http://localhost",
    });
  });

  it("still calls 'hello world' not a link where the URL parser accepts a space in a host, as Chromium's does", () => {
    // Node's URL refuses "http://hello world"; Chromium's parses it with the
    // host "hello%20world", which is how row 21 reached "not on this computer".
    const RealURL = globalThis.URL;
    class ChromiumLikeURL extends RealURL {
      constructor(input, base) {
        const spaced = /^(https?:\/\/)([^/?#]*\s[^/?#]*)(.*)$/i.exec(
          String(input),
        );
        if (!spaced) {
          super(input, base);
          return;
        }
        super(`${spaced[1]}${spaced[2].replace(/\s/g, "-")}${spaced[3]}`, base);
        const host = spaced[2].replace(/\s/g, "%20");
        Object.defineProperty(this, "hostname", { get: () => host });
        Object.defineProperty(this, "host", { get: () => host });
      }
    }
    globalThis.URL = ChromiumLikeURL;
    try {
      expect(new URL("http://hello world").hostname).toBe("hello%20world");
      for (const text of ["hello world", "not a link at all"]) {
        expect(parseStudioLink(text)).toEqual({
          ok: false,
          problem: LINK_PROBLEM.invalid,
        });
      }
    } finally {
      globalThis.URL = RealURL;
    }
  });

  it("drops an oversized or undecodable key instead of passing it on", () => {
    expect(
      parseStudioLink(`http://127.0.0.1:4191/#t=${"a".repeat(5000)}`).token,
    ).toBeNull();
    expect(parseStudioLink("http://127.0.0.1:4191/#t=%E0%A4%A").token).toBe(
      null,
    );
  });

  it("checks the key on the 'Open in Future AGI' link the same way", () => {
    const link = (t) =>
      `http://localhost:3031/dashboard/studio#connect=http://127.0.0.1:4191&t=${t}`;
    for (const bad of ["a".repeat(5000), "%E0%A4%A", ""]) {
      expect(parseStudioLink(link(bad))).toEqual({
        ok: true,
        url: "http://127.0.0.1:4191",
        origin: "http://127.0.0.1:4191",
        token: null,
      });
    }
    expect(parseStudioLink(link(encodeURIComponent(TOKEN))).token).toBe(TOKEN);
  });
});

describe("server helpers", () => {
  it("normalizes to scheme, host, port and path only", () => {
    expect(
      normalizeStudioServer("http://127.0.0.1:4191/studio/?x=1#t=secret"),
    ).toEqual({
      url: "http://127.0.0.1:4191/studio",
      origin: "http://127.0.0.1:4191",
    });
    expect(normalizeStudioServer("ftp://127.0.0.1:4191")).toBeNull();
  });

  it("refuses a server address that carries a user name or password", () => {
    expect(normalizeStudioServer("http://user:pw@127.0.0.1:4191")).toBeNull();
    expect(normalizeStudioServer("http://user@localhost:4191")).toBeNull();
    expect(
      parseStudioLink(
        `http://localhost:3031/dashboard/studio#connect=http://user:pw@127.0.0.1:4191&t=${TOKEN}`,
      ).ok,
    ).toBe(false);
  });

  it("frames Studio's page in embed mode and opens it directly without a key", () => {
    expect(studioFrameSrc("http://127.0.0.1:4191")).toBe(
      "http://127.0.0.1:4191/?embed=1",
    );
    expect(studioDirectHref("http://127.0.0.1:4191/")).toBe(
      "http://127.0.0.1:4191/",
    );
    expect(serverDisplayName("http://127.0.0.1:4191")).toBe("127.0.0.1:4191");
  });
});
