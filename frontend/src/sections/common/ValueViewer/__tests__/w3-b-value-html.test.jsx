// W3-2 B4: the HTML view is offered for HTML-like text and renders in an
// empty sandbox with a CSP that loads nothing from the network.
import React from "react";
import { describe, expect, it } from "vitest";
import { render } from "src/utils/test-utils";
import ValueViewer from "../ValueViewer";
import { HTML_VIEW_CSP, htmlViewDocument } from "../HtmlView";
import {
  effectiveMode,
  isHtmlLike,
  modeAvailability,
  VIEW_MODES,
} from "src/sections/common/Values/viewModes";
import { wantsToolbar } from "src/sections/common/Values/useValueView";
import { sniffValue } from "src/sections/common/Values/sniff";

const HTML = "<script>alert(1)</script><b>ok</b>";

describe("W3-2 B4 · HTML view", () => {
  it("knows HTML-like text from prose", () => {
    expect(isHtmlLike(HTML)).toBe(true);
    expect(isHtmlLike("<p>Hello</p>")).toBe(true);
    expect(isHtmlLike('<img src="data:image/png;base64,AA" />')).toBe(true);
    expect(isHtmlLike("a < b and c > d")).toBe(false);
    expect(isHtmlLike("Use <Forgot password> on the login page")).toBe(false);
    expect(isHtmlLike({ html: "<b>x</b>" })).toBe(false);
  });

  it("is available for HTML text only, with a reason otherwise", () => {
    expect(VIEW_MODES).toContain("html");
    expect(modeAvailability(sniffValue(HTML)).html).toBeNull();
    expect(modeAvailability(sniffValue("plain words")).html).toBe(
      "HTML needs text with HTML tags",
    );
    expect(effectiveMode("html", sniffValue("plain words"))).toBe("auto");
    expect(wantsToolbar(sniffValue(HTML), HTML)).toBe(true);
  });

  it("the frame document carries the CSP; the value is the body", () => {
    expect(HTML_VIEW_CSP).toBe(
      "default-src 'none'; img-src data:; style-src 'unsafe-inline'",
    );
    const doc = htmlViewDocument(HTML, { color: "#111" });
    expect(doc).toContain(
      `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">`,
    );
    expect(doc).toContain(`<body>${HTML}</body>`);
  });

  it("renders in an iframe with an empty sandbox (no allow-scripts)", () => {
    const { container } = render(
      <ValueViewer value={HTML} view="html" toolbar={false} />,
    );
    const frame = container.querySelector("iframe");
    expect(frame).not.toBeNull();
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("srcdoc")).toContain("<b>ok</b>");
    expect(container.querySelector("script")).toBeNull();
  });
});
