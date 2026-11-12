import { describe, expect, it } from "vitest";

import { GATEWAY_MONO } from "../utils/fonts";
import {
  bracedProp,
  lineOf,
  openingTags,
  ownProps,
  sources,
} from "./source-scan";

// A bare "monospace" is Courier on many machines, a
// typewriter serif next to Inter. Gateway code uses GATEWAY_MONO, the stack
// Future AGI's Evals code editor already uses (without its Courier New).
describe("gateway monospace", () => {
  it("is one constant with a modern mono first and the generic family last", () => {
    expect(GATEWAY_MONO).toBe(
      "'Fira Code', 'Cascadia Code', 'JetBrains Mono', Menlo, Monaco, monospace",
    );
    expect(GATEWAY_MONO).not.toMatch(/Courier/);
  });

  it("no gateway source sets a bare monospace font family", () => {
    const offenders = [];
    for (const [file, text] of sources()) {
      for (const match of text.matchAll(/["'`]monospace["'`]/g)) {
        offenders.push(`${file}:${lineOf(text, match.index)}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  // The browser's own style gives <code> and <pre> the
  // generic monospace (Courier), so a <code> inside a GATEWAY_MONO block
  // still drew Courier. Every code element names the font itself.
  it("every <code> and <pre> sets GATEWAY_MONO itself", () => {
    const offenders = [];
    for (const [file, text] of sources()) {
      // Same-file style objects that carry the font (`const mono = {...}`).
      const monoConsts = new Set(
        [...text.matchAll(/const (\w+) = \{[^}]*GATEWAY_MONO/g)].map(
          (m) => m[1],
        ),
      );
      for (const match of text.matchAll(/<(code|pre)[\s>]/g)) {
        offenders.push(`${file}:${lineOf(text, match.index)} <${match[1]}>`);
      }
      for (const tag of openingTags(text, ["[A-Z]\\w*"])) {
        // The tag's own props: a code element nested in a prop is its own tag.
        if (!/component=["'`](code|pre)["'`]/.test(ownProps(tag.text)))
          continue;
        const sx = bracedProp(tag.text, "sx") || "";
        const spreads = [...sx.matchAll(/\.\.\.(\w+)/g)].map((m) => m[1]);
        const hasFont =
          /fontFamily:\s*GATEWAY_MONO/.test(sx) ||
          spreads.some((name) => monoConsts.has(name));
        if (!hasFont) offenders.push(`${file}:${lineOf(text, tag.start)}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

// The keys and providers screens set monospace text at raw
// 12 / 13 px instead of an FA typography token (s2, s2_1).
describe("keys and providers monospace size", () => {
  const FILES = [
    "sections/gateway/keys/OneTimeKeyReveal.jsx",
    "sections/gateway/keys/KeyDetailDrawer.jsx",
    "sections/gateway/keys/KeyManagementSection.jsx",
    "sections/gateway/providers/ProviderList.jsx",
    "sections/gateway/components/frame/DetailRows.jsx",
  ];

  it("takes its size from a typography token, never a raw px size", () => {
    const offenders = [];
    for (const [file, text] of sources(FILES)) {
      for (const match of text.matchAll(/fontSize:\s*["'`]\d+px["'`]/g)) {
        offenders.push(`${file}:${lineOf(text, match.index)}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
