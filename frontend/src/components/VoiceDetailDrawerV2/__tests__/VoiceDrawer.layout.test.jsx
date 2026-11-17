import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTheme } from "@mui/material/styles";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { shadows } from "src/theme/shadows";
import { customShadows } from "src/theme/custom-shadows";
import { componentsOverrides } from "src/theme/overrides";
import { dsShape } from "src/theme/ds-tokens";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import TranscriptView from "../TranscriptView";
import CompactTabs from "../CompactTabs";
import useVoiceAudioStore from "../voiceAudioStore";

// The voice call drawer at narrow widths, where the transcript and right
// panes are under 300 px wide: the transcript filter pills wrap instead of
// clipping, the right-pane tabs stay readable, a turn without times has no
// empty band above its text, and in dark theme the assistant turns' accent
// bar is a solid colour.
//
// jsdom has no layout, and its getComputedStyle applies rules in sheet
// order without specificity (MUI's own Tab rule, inserted after the parent's
// descendant rule, would win there though not in a browser). So these tests
// read the style rules the fix injects, the classes MUI sets, and the DOM
// structure, never computed styles or sizes.
//
// The tab strip is checked under the app theme too: its MuiTabs defaults
// (theme/overrides/default-props.jsx) make every Tabs scrollable, which the
// test-utils theme does not, and CompactTabs must look the same under both.

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/sections/common/CellMarkdown", async (importOriginal) => ({
  ...(await importOriginal()),
  // eslint-disable-next-line react/prop-types
  default: function MarkdownText({ text }) {
    return <div>{text}</div>;
  },
}));

// The theme the app runs with, built the way theme/index.jsx builds it.
const appTheme = createTheme({
  palette: palette("light"),
  typography,
  shadows: shadows("light"),
  customShadows: customShadows("light"),
  shape: dsShape,
});
appTheme.components = componentsOverrides(appTheme);

// An OTLP call: the transcript says who said what, with no time or
// duration per turn.
const walkTranscript = [
  {
    role: "assistant",
    content: "Hi, thanks for calling Acme support. How can I help?",
  },
  { role: "user", content: "I want to check my order status." },
  { role: "assistant", content: "Your order A-1001 has shipped." },
];

// An Observe provider transcript: ISO time and duration in seconds.
const timedTranscript = [
  {
    speaker_role: "assistant",
    content: "Hello there",
    time: "2026-11-09T12:00:05.000Z",
    duration: 2,
  },
];

const PILLS = ["All", "Assistant", "Customer"];

// The right pane's tabs for an Observe call (no Scenario tab there).
const rightPaneTabs = [
  {
    label: "Call Analytics",
    value: "analytics",
    icon: "solar:graph-up-linear",
  },
  { label: "Evals", value: "evals", icon: "solar:check-circle-linear" },
  {
    label: "Messages",
    value: "messages",
    icon: "solar:chat-round-line-linear",
  },
  { label: "Logs", value: "logs", icon: "solar:list-linear" },
  { label: "Attributes", value: "attributes", icon: "solar:code-linear" },
  { label: "Annotations", value: "annotations", icon: "solar:pen-linear" },
];

// The fixture owns these tab glyphs; register them as the panel's owner
// does, or an unregistered glyph adds "Missing offline icon" to the name.
for (const { icon } of rightPaneTabs) {
  registerOfflineIcon(icon, bundled[icon]);
}

// MUI 5.15 shows a scrollable strip's arrows when an IntersectionObserver on
// its first and last tab reports one of them out of view (jsdom has no
// layout, and setupTests stubs the observer as a no-op). This stand-in
// reports the last tab cut off, as in a pane dragged narrower than one tab.
class LastTabCutOffObserver {
  constructor(callback) {
    this.callback = callback;
  }

  observe(el) {
    const isLast = el === el.parentElement?.lastElementChild;
    this.callback([{ target: el, isIntersecting: !isLast }]);
  }

  unobserve() {}

  disconnect() {}
}

// Every style rule in the document whose selector matches the element.
const rulesMatching = (el) => {
  const found = [];
  const visit = (rules) => {
    for (const rule of Array.from(rules ?? [])) {
      if (rule.cssRules) visit(rule.cssRules);
      if (!rule.selectorText || !rule.style) continue;
      try {
        if (el.matches(rule.selectorText)) found.push(rule);
      } catch {
        // A selector jsdom cannot parse is not the rule under test.
      }
    }
  };
  for (const sheet of Array.from(document.styleSheets)) visit(sheet.cssRules);
  return found;
};

const ruleValue = (rule, property) =>
  rule.style.getPropertyValue(property).trim();

// What each matching rule sets for the property, in sheet order.
const valuesFor = (el, property) =>
  rulesMatching(el).map((rule) => ruleValue(rule, property));

const declares = (el, property, value) =>
  valuesFor(el, property).includes(value);

const underlined = (tab) =>
  valuesFor(tab, "box-shadow").some((v) => v.startsWith("inset 0 -2px 0"));

// The value a ::before rule on this element sets for the property (the
// accent bar is the turn row's ::before).
const beforeValue = (el, property) => {
  let value = "";
  const visit = (rules) => {
    for (const rule of Array.from(rules ?? [])) {
      if (rule.cssRules) visit(rule.cssRules);
      if (!rule.selectorText || !rule.style) continue;
      for (const part of rule.selectorText.split(",")) {
        const selector = part.trim();
        if (!/::?before$/.test(selector)) continue;
        try {
          if (!el.matches(selector.replace(/::?before$/, ""))) continue;
        } catch {
          continue;
        }
        if (ruleValue(rule, property)) value = ruleValue(rule, property);
      }
    }
  };
  for (const sheet of Array.from(document.styleSheets)) visit(sheet.cssRules);
  return value;
};

// Colours compared in one notation: the sheet keeps the source text
// ("#A792FD") while an element's style reports rgb().
const asRgb = (color) => {
  const probe = document.createElement("i");
  probe.style.backgroundColor = color;
  return probe.style.backgroundColor;
};

const turnRow = (text) => screen.getByText(text).parentElement.parentElement;

describe("voice call drawer at narrow widths", () => {
  const originalScrollIntoView = Element.prototype.scrollIntoView;

  beforeEach(() => {
    useVoiceAudioStore.getState().reset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    useVoiceAudioStore.getState().reset();
    vi.unstubAllGlobals();
    if (originalScrollIntoView) {
      Element.prototype.scrollIntoView = originalScrollIntoView;
    } else {
      delete Element.prototype.scrollIntoView;
    }
  });

  it("wraps the transcript filter pills below the search box instead of clipping them", () => {
    render(<TranscriptView transcript={walkTranscript} />);

    const pills = PILLS.map((name) => screen.getByRole("button", { name }));
    const pillGroup = pills[0].parentElement;
    const toolbar = pillGroup.parentElement;
    const search = screen.getByPlaceholderText("Search transcript");

    expect(toolbar).toContainElement(search);
    expect(declares(toolbar, "flex-wrap", "wrap")).toBe(true);
    expect(declares(pillGroup, "flex-shrink", "0")).toBe(true);
    for (const pill of pills) {
      expect(pillGroup).toContainElement(pill);
      expect(declares(pill, "white-space", "nowrap")).toBe(true);
    }
  });

  it.each([
    ["test theme", undefined],
    ["app theme", appTheme],
  ])(
    "wraps the right-pane tabs onto extra rows instead of hiding them behind scroll arrows (%s)",
    (_themeName, theme) => {
      const { container } = render(
        <CompactTabs
          value="analytics"
          onChange={vi.fn()}
          tabs={rightPaneTabs}
        />,
        { theme },
      );

      for (const { label } of rightPaneTabs) {
        expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
      }
      const scroller = container.querySelector(".MuiTabs-scroller");
      const tabRow = container.querySelector(".MuiTabs-flexContainer");
      // CompactTabs sets the variant itself, so the strip is the same under
      // the test theme (MUI's default, standard) and the app theme
      // (default-props.jsx, scrollable).
      expect(scroller).toHaveClass("MuiTabs-scrollableX");
      expect(scroller).not.toHaveClass("MuiTabs-fixed");
      expect(declares(tabRow, "flex-wrap", "wrap")).toBe(true);
      for (const tab of screen.getAllByRole("tab")) {
        expect(declares(tab, "white-space", "nowrap")).toBe(true);
      }
    },
  );

  it("keeps the compact scroll arrows for a pane too narrow for one tab (app theme)", async () => {
    vi.stubGlobal("IntersectionObserver", LastTabCutOffObserver);
    const { container } = render(
      <CompactTabs value="analytics" onChange={vi.fn()} tabs={rightPaneTabs} />,
      { theme: appTheme },
    );

    const arrows = () => [
      ...container.querySelectorAll(".MuiTabs-scrollButtons"),
    ];
    await waitFor(() => expect(arrows()).toHaveLength(2));
    const [start, end] = arrows();
    expect(start).toHaveClass("Mui-disabled");
    expect(end).not.toHaveClass("Mui-disabled");
    // 24 px wide, not the theme's 48 px round button, and a disabled arrow
    // stays dimmed instead of vanishing.
    for (const arrow of [start, end]) {
      expect(declares(arrow, "width", "24px")).toBe(true);
    }
    expect(declares(start, "opacity", "0.3")).toBe(true);
  });

  it("underlines the selected tab itself, since the sliding indicator assumes one row", () => {
    const { container } = render(
      <CompactTabs value="analytics" onChange={vi.fn()} tabs={rightPaneTabs} />,
    );

    const indicator = container.querySelector(".MuiTabs-indicator");
    const selected = screen.getByRole("tab", { name: "Call Analytics" });
    const other = screen.getByRole("tab", { name: "Evals" });

    expect(declares(indicator, "display", "none")).toBe(true);
    expect(selected).toHaveAttribute("aria-selected", "true");
    expect(underlined(selected)).toBe(true);
    expect(underlined(other)).toBe(false);
  });

  it("floats a turn's hover actions instead of leaving an empty band above a turn with no time", () => {
    render(<TranscriptView transcript={walkTranscript} />);

    const text = screen.getByText("I want to check my order status.");
    const content = text.parentElement;
    const row = content.parentElement;
    const copy = within(row).getByRole("button", { name: "Copy turn" });
    const actions = copy.closest(".turn-actions");

    // Only the floating actions and the text: no row of its own above it.
    expect(row.children).toHaveLength(2);
    expect(row.children[0]).toBe(actions);
    expect(row.children[1]).toBe(content);
    expect(declares(actions, "position", "absolute")).toBe(true);
  });

  it("keeps the time, duration and actions in one row above a timed turn", () => {
    render(
      <TranscriptView
        transcript={timedTranscript}
        callStartedAt="2026-11-09T12:00:00.000Z"
      />,
    );

    const metaRow = screen.getByText("0:05").parentElement;
    const copy = within(metaRow).getByRole("button", { name: "Copy turn" });
    const actions = copy.closest(".turn-actions");

    expect(metaRow).toHaveTextContent("2.0s");
    expect(metaRow.parentElement).toBe(turnRow("Hello there"));
    expect(declares(actions, "position", "absolute")).toBe(false);
  });

  it("draws the assistant turns' accent bar in a solid colour in dark theme", () => {
    const dark = createTheme({ palette: palette("dark") });
    render(<TranscriptView transcript={walkTranscript} />, { theme: dark });

    const row = turnRow("Your order A-1001 has shipped.");
    const bar = beforeValue(row, "background-color");

    expect(asRgb(bar)).not.toBe("");
    expect(asRgb(bar)).toBe(asRgb(dark.palette.data[0]));
    expect(asRgb(bar)).not.toBe(asRgb(dark.palette.primary.light));
  });
});
