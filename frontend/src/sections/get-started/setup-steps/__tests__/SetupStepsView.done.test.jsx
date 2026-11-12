/* eslint-disable react/prop-types */
// The open step is highlighted in the list, and that highlight hides the
// check mark a finished step shows there. A user who comes back to a step
// they finished elsewhere (a dataset made in Datasets) must still see that
// it counted.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderWithRouter, screen } from "src/utils/test-utils";
import SetupStepsView from "../SetupStepsView";

const http = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { id: "person-a", organization_role: "Owner" },
  }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  handleOnDocsClicked: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/external-content/ExternalContentFrame", () => ({
  default: () => null,
}));

const DONE_NOTE = "This step is done.";

beforeEach(() => {
  http.get.mockReset();
  http.get.mockResolvedValue({ data: { status: true, result: {} } });
});

const renderSteps = async (currentLabel, data) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = renderWithRouter(
    <QueryClientProvider client={client}>
      <SetupStepsView
        data={data}
        currentLabel={currentLabel}
        setCurrentLabel={vi.fn()}
        setShowDemoModal={vi.fn()}
      />
    </QueryClientProvider>,
    { route: `/dashboard/get-started?tab=${currentLabel}` },
  );
  await act(async () => {});
  return view;
};

describe("Get started: the open step says when it is done", () => {
  it("an open step that is done says so", async () => {
    await renderSteps("createFirstDataset", { keys: true, dataset: true });

    expect(
      screen.getByText(
        "Create a dataset to hold the examples you test your prompts and models against.",
      ),
    ).toBeVisible();
    expect(screen.getByText(DONE_NOTE)).toBeVisible();
  });

  it("guard: an open step that is not done says nothing about it", async () => {
    await renderSteps("createFirstEvaluation", {
      keys: true,
      dataset: true,
      evaluation: false,
    });

    expect(
      screen.getByText(
        "Create an evaluation to score your model outputs automatically instead of checking each one by hand.",
      ),
    ).toBeVisible();
    expect(screen.queryByText(DONE_NOTE)).not.toBeInTheDocument();
  });

  it("guard: the 'You are all set up' view has no step note", async () => {
    await renderSteps("completed", {
      keys: true,
      dataset: true,
      evaluation: true,
      experiment: true,
      observe: true,
      invite: true,
    });

    expect(screen.getByText("Setup is complete")).toBeVisible();
    expect(screen.queryByText(DONE_NOTE)).not.toBeInTheDocument();
  });
});
