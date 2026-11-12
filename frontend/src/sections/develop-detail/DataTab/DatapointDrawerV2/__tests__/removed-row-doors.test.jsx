// The Row view has no stand-in output label, no Task tab and no Replay
// doors. Every gate is open here (launch off), so an absent door cannot
// come from a hidden surface.
import React from "react";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const { Null } = vi.hoisted(() => ({ Null: () => null }));

vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("src/sections/common/ValueViewer/FieldCard", () => ({
  default: ({ actions, children }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("src/sections/common/ValueViewer/ValueViewer", () => ({
  default: ({ value }) => <span>{String(value ?? "")}</span>,
}));
vi.mock("src/sections/common/ValueViewer/TextDiff", () => ({
  default: Null,
  TextDiffLegend: Null,
}));
vi.mock("src/sections/common/ux/oneview/OutlineToggle", () => ({
  default: Null,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: Null,
}));
vi.mock("../EvalChip", () => ({ default: Null }));
vi.mock("../EvalDetail", () => ({ default: Null }));

import OutputCard from "../OutputCard";
import ConversationThread from "../ConversationThread";
import { ROW_TABS } from "../useRowViewUrl";

const theme = createTheme({ palette: palette("light") });
const wrap = (node) => <ThemeProvider theme={theme}>{node}</ThemeProvider>;
const DRAWER = "src/sections/develop-detail/DataTab/DatapointDrawerV2/";

beforeEach(() => setLaunchOverrideForTest({ launch: false }));
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("Row view output card", () => {
  it("names the API call without a Simulated stand-in label", () => {
    render(
      wrap(
        <OutputCard
          output={{
            column: {
              id: "c-1",
              name: "Weather",
              origin_type: "api_call",
              data_type: "text",
            },
            cell: {
              status: "completed",
              cell_value: "sunny",
              value_infos: { simulated_host: "api.example.com" },
              meta: { simulated: true, host: "api.example.com" },
            },
            evals: [],
          }}
          row={{}}
          mode="formatted"
          diffOn={false}
          expectedOptions={[]}
          onCompareWith={() => {}}
          onToggleEval={() => {}}
          onRerun={() => {}}
        />,
      ),
    );
    expect(document.body.textContent).toMatch(/API Call/);
    expect(screen.getByText("sunny")).toBeInTheDocument();
    expect(screen.queryByTestId("api-row-simulated")).toBeNull();
    expect(document.body.textContent).not.toMatch(/Simulated/);
  });
});

describe("Row view conversation", () => {
  const conv = {
    turns: [
      { n: 1, role: "customer", content: "Where is my order?" },
      { n: 2, role: "agent", content: "It shipped." },
      { n: 3, role: "customer", content: "It did not arrive." },
      { n: 4, role: "agent", content: "Sorry about that." },
    ].map((t) => ({ attachments: [], toolCalls: [], ...t })),
  };

  it("marks the failing turn without a replay marker, even with a move handler", () => {
    render(
      wrap(
        <ConversationThread
          conv={conv}
          replayTurn={4}
          onReplayTurn={vi.fn()}
        />,
      ),
    );
    expect(screen.getByText("It shipped.")).toBeInTheDocument();
    expect(screen.queryByTestId("replay-marker")).toBeNull();
    expect(document.body.textContent).not.toMatch(/Replay starts here/);
    expect(screen.getByTestId("failing-turn-label")).toHaveTextContent(
      "First failing turn",
    );
  });
});

describe("Row view deep links and modules", () => {
  it("no longer accept a Task tab", () => {
    expect(ROW_TABS).not.toContain("task");
    expect(ROW_TABS).toEqual(
      expect.arrayContaining(["row", "raw", "conversation", "trajectory"]),
    );
  });

  it("ship no Task tab or Replay section module", () => {
    for (const file of ["TaskTab.jsx", "ReplaySection.jsx"])
      expect(existsSync(resolve(process.cwd(), DRAWER + file)), file).toBe(
        false,
      );
    expect(existsSync(resolve(process.cwd(), DRAWER + "RowView.jsx"))).toBe(
      true,
    );
  });
});
