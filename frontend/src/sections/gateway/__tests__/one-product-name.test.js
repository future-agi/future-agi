import { describe, expect, it } from "vitest";

import { GATEWAY_DIRS, sources } from "./source-scan";
import { gatewayDisplayName } from "../utils/productName";

// The product is the AI Gateway. Its former name appears on no screen: not
// in a string a page shows, nor as the gateway's name in Gateway settings
// (the API still answers the former name for the gateway record; the page
// shows the product's name). Comments may still say where a name came from.
const DIRS = [
  ...GATEWAY_DIRS,
  "pages/dashboard/gateway",
  "routes/sections/dashboard.jsx",
  "layouts/dashboard/config-navigation.jsx",
];

// A line that is a comment: what follows // or a block comment's * gutter.
const isComment = (line) => /^\s*(\/\/|\*|\/\*)/.test(line);

// The one place the former name is spelled out: the mapping that shows the
// product's name where the API still answers the former one.
const MAPPING_FILE = "sections/gateway/utils/productName.js";
const MAPPING_LINE = /^const FORMER_NAME = "Agent Command Center";$/;

// Every non-comment line, in any case, that spells the former name.
function formerNameLines() {
  const found = [];
  for (const [file, text] of sources(DIRS)) {
    const lines = text.split("\n");
    lines.forEach((line, index) => {
      if (/agent\s+command\s+center/i.test(line) && !isComment(line)) {
        found.push({ file, line: index + 1, text: line.trim() });
      }
    });
  }
  return found;
}

describe("one product name", () => {
  it("no gateway screen shows the former product name", () => {
    const offenders = formerNameLines()
      .filter(
        ({ file, text }) => !(file === MAPPING_FILE && MAPPING_LINE.test(text)),
      )
      .map(({ file, line }) => `${file}:${line}`);
    expect(offenders).toEqual([]);
  });

  it("spells the former name once, in the mapping to the product's name", () => {
    expect(
      formerNameLines().filter(({ file }) => file === MAPPING_FILE),
    ).toEqual([
      expect.objectContaining({
        text: 'const FORMER_NAME = "Agent Command Center";',
      }),
    ]);
  });

  it("names the gateway AI Gateway where the API answers the former name", () => {
    expect(gatewayDisplayName("Agent Command Center Gateway")).toBe(
      "AI Gateway",
    );
    expect(gatewayDisplayName("")).toBe("AI Gateway");
    expect(gatewayDisplayName(null)).toBe("AI Gateway");
    // A name an operator gave the gateway is shown as given.
    expect(gatewayDisplayName("eu-west gateway")).toBe("eu-west gateway");
  });
});
