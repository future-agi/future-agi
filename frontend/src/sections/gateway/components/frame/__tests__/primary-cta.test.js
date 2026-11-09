import { describe, expect, it } from "vitest";

import {
  bracedProp,
  lineOf,
  openingTags,
  ownProps,
  sources,
} from "../../../__tests__/source-scan";

const BUTTONS = ["Button", "LoadingButton"];

// The theme's default button colour is "inherit", which
// draws a contained button black (white in dark). Every native Future AGI
// page draws its primary action purple (color="primary") at the native
// 38 px; the gateway passed color on 2 of its contained buttons.
describe("gateway primary buttons", () => {
  it("every contained button names its colour: primary, or error when destructive", () => {
    const offenders = [];
    for (const [file, text] of sources()) {
      for (const tag of openingTags(text, BUTTONS)) {
        const own = ownProps(tag.text);
        if (!/variant="contained"/.test(own)) continue;
        const color = own.match(/\bcolor=("([^"]*)"|\{)/);
        if (!color || color[2] === "inherit") {
          offenders.push(`${file}:${lineOf(text, tag.start)}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("page-header actions use the native button size, not small", () => {
    const offenders = [];
    for (const [file, text] of sources()) {
      for (const frame of openingTags(text, ["GatewayPageFrame"])) {
        let actions = bracedProp(frame.text, "actions");
        if (!actions) continue;
        // actions={createButton}: the element is defined above in the file.
        const named = actions.match(/^\{\s*([A-Za-z_]\w*)\s*\}$/);
        if (named) {
          const def = text.match(
            new RegExp(`const ${named[1]} = \\(([\\s\\S]*?)\\n  \\);`),
          );
          actions = def ? def[1] : actions;
        }
        for (const tag of openingTags(actions, BUTTONS)) {
          if (/size="small"/.test(ownProps(tag.text))) {
            offenders.push(
              `${file}:${lineOf(text, frame.start)} ${tag.text
                .split("\n")
                .join(" ")
                .slice(0, 60)}`,
            );
          }
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
