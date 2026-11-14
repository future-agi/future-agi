import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import { paths } from "src/routes/paths";
import QueueAgreementTab from "../queue-agreement-tab";

const mocks = vi.hoisted(() => ({
  useQueueAgreement: vi.fn(),
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useQueueAgreement: (...args) => mocks.useQueueAgreement(...args),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

const cappedItemIds = Array.from({ length: 20 }, (_, i) => `capped-${i + 1}`);

const agreement = {
  overall_agreement: 0.5,
  labels: {
    "label-sentiment": {
      label_name: "Sentiment",
      label_type: "categorical",
      agreement_pct: 0.5,
      cohens_kappa: 0.2,
      disagreement_count: 2,
      disagreement_items: ["item-1", "item-2"],
    },
    "label-tone": {
      label_name: "Tone",
      label_type: "text",
      agreement_pct: 1,
      cohens_kappa: null,
      disagreement_count: 0,
      disagreement_items: [],
    },
    "label-quality": {
      label_name: "Quality",
      label_type: "star",
      agreement_pct: 0.2,
      cohens_kappa: 0.1,
      disagreement_count: 25,
      disagreement_items: cappedItemIds,
    },
  },
  annotator_pairs: [],
};

const workspaceHref = (itemId) =>
  `${paths.dashboard.annotations.annotate("queue-1")}?itemId=${itemId}&mode=review`;

describe("QueueAgreementTab disagreements", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useQueueAgreement.mockReturnValue({
      data: agreement,
      isLoading: false,
    });
  });

  it("shows a non-zero disagreement count as a button and keeps zero as plain text", () => {
    render(<QueueAgreementTab queueId="queue-1" />);

    const button = screen.getByRole("button", {
      name: "View 2 disagreed items for Sentiment",
    });
    expect(button).toHaveTextContent("2");
    expect(button).toHaveAttribute("aria-expanded", "false");

    const toneRow = screen.getByText("Tone").closest("tr");
    expect(within(toneRow).queryByRole("button")).toBeNull();
    expect(toneRow).toHaveTextContent("0");
  });

  it("reveals the disagreed items as links to that item in the annotation workspace", async () => {
    const user = userEvent.setup();
    render(<QueueAgreementTab queueId="queue-1" />);

    expect(screen.queryByRole("link", { name: "item-1" })).toBeNull();

    const button = screen.getByRole("button", {
      name: "View 2 disagreed items for Sentiment",
    });
    await user.click(button);

    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: "item-1" })).toHaveAttribute(
      "href",
      workspaceHref("item-1"),
    );
    expect(screen.getByRole("link", { name: "item-2" })).toHaveAttribute(
      "href",
      workspaceHref("item-2"),
    );
    expect(screen.queryByText(/^Showing \d+ of \d+/)).toBeNull();

    await user.click(button);

    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("link", { name: "item-1" })).toBeNull();
  });

  it("opens the disagreed items from the keyboard", async () => {
    const user = userEvent.setup();
    render(<QueueAgreementTab queueId="queue-1" />);

    screen
      .getByRole("button", { name: "View 2 disagreed items for Sentiment" })
      .focus();
    await user.keyboard("{Enter}");

    expect(screen.getByRole("link", { name: "item-1" })).toBeInTheDocument();
  });

  it("says more disagreements exist when the count is above the returned items", async () => {
    const user = userEvent.setup();
    render(<QueueAgreementTab queueId="queue-1" />);

    await user.click(
      screen.getByRole("button", {
        name: "View 25 disagreed items for Quality",
      }),
    );

    expect(
      screen.getByText("Showing 20 of 25 disagreed items."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "capped-20" })).toHaveAttribute(
      "href",
      workspaceHref("capped-20"),
    );
    expect(
      screen.getAllByRole("link", { name: /^capped-\d+$/ }),
    ).toHaveLength(20);
  });

  it("uses the singular for a single disagreement", () => {
    mocks.useQueueAgreement.mockReturnValue({
      data: {
        ...agreement,
        labels: {
          "label-sentiment": {
            ...agreement.labels["label-sentiment"],
            disagreement_count: 1,
            disagreement_items: ["item-1"],
          },
        },
      },
      isLoading: false,
    });
    render(<QueueAgreementTab queueId="queue-1" />);

    expect(
      screen.getByRole("button", {
        name: "View 1 disagreed item for Sentiment",
      }),
    ).toBeInTheDocument();
  });
});
