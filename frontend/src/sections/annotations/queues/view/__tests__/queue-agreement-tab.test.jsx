import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import QueueAgreementTab from "../queue-agreement-tab";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  useQueueAgreement: vi.fn(),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useQueueAgreement: (...args) => mocks.useQueueAgreement(...args),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

const mockAgreementData = {
  overall_agreement: 0.85,
  labels: {
    "label-1": {
      label_name: "Relevance",
      label_type: "categorical",
      agreement_pct: 0.8,
      cohens_kappa: 0.75,
      disagreement_count: 25,
      disagreement_items: [
        "3fa85f64-5717-4562-b3fc-2c963f66af00",
        "7bc12d34-5717-4562-b3fc-2c963f66af01",
        "9ef56a78-5717-4562-b3fc-2c963f66af02",
      ],
    },
    "label-2": {
      label_name: "Tone",
      label_type: "categorical",
      agreement_pct: 1.0,
      cohens_kappa: 1.0,
      disagreement_count: 0,
      disagreement_items: [],
    },
    "label-3": {
      label_name: "Grammar",
      label_type: "categorical",
      agreement_pct: 0.9,
      cohens_kappa: 0.85,
      disagreement_count: 2,
      disagreement_items: [
        "4fa85f64-5717-4562-b3fc-2c963f66af10",
        "8de91a23-5717-4562-b3fc-2c963f66af11",
      ],
    },
  },
  annotator_pairs: [
    {
      annotator_1_id: "user-1",
      annotator_2_id: "user-2",
      agreement_pct: 0.85,
      total_comparisons: 20,
    },
  ],
};

describe("QueueAgreementTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useQueueAgreement.mockReturnValue({
      data: mockAgreementData,
      isLoading: false,
    });
  });

  it("renders LoadingScreen when agreement data is loading", () => {
    mocks.useQueueAgreement.mockReturnValue({
      data: undefined,
      isLoading: true,
    });

    render(<QueueAgreementTab queueId="queue-1" />);

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText("Overall Agreement")).not.toBeInTheDocument();
  });

  it("renders agreement overview, label table, and makes non-zero disagreements interactive while zero stays non-interactive", async () => {
    render(<QueueAgreementTab queueId="queue-1" />);

    // Overall agreement card
    expect(screen.getByText("Overall Agreement")).toBeInTheDocument();
    expect(screen.getAllByText("85.0%").length).toBeGreaterThanOrEqual(1);

    // Per-label rows
    expect(screen.getByText("Relevance")).toBeInTheDocument();
    expect(screen.getByText("Tone")).toBeInTheDocument();

    // Non-zero disagreement count is interactive with aria attributes
    const trigger = screen.getByRole("button", { name: "25 disagreements for Relevance" });
    expect(trigger).toBeInTheDocument();
    expect(trigger).toHaveAttribute("id", "disagreement-trigger-label-1");
    expect(trigger).toHaveAttribute("aria-haspopup", "dialog");
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    // Zero disagreement count is plain text (not a button)
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "0" })).not.toBeInTheDocument();
  });

  it("opens popover on click with heading, truncated short IDs, and aria-labelledby", async () => {
    const user = userEvent.setup();
    render(<QueueAgreementTab queueId="queue-1" />);

    const trigger = screen.getByRole("button", { name: "25 disagreements for Relevance" });
    await user.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");

    // Popover heading
    expect(screen.getByText("Disagreed Items: Relevance")).toBeInTheDocument();

    const popover = screen.getByRole("dialog", { name: "Disagreed Items: Relevance" });
    expect(popover).toHaveAttribute("id", trigger.getAttribute("aria-controls"));
    expect(popover).toHaveAttribute("aria-labelledby", "disagreement-popover-title");

    // Displays truncated short IDs (8 chars) instead of full 36-char UUIDs
    expect(screen.getByText("Item #3fa85f64")).toBeInTheDocument();
    expect(screen.getByText("Item #7bc12d34")).toBeInTheDocument();
    expect(screen.getByText("Item #9ef56a78")).toBeInTheDocument();
    expect(
      screen.queryByText("Item #3fa85f64-5717-4562-b3fc-2c963f66af00"),
    ).not.toBeInTheDocument();
  });

  it("navigates to workspace with mode=review when the viewer can view submissions", async () => {
    const user = userEvent.setup();
    render(<QueueAgreementTab queueId="queue-1" canViewSubmissions />);

    const trigger = screen.getByRole("button", { name: "25 disagreements for Relevance" });
    await user.click(trigger);

    const itemLink = screen.getByText("Item #3fa85f64");
    await user.click(itemLink);

    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/annotations/queues/queue-1/annotate?itemId=3fa85f64-5717-4562-b3fc-2c963f66af00&mode=review",
    );
  });

  it("navigates to workspace with mode=annotate when the viewer cannot view submissions", async () => {
    const user = userEvent.setup();
    render(<QueueAgreementTab queueId="queue-1" canViewSubmissions={false} />);

    const trigger = screen.getByRole("button", { name: "25 disagreements for Relevance" });
    await user.click(trigger);

    const itemLink = screen.getByText("Item #3fa85f64");
    await user.click(itemLink);

    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/annotations/queues/queue-1/annotate?itemId=3fa85f64-5717-4562-b3fc-2c963f66af00&mode=annotate",
    );
  });

  it("displays '+ N more disagreements' when count exceeds returned items, and hides it when all items are shown", async () => {
    const user = userEvent.setup();
    render(<QueueAgreementTab queueId="queue-1" />);

    // Label 1 has 25 disagreements with 3 items returned -> "+ 22 more disagreements"
    const triggerLabel1 = screen.getByRole("button", { name: "25 disagreements for Relevance" });
    await user.click(triggerLabel1);

    expect(screen.getByText("+ 22 more disagreements")).toBeInTheDocument();

    // Close popover with Escape
    await user.keyboard("{Escape}");

    // Open Label 3 (2 disagreements with 2 items returned -> no overflow text)
    const triggerLabel3 = screen.getByRole("button", { name: "2 disagreements for Grammar" });
    await user.click(triggerLabel3);

    expect(screen.getByText("Disagreed Items: Grammar")).toBeInTheDocument();
    expect(screen.queryByText(/more disagreements/)).not.toBeInTheDocument();
  });

  it("derives popover items from fresh live data when agreement query updates", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<QueueAgreementTab queueId="queue-1" />);

    // Open popover for Label 1
    const trigger = screen.getByRole("button", { name: "25 disagreements for Relevance" });
    await user.click(trigger);

    expect(screen.getByText("Item #3fa85f64")).toBeInTheDocument();

    // Agreement data updates in background on window focus
    mocks.useQueueAgreement.mockReturnValue({
      data: {
        ...mockAgreementData,
        labels: {
          ...mockAgreementData.labels,
          "label-1": {
            ...mockAgreementData.labels["label-1"],
            disagreement_items: ["99999999-5717-4562-b3fc-2c963f66af99"],
          },
        },
      },
      isLoading: false,
    });

    rerender(<QueueAgreementTab queueId="queue-1" />);

    // Rerender reflects the new item ID immediately from live data
    expect(screen.getByText("Item #99999999")).toBeInTheDocument();
    expect(screen.queryByText("Item #3fa85f64")).not.toBeInTheDocument();
  });

  it.each(["zero", "removed"])("closes when the selected label is %s and focuses surviving results", async (change) => {
    const user = userEvent.setup();
    const { rerender } = render(<QueueAgreementTab queueId="queue-1" />);
    await user.click(screen.getByRole("button", { name: "25 disagreements for Relevance" }));
    const labels = { ...mockAgreementData.labels };
    if (change === "removed") delete labels["label-1"];
    else labels["label-1"] = { ...labels["label-1"], disagreement_count: 0, disagreement_items: [] };
    mocks.useQueueAgreement.mockReturnValue({ data: { ...mockAgreementData, labels }, isLoading: false });
    rerender(<QueueAgreementTab queueId="queue-1" />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Agreement results" })).toHaveFocus();
    mocks.useQueueAgreement.mockReturnValue({ data: mockAgreementData, isLoading: false });
    rerender(<QueueAgreementTab queueId="queue-1" />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not carry an open selection into another queue", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<QueueAgreementTab queueId="queue-1" />);
    await user.click(screen.getByRole("button", { name: "25 disagreements for Relevance" }));
    rerender(<QueueAgreementTab queueId="queue-2" />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens by keyboard, restores focus on Escape and opens the exact item on Enter", async () => {
    const user = userEvent.setup();
    render(<QueueAgreementTab queueId="queue-1" canViewSubmissions />);
    const trigger = screen.getByRole("button", { name: "25 disagreements for Relevance" });
    trigger.focus();
    await user.keyboard("{Enter}");
    await user.tab();
    expect(screen.getByRole("button", { name: "Item #3fa85f64" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
    await user.keyboard("{Enter}");
    await user.tab();
    await user.keyboard("{Enter}");
    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/annotations/queues/queue-1/annotate?itemId=3fa85f64-5717-4562-b3fc-2c963f66af00&mode=review",
    );
  });

  it("keeps the full long label in the cell and popup accessible name and title", async () => {
    const labelName = "LongUnbrokenDisagreementLabel".repeat(6);
    mocks.useQueueAgreement.mockReturnValue({ data: { ...mockAgreementData, labels: {
      "label-1": { ...mockAgreementData.labels["label-1"], label_name: labelName },
    } }, isLoading: false });
    const user = userEvent.setup();
    render(<QueueAgreementTab queueId="queue-1" />);
    expect(screen.getByRole("cell", { name: labelName })).toHaveAttribute("title", labelName);
    await user.click(screen.getByRole("button", { name: `25 disagreements for ${labelName}` }));
    expect(screen.getByRole("dialog", { name: `Disagreed Items: ${labelName}` })).toBeVisible();
    expect(screen.getByText(`Disagreed Items: ${labelName}`)).toHaveAttribute("title", labelName);
  });
});
