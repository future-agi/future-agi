import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import ErrorFeedAlerts from "../ErrorFeedAlerts";

// Keep the real drawer, form, and storage lifecycle; only replace API hooks.
vi.mock("src/api/errorFeed/alerts", () => ({
  useErrorFeedAlerts: () => ({ data: [], isLoading: false, isError: false }),
  useErrorFeedAlertOptions: () => ({
    data: { projects: [{ value: "project-1", label: "Checkout bot" }] },
    isLoading: false,
    isError: false,
  }),
  useSaveErrorFeedAlert: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteErrorFeedAlert: () => ({ mutate: vi.fn() }),
  useTestErrorFeedAlert: () => ({ mutate: vi.fn() }),
}));
vi.mock("src/api/integrations", () => ({
  useIntegrationConnections: () => ({
    data: [{ id: "slack-1", platform: "slack", name: "Test workspace" }],
    isLoading: false,
    isError: false,
  }),
  useSlackChannels: () => ({ data: { channels: [] } }),
}));

const DRAFT_KEY = "error-feed-slack-alert-draft-v1";
const ALERTS_PATH = "/dashboard/error-feed/alerts";
const draft = {
  name: "Critical checkout issues",
  projectId: "project-1",
  trigger: "occurrences_crossed",
  triggerValue: 5,
  sources: ["scanner"],
  severities: ["high", "critical"],
  statuses: ["for_review"],
  issueCategories: ["Timeout"],
  issueGroups: ["Payment"],
  cooldown: 900,
};

const renderPage = () =>
  render(
    <StrictMode>
      <ErrorFeedAlerts />
    </StrictMode>,
  );

const expectRestoredDraft = () => {
  expect(screen.getByText("Create Error Feed alert")).toBeVisible();
  expect(screen.getByRole("textbox", { name: "Rule name" })).toHaveValue(
    draft.name,
  );
  expect(screen.getByRole("combobox", { name: "Project" })).toHaveTextContent(
    "Checkout bot",
  );
  expect(screen.getByRole("combobox", { name: "Issue event" })).toHaveTextContent(
    "Occurrence count crosses a threshold",
  );
  expect(
    screen.getByRole("spinbutton", { name: "Occurrence threshold" }),
  ).toHaveValue(5);
  expect(screen.getByRole("combobox", { name: "Sources" })).toHaveTextContent(
    "Scanner",
  );
  expect(
    screen.getByRole("combobox", { name: "Severity filter" }),
  ).toHaveTextContent("High, Critical");
  expect(screen.getByRole("combobox", { name: "Status" })).toHaveTextContent(
    "For Review",
  );
  expect(screen.getByText("Timeout")).toBeVisible();
  expect(screen.getByText("Payment")).toBeVisible();
  expect(screen.getByRole("combobox", { name: "Notify at most" })).toHaveTextContent(
    "Once per issue every 15 minutes",
  );
};

const expectBlankForm = () => {
  fireEvent.click(screen.getAllByRole("button", { name: "Create alert" })[0]);
  expect(screen.getByRole("textbox", { name: "Rule name" })).toHaveValue("");
  expect(screen.getByRole("combobox", { name: "Project" })).toHaveTextContent(
    "All projects in this workspace",
  );
  expect(screen.getByRole("combobox", { name: "Sources" })).toHaveTextContent(
    "Any source",
  );
  expect(
    screen.getByRole("combobox", { name: "Severity filter" }),
  ).toHaveTextContent("Any severity");
  expect(screen.getByRole("combobox", { name: "Status" })).toHaveTextContent(
    "Any status",
  );
  expect(screen.queryByText("Timeout")).not.toBeInTheDocument();
  expect(screen.queryByText("Payment")).not.toBeInTheDocument();
};

describe("Error Feed alert draft after Slack connection (unit)", () => {
  beforeEach(() => {
    sessionStorage.clear();
    window.history.replaceState({}, "", `${ALERTS_PATH}?resume_alert=1`);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    sessionStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  it("restores the open drawer and all draft fields when StrictMode restarts rendering", () => {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));

    renderPage();

    expectRestoredDraft();
  });

  it("keeps the drawer closed and starts a blank form on a normal visit without a draft", () => {
    window.history.replaceState({}, "", ALERTS_PATH);

    renderPage();

    expect(screen.queryByText("Create Error Feed alert")).not.toBeInTheDocument();
    expectBlankForm();
  });

  it("consumes the draft after restoration so a later visit cannot reopen it", () => {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    const { unmount } = renderPage();

    expectRestoredDraft();
    expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull();
    unmount();
    // Even revisiting the OAuth return URL must not replay a consumed draft.
    renderPage();

    expect(screen.queryByText("Create Error Feed alert")).not.toBeInTheDocument();
    expectBlankForm();
  });

  it("does not restore or consume a pending draft without the return marker", () => {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    window.history.replaceState({}, "", ALERTS_PATH);

    renderPage();

    expect(screen.queryByText("Create Error Feed alert")).not.toBeInTheDocument();
    expectBlankForm();
    const pending = sessionStorage.getItem(DRAFT_KEY);
    expect(pending).not.toBeNull();
    expect(JSON.parse(pending as string)).toEqual(draft);
  });

  it("discards a malformed draft after commit instead of opening the drawer", () => {
    sessionStorage.setItem(DRAFT_KEY, "not json");

    renderPage();

    expect(screen.queryByText("Create Error Feed alert")).not.toBeInTheDocument();
    expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull();
    expectBlankForm();
  });

  it("keeps the restored form usable if storage cleanup is unavailable", () => {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("Storage unavailable", "SecurityError");
    });

    renderPage();

    expectRestoredDraft();
    fireEvent.change(screen.getByRole("textbox", { name: "Rule name" }), {
      target: { value: "Updated rule" },
    });
    expect(screen.getByRole("textbox", { name: "Rule name" })).toHaveValue(
      "Updated rule",
    );
  });
});
