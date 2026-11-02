import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import ErrorFeedRoute from "../ErrorFeedRoute";

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (orig) => ({
  ...(await orig()),
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
}));

const listState = { current: null };
vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedList: () => listState.current,
}));

// Two issues as GET /tracer/feed/issues/ sends them, after the hook's select
// has unwrapped `result`.
const ISSUES = [
  {
    cluster_id: "TH-1201",
    source: "scanner",
    error: {
      name: "Refund issued without identity check",
      type: "ToolMisuseError",
    },
    status: "escalating",
    severity: "critical",
    occurrences: 42,
    trace_count: 17,
    users_affected: 9,
    last_seen: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    project: "Support voice",
    project_id: "p-1",
  },
  {
    cluster_id: "TH-1188",
    source: "eval",
    error: {
      name: "Quoted stock from the wrong warehouse",
      type: "Hallucination",
    },
    status: "for_review",
    severity: "medium",
    occurrences: 5,
    trace_count: 4,
    users_affected: 3,
    last_seen: null,
    project: "Orders chat",
    project_id: "p-2",
  },
];

const listOf = (rows) => ({
  data: { data: rows, total: rows.length, limit: 100, offset: 0 },
  isLoading: false,
  isError: false,
});

const SAMPLE_ROUTE = "/x?uiSample=scenarioSources";
// Copy only the sample proposals carry, never a real issue.
const SAMPLE_TEXT = "The Impatient Caller";

const renderRoute = (route = "/x", onClose = vi.fn()) => {
  renderWithRouter(
    <ErrorFeedRoute env={{ id: "env-1" }} envState={{}} onClose={onClose} />,
    { route },
  );
  return onClose;
};

describe("ErrorFeedRoute", () => {
  beforeEach(() => {
    enqueueSnackbar.mockClear();
    listState.current = listOf(ISSUES);
  });

  it("lists the real Error Feed issues with their fields", () => {
    renderRoute();
    expect(
      screen.getByText("Refund issued without identity check"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Quoted stock from the wrong warehouse"),
    ).toBeInTheDocument();
    expect(screen.getByText("TH-1201")).toBeInTheDocument();
    expect(screen.getByText("ToolMisuseError")).toBeInTheDocument();
    expect(screen.getByText("Support voice")).toBeInTheDocument();
    expect(screen.getByText("17")).toBeInTheDocument();
    expect(screen.getByText("Escalating")).toBeInTheDocument();
    expect(screen.getByText("2 hours ago")).toBeInTheDocument();
    expect(screen.getByText("2 issues")).toBeInTheDocument();
  });

  it("shows what the API lacks as soon chips", () => {
    renderRoute();
    expect(screen.getByText("Evidence soon")).toBeInTheDocument();
    expect(screen.getByText("Scenarios soon")).toBeInTheDocument();
    expect(screen.getByText("Agent scope soon")).toBeInTheDocument();
  });

  it("picking a real issue shows proposals as soon, with no sample proposals", () => {
    renderRoute();
    fireEvent.click(screen.getByText("Refund issued without identity check"));
    expect(screen.getByText("Proposals soon")).toBeInTheDocument();
    expect(screen.getByText(/17 failing traces/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Open TH-1201 in the Error Feed/ }),
    ).toHaveAttribute("href", "/dashboard/error-feed/TH-1201");
    expect(document.body.textContent).not.toContain(SAMPLE_TEXT);
    expect(
      screen.getByRole("button", { name: /Approve scenarios/ }),
    ).toBeDisabled();
  });

  it("carries a UI only flag next to the action", () => {
    renderRoute();
    expect(screen.getAllByLabelText("UI only").length).toBeGreaterThan(0);
  });

  it("shows the sample proposals under ?uiSample=scenarioSources", () => {
    renderRoute(SAMPLE_ROUTE);
    fireEvent.click(screen.getByText("Refund issued without identity check"));
    expect(screen.queryByText("Proposals soon")).not.toBeInTheDocument();
    expect(document.body.textContent).toContain(SAMPLE_TEXT);
    expect(screen.getByText("Becomes 3 scenarios")).toBeInTheDocument();
    expect(screen.getAllByLabelText("UI only").length).toBeGreaterThan(1);
  });

  it("approving sends nothing: says so and closes", () => {
    const onClose = renderRoute(SAMPLE_ROUTE);
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Pick Refund issued without identity check",
      }),
    );
    const approve = screen.getByRole("button", { name: "Approve 2 scenarios" });
    fireEvent.click(approve);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "UI only. Nothing was saved.",
      { variant: "info" },
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("picks single proposals inside an issue", () => {
    renderRoute(SAMPLE_ROUTE);
    fireEvent.click(screen.getByText("Refund issued without identity check"));
    fireEvent.click(
      screen.getAllByRole("checkbox", { name: /Pick proposal/ })[0],
    );
    expect(
      screen.getByRole("button", { name: "Approve 1 scenario" }),
    ).toBeEnabled();
  });

  it("filters issues by search", () => {
    renderRoute();
    fireEvent.change(screen.getByPlaceholderText(/Search issues/), {
      target: { value: "warehouse" },
    });
    expect(
      screen.queryByText("Refund issued without identity check"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("1 of 2 issues")).toBeInTheDocument();
  });

  it("shows a plain empty state when there are no issues", () => {
    listState.current = listOf([]);
    renderRoute(SAMPLE_ROUTE);
    expect(screen.getByText("No Error Feed issues yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain(SAMPLE_TEXT);
  });

  it("shows a loading state while issues load", () => {
    listState.current = { data: undefined, isLoading: true, isError: false };
    renderRoute();
    expect(screen.getByText(/Loading Error Feed issues/)).toBeInTheDocument();
  });

  it("never shows an em dash", () => {
    renderRoute(SAMPLE_ROUTE);
    fireEvent.click(screen.getByText("Refund issued without identity check"));
    fireEvent.click(screen.getByText("Quoted stock from the wrong warehouse"));
    expect(document.body.textContent).not.toContain("—");
  });

  it("never shows an em dash in real mode", () => {
    renderRoute();
    fireEvent.click(screen.getByText("Refund issued without identity check"));
    expect(document.body.textContent).not.toContain("—");
  });
});
