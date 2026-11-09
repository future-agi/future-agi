import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import StoredAgreement from "../StoredAgreement";

const state = vi.hoisted(() => ({
  result: {},
  options: null,
  refetch: vi.fn(),
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: (options) => {
    state.options = options;
    return { ...state.result, refetch: state.refetch };
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "ws-a" }),
}));

beforeEach(() => {
  state.refetch.mockClear();
  state.result = {
    data: {
      sources: [
        {
          key: "binding:label",
          dataset_name: "Saved data",
          binding_name: "Correctness",
          label_name: "Human review",
        },
      ],
      report: {
        key: "binding:label",
        n: 3,
        numeric: false,
        accuracy: 2 / 3,
        kappa: 0.4,
        excluded: 1,
        per_label: [
          { label: "Pass", precision: 0.5, recall: 1, f1: 2 / 3, support: 1 },
        ],
        disagreements: [{ row_id: "row-one", human: "Fail", judge: "Pass" }],
      },
    },
  };
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: ["evals.agreement"],
  });
});
afterEach(() => setLaunchOverrideForTest(null));
const mount = () =>
  render(
    <MemoryRouter>
      <StoredAgreement templateId="template-a" />
    </MemoryRouter>,
  );

it("reports stored statistics without exposing unbuilt judge actions", () => {
  mount();
  expect(screen.getByTestId("w7-headline").textContent).toBe(
    "Agreement 67% · κ 0.40 · n 3",
  );
  expect(
    screen.getByRole("table", { name: "Agreement per label" }).textContent,
  ).toBe("LabelPrecisionRecallF1Reviewer rowsPass50%100%0.671");
  expect(
    screen.getByRole("table", { name: "Stored disagreements" }).textContent,
  ).toBe("RowHuman labelStored judge resultrow-oneFailPass");
  expect(
    screen.queryByRole("button", { name: /Improve|Train|Compare|Export/ }),
  ).toBeNull();
  expect(state.options.queryKey.slice(0, 4)).toEqual([
    "stored-agreement",
    "org-a",
    "ws-a",
    "template-a",
  ]);
  expect(state.options.enabled).toBe(true);
});
it("hides the report and disables reads when unclaimed", () => {
  setLaunchOverrideForTest({ launch: true, madeReal: [] });
  mount();
  expect(screen.queryByTestId("w7-agreement-tab")).toBeNull();
  expect(state.options.enabled).toBe(false);
});
it("does not display stale statistics after a failed read", () => {
  state.result.isError = true;
  mount();
  expect(screen.queryByTestId("w7-headline")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(state.refetch).toHaveBeenCalledOnce();
});
it("shows an honest empty state and refreshes on demand", () => {
  state.result = { data: { sources: [], report: null } };
  mount();
  expect(
    screen.getByText("No saved dataset results have human labels yet."),
  ).toBeTruthy();
  expect(screen.queryByTestId("w7-headline")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(state.refetch).toHaveBeenCalledOnce();
});
it("keeps source selection usable when the first report is too large", () => {
  state.result.data.report = null;
  mount();
  expect(screen.getByRole("alert").textContent).toBe(
    "Choose a label source to view its report. The first source is too large to calculate automatically.",
  );
  fireEvent.mouseDown(
    screen.getByRole("combobox", { name: "Human label source" }),
  );
  fireEvent.click(
    screen.getByRole("option", {
      name: "Saved data · Correctness · Human review",
    }),
  );
  expect(state.options.queryKey[4]).toEqual({ source: "binding:label" });
  expect(screen.queryByTestId("w7-headline")).toBeNull();
});
it("does not render a missing numeric correlation as zero or perfect", () => {
  state.result.data.report = {
    ...state.result.data.report,
    numeric: true,
    spearman: null,
    mad: 0,
    n: 1,
  };
  mount();
  expect(screen.getByTestId("w7-headline").textContent).toBe(
    "Spearman ρ – · mean absolute difference 0.00 · n 1",
  );
});
it("respects a linked source and can clear a deleted selection", () => {
  state.result.isError = true;
  render(
    <MemoryRouter
      initialEntries={[
        "/?tab=agreement&source=deleted&dataset=dataset-a&label=label-a",
      ]}
    >
      <StoredAgreement templateId="template-a" />
    </MemoryRouter>,
  );
  expect(state.options.queryKey[4]).toEqual({
    source: "deleted",
    dataset_id: "dataset-a",
    label_id: "label-a",
  });
  fireEvent.click(screen.getByRole("button", { name: "Choose a source" }));
  expect(state.options.queryKey[4]).toEqual({});
});
