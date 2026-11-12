// At 1024 and 1280 px wide, list tables ran
// columns and row actions past the right edge with no scrollbar in sight
// (macOS shows overlay bars only while you scroll, and the app's
// `* { scrollbar-width: thin }` keeps Chromium on those overlay bars). Two
// opt-in DataTable props cover a table whose columns cannot all fit:
// `showScrollbar` draws the grid's own bars as always-visible bars, and
// `pinnedRight` keeps one column (the row actions) on the right edge while
// the table scrolls sideways. jsdom lays nothing out, so these tests pin the
// emitted rules and the measured-overflow switch; the pixel proof is a
// browser walk.
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, render, screen, waitFor } from "src/utils/test-utils";
import DataTable from "../DataTable";

const COLUMNS = [
  {
    id: "name",
    accessorKey: "name",
    header: "Name",
    meta: { flex: 1 },
    minSize: 200,
  },
  { id: "updated", accessorKey: "updated", header: "Last updated", size: 150 },
  {
    id: "actions",
    accessorKey: "id",
    header: "",
    size: 80,
    // eslint-disable-next-line react/prop-types
    cell: ({ row }) => (
      <button type="button">{`Duplicate ${row.original.name}`}</button>
    ),
  },
];
const DATA = [
  { id: "p1", name: "The Polite Senior Caller", updated: "10 minutes ago" },
];

const SCROLLER = "MuiDataGrid-virtualScroller";

// The grid's scroller reports `scrollWidth` x `clientWidth`, as a browser
// does when the columns are wider than the table (or not). Every other
// element keeps jsdom's 0.
const scrollerMeasures = (scrollWidth, clientWidth) => {
  const proto = HTMLElement.prototype;
  const own = (name, value) =>
    Object.defineProperty(proto, name, {
      configurable: true,
      get() {
        return this.classList?.contains(SCROLLER) ? value : 0;
      },
    });
  own("scrollWidth", scrollWidth);
  own("clientWidth", clientWidth);
  return () => {
    delete proto.scrollWidth;
    delete proto.clientWidth;
  };
};

let restoreMeasures = () => {};
afterEach(() => {
  cleanup();
  restoreMeasures();
  restoreMeasures = () => {};
});

const styleText = () =>
  [...document.querySelectorAll("style")].map((s) => s.textContent).join("\n");

const emotionClasses = (el) =>
  [...el.classList].filter((c) => c.startsWith("css-"));

// jsdom lays nothing out and its computed style drops properties it does not
// know, so read the element's own stylesheet rules (last declaration wins).
const declared = (element, property) => {
  let value = "";
  const visit = (rules) => {
    for (const rule of rules) {
      if (!rule.selectorText) {
        if (rule.cssRules) visit(rule.cssRules);
        continue;
      }
      let matches = false;
      try {
        matches = element.matches(rule.selectorText);
      } catch (error) {
        // Vendor pseudo-elements jsdom cannot parse never style these.
        if (error.name !== "SyntaxError") throw error;
      }
      const v = matches && rule.style.getPropertyValue(property);
      if (v) value = v.trim();
    }
  };
  for (const sheet of document.styleSheets) visit(sheet.cssRules);
  return value;
};

const actionsCell = () =>
  document.querySelector('.MuiDataGrid-cell[data-field="actions"]');

describe("DataTable on a table wider than its box", () => {
  it("keeps the row actions on the right edge while the table scrolls sideways, and only then", async () => {
    restoreMeasures = scrollerMeasures(1200, 800);
    render(
      <DataTable
        columns={COLUMNS}
        data={DATA}
        rowCount={1}
        pinnedRight="actions"
      />,
    );
    await screen.findByRole("button", {
      name: "Duplicate The Polite Senior Caller",
    });
    await waitFor(() =>
      expect(declared(actionsCell(), "position")).toBe("sticky"),
    );
    // The header cell follows the sideways scroll back by
    // scrollLeft - (scrollWidth - clientWidth): -400 px at the left edge.
    const root = actionsCell().closest(".MuiDataGrid-root").parentElement;
    expect(root.style.getPropertyValue("--fa-scroll-right")).toBe("-400px");
    expect(styleText()).toMatch(
      /\.MuiDataGrid-columnHeader\[data-field="actions"\]\{[^}]*translateX\(var\(--fa-scroll-right/,
    );

    // A table that fits never scrolls sideways: no sticky cell, no edge line.
    cleanup();
    restoreMeasures();
    restoreMeasures = scrollerMeasures(800, 800);
    render(
      <DataTable
        columns={COLUMNS}
        data={DATA}
        rowCount={1}
        pinnedRight="actions"
      />,
    );
    await screen.findByRole("button", {
      name: "Duplicate The Polite Senior Caller",
    });
    expect(declared(actionsCell(), "position")).not.toBe("sticky");
  });

  it("draws the grid's scrollbars as always-visible 8 px bars when asked, and leaves other tables alone", async () => {
    render(
      <>
        <div data-testid="with-bars">
          <DataTable columns={COLUMNS} data={DATA} rowCount={1} showScrollbar />
        </div>
        <div data-testid="default">
          <DataTable columns={COLUMNS} data={DATA} rowCount={1} />
        </div>
      </>,
    );
    const gridIn = (id) =>
      screen.getByTestId(id).querySelector(".MuiDataGrid-root");
    const css = styleText();
    const barRule = (cls) =>
      new RegExp(
        `\\.${cls} \\.MuiDataGrid-virtualScroller::-webkit-scrollbar\\{[^}]*width:8px;[^}]*height:8px`,
      );
    // The app's global `scrollbar-width: thin` would switch Chromium back
    // to overlay bars, so the scroller resets it.
    const resetRule = (cls) =>
      new RegExp(
        `\\.${cls} \\.MuiDataGrid-virtualScroller\\{[^}]*scrollbar-width:auto`,
      );
    expect(
      emotionClasses(gridIn("with-bars")).some(
        (cls) => barRule(cls).test(css) && resetRule(cls).test(css),
      ),
    ).toBe(true);
    expect(
      emotionClasses(gridIn("default")).some((cls) => barRule(cls).test(css)),
    ).toBe(false);
  });

  // Repair round: the pinned cell sticks to the right edge, so it is always
  // in view. Scrolling it "into view" past the scroller's right padding only
  // slid the table sideways on every focus or click of Duplicate, Edit or
  // the gear, and the row's name slid out of sight.
  it("scrolls a control that takes focus into view, but never for the pinned row actions, which are always in view", async () => {
    const saved = Element.prototype.scrollIntoView;
    const calls = [];
    Element.prototype.scrollIntoView = function scrollIntoView(options) {
      calls.push([this, options]);
    };
    const withLink = [
      {
        ...COLUMNS[0],
        // eslint-disable-next-line react/prop-types
        cell: ({ row }) => (
          <a href={`#${row.original.id}`}>{`Open ${row.original.name}`}</a>
        ),
      },
      ...COLUMNS.slice(1),
    ];
    try {
      render(
        <DataTable
          columns={withLink}
          data={DATA}
          rowCount={1}
          showScrollbar
          pinnedRight="actions"
        />,
      );
      const link = await screen.findByRole("link", {
        name: "Open The Polite Senior Caller",
      });
      await act(async () => {
        link.focus();
      });
      expect(calls).toContainEqual([
        link,
        { block: "nearest", inline: "nearest" },
      ]);

      calls.length = 0;
      const button = screen.getByRole("button", {
        name: "Duplicate The Polite Senior Caller",
      });
      await act(async () => {
        button.focus();
      });
      expect(document.activeElement).toBe(button);
      expect(calls.filter(([el]) => actionsCell().contains(el))).toEqual([]);
    } finally {
      Element.prototype.scrollIntoView = saved;
    }
  });
});
