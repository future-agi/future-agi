import { describe, it, expect, vi, beforeEach, afterAll } from "vitest";
import { configure } from "@testing-library/react";
import { screen, fireEvent, waitFor } from "src/utils/test-utils";

import useSimulationAccess from "src/sections/simulate/environments/hooks/useSimulationAccess";
import ScenariosStep from "../ScenariosStep";
import { WORKSPACE_COPY } from "../../workspace.constants";
import {
  TEST_ENV,
  SERVER_ROWS,
  envStateFor,
  renderWithClient,
} from "./scenariosTestUtils";

// Add scenarios is the Scenarios tab's one way in. Its panel offers the
// sources that work today: Observe and dataset evidence is picked where it
// lives, and its import lands back here as `?import=`; a transcript is added
// here, and its review opens at once.

configure({ asyncUtilTimeout: 5000 });
afterAll(() => configure({ asyncUtilTimeout: 1000 }));

const drawer = vi.hoisted(() => ({ props: null }));
vi.mock(
  "src/sections/simulate/environments/evidence/EvidenceReviewDrawer",
  () => ({
    default: (props) => {
      drawer.props = props;
      return (
        // Through the captured props, which the assertions read too.
        <button type="button" onClick={() => drawer.props.onClose()}>
          close review
        </button>
      );
    },
  }),
);
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn(), closeSnackbar: vi.fn() }),
}));
vi.mock("src/api/simulate-environments/scenarios", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/scenarios",
  );
  return {
    ...actual,
    listScenarios: vi.fn(),
    amendScenarios: vi.fn(),
    scenarioCoverage: vi.fn(async () => ({
      axes: [],
      per_axis: [],
      rows: [],
      columns: [],
      cells: [],
    })),
  };
});
const { listScenarios } = await import(
  "src/api/simulate-environments/scenarios"
);
const { queryScenarioFixture, resetScenarioFixture } = await import(
  "src/api/simulate-environments/_fixtures/scenariosFixtures"
);
const { default: axios } = await import("src/utils/axios");

const IMPORT = "22222222-2222-2222-2222-222222222222";
const SUBTITLE = /Add the ones it couldn't work out from your agent/;
// The design's sources that have no working path yet.
const HIDDEN = [
  "Generate",
  "From chat",
  "Write one",
  "From a script",
  "From an Error Feed issue",
  "From failing cases",
];

const renderAt = (search) => {
  window.history.pushState({}, "", `/environments/${TEST_ENV.id}${search}`);
  renderWithClient(<ScenariosStep env={TEST_ENV} envState={envStateFor()} />);
};

const openPanel = async () => {
  fireEvent.click(await screen.findByRole("button", { name: "Add scenarios" }));
  expect(await screen.findByText(SUBTITLE)).toBeInTheDocument();
};

// A card's accessible name is its title and then its one line.
const card = (title) =>
  screen.getByRole("button", { name: new RegExp(`^${title}`) });

beforeEach(() => {
  drawer.props = null;
  resetScenarioFixture();
  listScenarios.mockReset();
  listScenarios.mockImplementation((jobId, params) =>
    queryScenarioFixture(params, SERVER_ROWS),
  );
});

describe("ScenariosStep evidence import", () => {
  it("opens the review named in the URL and closes it without losing the tab", async () => {
    renderAt(`?tab=scenarios&import=${IMPORT}`);
    await waitFor(() =>
      expect(drawer.props).toMatchObject({
        envId: TEST_ENV.id,
        importId: IMPORT,
        open: true,
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "close review" }));

    await waitFor(() => expect(window.location.search).toBe("?tab=scenarios"));
    expect(screen.queryByRole("button", { name: "close review" })).toBeNull();
  });

  it("opens no review without an import, and has no separate Import evidence button", async () => {
    renderAt("?tab=scenarios");
    expect(
      await screen.findByRole("button", { name: "Add scenarios" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Import evidence" }),
    ).toBeNull();
    expect(drawer.props).toBeNull();
  });
});

describe("ScenariosStep — Add scenarios panel", () => {
  it("offers only the sources that work today", async () => {
    renderAt("?tab=scenarios");
    await openPanel();

    expect(screen.getByText("From what already happened")).toBeInTheDocument();
    expect(card("From production")).toHaveTextContent(
      "Traces or sessions from Observe",
    );
    expect(card("From a dataset")).toHaveTextContent(
      "Each row becomes a draft",
    );
    expect(card("Upload transcript or file")).toHaveTextContent(
      "Transcripts, CSV or JSON",
    );
    expect(screen.queryByText("Write new")).toBeNull();
    HIDDEN.forEach((title) =>
      expect(screen.queryByText(title, { exact: true })).toBeNull(),
    );
  });

  it.each([
    ["From production", "/dashboard/observe"],
    ["From a dataset", "/dashboard/develop"],
  ])("%s goes to where that evidence is picked", async (title, path) => {
    renderAt("?tab=scenarios");
    await openPanel();

    fireEvent.click(card(title));

    await waitFor(() => expect(window.location.pathname).toBe(path));
  });

  it("closes on Cancel", async () => {
    renderAt("?tab=scenarios");
    await openPanel();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByText(SUBTITLE)).toBeNull());
  });

  it("uploads a transcript in place of the panel and opens its review", async () => {
    const post = vi
      .spyOn(axios, "post")
      .mockResolvedValue({ data: { id: IMPORT, cases: [] } });
    renderAt("?tab=scenarios");
    await openPanel();

    fireEvent.click(card("Upload transcript or file"));

    // One at a time: the panel closes as the transcript dialog opens.
    const transcript = await screen.findByLabelText("Transcript");
    await waitFor(() => expect(screen.queryByText(SUBTITLE)).toBeNull());
    fireEvent.change(transcript, {
      target: { value: "User: Refund my order\nAgent: Done." },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Create environment test" }),
    );

    await waitFor(() =>
      expect(drawer.props).toMatchObject({
        envId: TEST_ENV.id,
        importId: IMPORT,
        open: true,
      }),
    );
    expect(post).toHaveBeenCalledWith(
      `/simulate/api/harness-environments/${TEST_ENV.id}/evidence-imports/`,
      expect.objectContaining({ source_kind: "transcript" }),
    );
    expect(window.location.search).toBe(`?tab=scenarios&import=${IMPORT}`);
    expect(screen.queryByLabelText("Transcript")).toBeNull();
    post.mockRestore();
  });

  it("shows a Viewer Add scenarios locked, with the reason", async () => {
    vi.mocked(useSimulationAccess).mockReturnValue({
      canEdit: false,
      canRun: false,
    });
    renderAt("?tab=scenarios");

    const button = await screen.findByRole("button", {
      name: "Add scenarios",
    });
    expect(button).toBeDisabled();
    fireEvent.mouseOver(button.closest("span"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      WORKSPACE_COPY.viewer.scenarios,
    );
    vi.mocked(useSimulationAccess).mockReturnValue({
      canEdit: true,
      canRun: true,
    });
  });
});
