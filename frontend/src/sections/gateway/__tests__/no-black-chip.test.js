import { describe, expect, it } from "vitest";

import { lineOf, openingTags, ownProps, sources } from "./source-scan";

// The theme draws MUI's default chip (filled, default
// colour) as a black pill, the last black element on gateway pages (the
// 429/502/504 retry codes on Routing). Gateway chips are the FA outlined
// chip; a chip may fill only in a named colour (a selected filter pill).
describe("gateway chips", () => {
  it("every chip names its variant, and none is the default black fill", () => {
    const offenders = [];
    for (const [file, text] of sources()) {
      for (const tag of openingTags(text, ["Chip"])) {
        const own = ownProps(tag.text);
        const at = `${file}:${lineOf(text, tag.start)}`;
        const variant = own.match(/\bvariant=("[^"]*"|\{\})/)?.[1];
        const color = own.match(/\bcolor=("[^"]*"|\{\})/)?.[1];
        if (!variant) offenders.push(`${at} no variant`);
        else if (variant === '"filled"' && (!color || color === '"default"'))
          offenders.push(`${at} default filled`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
