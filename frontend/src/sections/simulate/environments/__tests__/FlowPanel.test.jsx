import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

// The source panel runs the real preflight inline before Build enables.
vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  uploadHarnessSource: vi.fn(),
}));

const { preflightHarnessJob } = await import("src/api/harness/harness");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../store/useEnvironmentsStore"
);
const { default: FlowPanel } = await import("../panels/FlowPanel");

const renderPanel = (choice) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FlowPanel choice={choice} />
    </QueryClientProvider>,
  );
};

describe("FlowPanel", () => {
  beforeEach(() => {
    resetEnvironmentsStore();
    navigate.mockReset();
  });

  afterEach(() => {
    resetEnvironmentsStore();
  });

  it("renders the source panel with its title and subtitle", () => {
    renderPanel("source");
    expect(screen.getByText("Source repository")).toBeInTheDocument();
    // The subtitle string is also the Repository field helper, so it appears twice.
    expect(
      screen.getAllByText(
        "We read the code so scenarios stay in sync with your actual tools.",
      ),
    ).toHaveLength(2);
  });

  it("renders the hosted panel with its title and subtitle", () => {
    renderPanel("hosted");
    expect(screen.getByText("Hosted platform")).toBeInTheDocument();
    expect(
      screen.getByText("Point at an agent living on a managed platform."),
    ).toBeInTheDocument();
  });

  it("renders the upload panel with its title and subtitle", () => {
    renderPanel("upload");
    expect(screen.getByText("Code upload")).toBeInTheDocument();
    expect(
      screen.getByText("Upload your agent code and we'll analyze it in place."),
    ).toBeInTheDocument();
  });

  it("renders nothing for a choice without a panel", () => {
    const { container } = renderPanel("mcp");
    expect(container.firstChild).toBeNull();
  });

  it("runs preflight then stages the draft and navigates on Build", async () => {
    preflightHarnessJob.mockResolvedValue({
      ready_to_submit: true,
      state: "connected",
      checks: [],
    });
    const user = userEvent.setup();
    renderPanel("source");

    await user.type(screen.getByPlaceholderText("owner/repo"), "owner/repo");
    await user.click(screen.getByRole("button", { name: "Run preflight" }));

    const build = await screen.findByRole("button", { name: /Build environment/ });
    await waitFor(() => expect(build).toBeEnabled());
    await user.click(build);

    await waitFor(() => {
      expect(useEnvironmentsStore.getState().pendingBuild?.draft?.kind).toBe("repo");
    });
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/build",
    );
  });
});
