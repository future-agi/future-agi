import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import NewExperimentButton from "../NewExperimentButton";

const testTheme = createTheme({ palette: palette("light") });
const renderButton = (props = {}) =>
  render(
    <ThemeProvider theme={testTheme}>
      <NewExperimentButton {...props} />
    </ThemeProvider>,
  );

const flags = vi.hoisted(() => ({ launch: true, kinds: true, running: false }));
const open = vi.hoisted(() => vi.fn());
vi.mock("src/launch/launchSwitch", () => ({
  isLaunchBuild: () => flags.launch,
  isSurfaceShown: (id) =>
    !flags.launch ||
    (id === "exp.new.kindMenu"
      ? flags.kinds
      : id === "exp.new.runningSources"
        ? flags.running
        : true),
}));
vi.mock("react-router-dom", () => ({ useParams: () => ({ dataset: "ds" }) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
// Inspect the exact title passed to the real tooltip, without jsdom hover timing.
vi.mock("src/components/tooltip", () => ({
  default: ({ children, title, show }) => (
    <div>
      {show ? <div data-testid="tooltip-copy">{title}</div> : null}
      {children}
    </div>
  ),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "owner" }),
}));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { UPDATE: "update" },
  RolePermission: { DATASETS: { update: { owner: true } } },
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({ can: { run: true }, reason: () => null }),
}));
vi.mock("../../states", () => ({
  useRunExperimentStoreShallow: (select) =>
    select({ initiateCreateMode: open }),
}));
vi.mock("../../DataTab/Versions/PendingChangesGate", () => ({
  usePendingChangesGate: () => (_action, callback) => callback(),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "rows", dataset: { kind: "rows" } }),
}));
vi.mock("../../ExperimentTab/useOpenSource", () => ({ default: () => open }));

beforeEach(() => {
  Object.assign(flags, { launch: true, kinds: true, running: false });
  open.mockClear();
});
afterEach(cleanup);

const titles = () =>
  screen.getAllByTestId("tooltip-copy").map((el) => el.textContent);

describe("New experiment never advertises hidden running sources", () => {
  it.each(["header", "experiments"])(
    "names only prompts and agent graphs in the %s arrow",
    (placement) => {
      renderButton({ placement });
      expect(titles()).toContain(
        "Pick what to run: a prompt or an agent graph",
      );
      expect(titles().join(" ")).not.toMatch(
        /endpoint|A2A|MCP|already running/i,
      );
      fireEvent.click(screen.getByTestId("new-experiment-kinds"));
      expect(
        screen.getByTestId("new-experiment-kind-prompt"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("new-experiment-kind-agent"),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("new-experiment-kind-running")).toBeNull();
      expect(screen.getAllByRole("menuitem")).toHaveLength(2);
    },
  );

  it("names the qualified agent path in the header hover", () => {
    renderButton();
    expect(titles()).toContain(
      "Run prompts and agent graphs on this dataset and compare them. Read more",
    );
  });

  it("keeps the shipped prompt-only header unchanged and has no arrow", () => {
    flags.kinds = false;
    renderButton();
    expect(titles()).toEqual([
      "Run prompts on this dataset and compare them. Read more",
    ]);
    expect(screen.queryByTestId("new-experiment-kinds")).toBeNull();
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "New experiment" }));
    expect(open).toHaveBeenCalledWith({
      prefill: undefined,
      firstRunType: undefined,
    });
  });

  it.each(["non-launch", "all-shown"])("preserves %s copy", (mode) => {
    flags.launch = mode !== "non-launch";
    flags.running = mode === "all-shown";
    renderButton();
    expect(titles()).toContain(
      "Run prompts, agent graphs, endpoints, A2A agents or MCP tools on this dataset and compare them. Read more",
    );
    expect(titles()).toContain(
      "Pick what to run: a prompt, an agent graph, or something already running (an endpoint, an A2A agent or an MCP tool)",
    );
  });
});
