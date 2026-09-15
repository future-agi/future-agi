import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { useEnvironmentsStore, resetEnvironmentsStore } from "../store/useEnvironmentsStore";
import { BUILD_HANDOFF_COPY } from "../environmentOptions";

const enqueueSnackbar = vi.fn();
vi.mock("notistack", () => ({ enqueueSnackbar: (...a) => enqueueSnackbar(...a) }));

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
    enqueueSnackbar.mockReset();
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

  it("hands the draft to the store and snackbars on submit", async () => {
    const user = userEvent.setup();
    renderPanel("source");

    await user.type(screen.getByPlaceholderText("owner/repo"), "owner/repo");
    await user.click(screen.getByRole("button", { name: /Build environment/ }));

    await waitFor(() => {
      expect(useEnvironmentsStore.getState().draft?.kind).toBe("repo");
    });
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(BUILD_HANDOFF_COPY, {
      variant: "info",
    });
  });
});
