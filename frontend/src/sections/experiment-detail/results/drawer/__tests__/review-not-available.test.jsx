// The experiment Review tab in this release: saving reviews isn't available.
// The rubric card's Submit & next, the pairwise card's votes and the
// correction card's Save correction stay on screen turned off, each card
// says so once, no click or key sends a review request, and nothing on the
// tab reads as saved. The end-of-queue summary shows the sentence in place
// of a count, and its Review the skipped stays on screen turned off.
/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  renderWithRouter,
  screen,
  userEvent,
  within,
} from "src/utils/test-utils";
import notify from "src/sections/common/ux/toast";
import ReviewTab from "../ReviewTab";
import { myPairVotes } from "../usePairVote";

const { post, get } = vi.hoisted(() => ({
  post: vi.fn(() => Promise.resolve({ data: { result: {} } })),
  get: vi.fn(() => Promise.resolve({ data: { result: {} } })),
}));
vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: { ...actual.default, post, get },
  };
});
vi.mock("src/sections/common/ux/toast", () => ({
  default: vi.fn(),
  notify: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/ValueViewer/ValueViewer", () => ({
  default: ({ value }) => <div>{String(value)}</div>,
}));
vi.mock("src/sections/common/ValueViewer/useValueViewMode", () => ({
  default: () => ["text"],
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "user-1" } }),
}));
vi.mock("src/sections/common/Runs/runColors", () => ({
  useRunColors: () => ({}),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "dataset" }),
  rowLabel: (_, n) => `Row ${n}`,
}));
// The comments under the cards read their own queries; they render nothing
// here.
vi.mock("src/sections/common/Collab/RunCommentsSection", () => ({
  default: () => null,
}));
const LABELS = [
  {
    id: "correct",
    name: "Correct?",
    type: "thumbs_up_down",
    settings: {},
  },
  {
    id: "help",
    name: "Helpfulness",
    type: "numeric",
    settings: { min: 0, max: 1, step_size: 0.1, display_type: "slider" },
  },
];
vi.mock("src/api/annotation-labels/annotation-labels", () => ({
  useAnnotationLabelsList: () => ({
    data: { results: LABELS },
    isLoading: false,
  }),
}));
// The rubric queue is found; the review submit is the app's own.
vi.mock("src/api/develop/review", async (importOriginal) => ({
  ...(await importOriginal()),
  useReviewQueueFor: (_, { mode }) => ({
    queue:
      mode === "rubric"
        ? {
            name: "Quality · #2B",
            labels: [{ label_id: "correct" }, { label_id: "help" }],
          }
        : null,
  }),
}));

const SENTENCE = "Saving reviews isn't available in this release.";
const RUN = { run_id: "run-2b", label: "#2B", name: "Support bot v2" };
const ROW = {
  example_id: "ex-12",
  row_order: 12,
  results: { "run-2b": { status: "ok", output: "Refund issued" } },
};

const reviewRequests = () =>
  post.mock.calls.filter(([url]) => String(url).includes("result-reviews"));

// One run in view: the rubric card.
const renderRubric = (onNext = vi.fn()) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ReviewTab
        datasetId="ds-1"
        row={ROW}
        runs={[RUN]}
        baselineId="run-2b"
        focusedRunId="run-2b"
        onNext={onNext}
      />
    </QueryClientProvider>,
  );
// The rubric card with every label rated.
const renderRated = async (onNext = vi.fn()) => {
  const user = userEvent.setup();
  renderRubric(onNext);
  await user.click(screen.getByText("Yes"));
  fireEvent.change(screen.getByRole("spinbutton", { name: "Helpfulness" }), {
    target: { value: "0.5" },
  });
  return { user, onNext };
};
const submitButton = () =>
  screen.getByRole("button", { name: /submit & next/i });
// Lets any request a key or click started reach the network layer.
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

// Two runs in view: the pairwise card, on the row the URL names.
const RUN_A = { run_id: "run-a", label: "#3A", name: "Support bot v1" };
const RUN_B = { run_id: "run-b", label: "#4A", name: "Support bot v2" };
const PAIR_ROW = {
  example_id: "ex-21",
  row_order: 21,
  results: {
    "run-a": { status: "ok", output: "Refund issued" },
    "run-b": { status: "ok", output: "Refund issued on 3 March" },
  },
};
const renderPair = (onNext = vi.fn()) => {
  renderWithRouter(
    <QueryClientProvider client={new QueryClient()}>
      <ReviewTab
        datasetId="ds-1"
        row={PAIR_ROW}
        runs={[RUN_A, RUN_B]}
        baselineId="run-a"
        focusedRunId="run-b"
        onNext={onNext}
      />
    </QueryClientProvider>,
    { route: "/?row=ex-21" },
  );
  return { onNext };
};
// The pair on the last row of the view: S opens the end-of-queue summary.
const renderQueueEnd = (onClose = vi.fn()) => {
  renderWithRouter(
    <QueryClientProvider client={new QueryClient()}>
      <ReviewTab
        datasetId="ds-1"
        row={PAIR_ROW}
        runs={[RUN_A, RUN_B]}
        baselineId="run-a"
        focusedRunId="run-b"
        rowIds={["ex-20", "ex-21"]}
        onClose={onClose}
      />
    </QueryClientProvider>,
    { route: "/?row=ex-21" },
  );
  fireEvent.keyDown(document.body, { key: "s", bubbles: true });
  return { onClose };
};
const voteButtons = () =>
  Array.from(document.querySelectorAll("[data-review-vote]"));
const savedToasts = () =>
  notify.mock.calls.filter(
    ([text, options]) => /saved/i.test(String(text)) || options?.undo,
  );

beforeEach(() => {
  post.mockClear();
  get.mockClear();
  notify.mockClear();
  myPairVotes.clear();
  window.localStorage.clear();
});

describe("ReviewTab rubric card: saving reviews isn't available in this release", () => {
  it("Submit & next stays on screen, turned off, described by the sentence", async () => {
    await renderRated();
    const button = submitButton();
    expect(button).toBeVisible();
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(SENTENCE);
    expect(screen.getAllByText(SENTENCE)).toHaveLength(1);
  });

  it("a click on Submit & next sends no review request", async () => {
    await renderRated();
    fireEvent.click(submitButton());
    await settle();
    expect(reviewRequests()).toEqual([]);
  });

  it("⌘↵ and Ctrl↵ send no review request", async () => {
    await renderRated();
    fireEvent.keyDown(document.body, {
      key: "Enter",
      metaKey: true,
      bubbles: true,
    });
    fireEvent.keyDown(document.body, {
      key: "Enter",
      ctrlKey: true,
      bubbles: true,
    });
    await settle();
    expect(reviewRequests()).toEqual([]);
  });

  it("S opens the next row and sends no review request", async () => {
    const { onNext } = await renderRated();
    fireEvent.keyDown(document.body, { key: "s", bubbles: true });
    await settle();
    expect(onNext).toHaveBeenCalledTimes(1);
    expect(reviewRequests()).toEqual([]);
  });

  it("nothing on the card reads as saved, notes included", async () => {
    const { user } = await renderRated();
    await user.type(
      screen.getByPlaceholderText("What makes this response good or bad?"),
      "Clear and correct",
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 350));
    });
    expect(screen.queryByText(/\bsaved\b|saves to/i)).toBeNull();
    expect(screen.queryByText(/to submit$/)).toBeNull();
    expect(screen.getByText(SENTENCE)).toBeInTheDocument();
  });
});

describe("ReviewTab pairwise card: saving reviews isn't available in this release", () => {
  it("the three votes stay on screen, turned off, described by the sentence shown once", () => {
    renderPair();
    const votes = voteButtons();
    expect(votes).toHaveLength(3);
    votes.forEach((vote) => {
      expect(vote).toBeVisible();
      expect(vote).toBeDisabled();
      expect(vote).toHaveAccessibleDescription(SENTENCE);
    });
    expect(screen.getAllByText(SENTENCE)).toHaveLength(1);
  });

  it("a click on a vote and keys 1, 2 and 3 send no review request and nothing says saved", async () => {
    const { onNext } = renderPair();
    voteButtons().forEach((vote) => fireEvent.click(vote));
    ["1", "2", "3"].forEach((key) =>
      fireEvent.keyDown(document.body, { key, bubbles: true }),
    );
    await settle();
    expect(reviewRequests()).toEqual([]);
    expect(savedToasts()).toEqual([]);
    expect(screen.queryByText(/\bsaved\b/i)).toBeNull();
    expect(onNext).not.toHaveBeenCalled();
  });

  it("a pick made on the row shows as the person's pick, never as saved; Clear choice and the comment send no request", async () => {
    myPairVotes.set("run-a|run-b|ex-21", { choice: "B", notes: "", at: 1 });
    renderPair();
    expect(screen.getByText(/^Your vote: /)).not.toHaveTextContent(/saved/i);
    const clear = screen.getByRole("button", { name: "Clear choice" });
    expect(clear).toBeVisible();
    expect(clear).toBeDisabled();
    expect(clear).toHaveAccessibleDescription(SENTENCE);
    fireEvent.click(clear);
    fireEvent.click(screen.getByRole("button", { name: "+ Comment" }));
    const field = screen.getByPlaceholderText(/^Why\?/);
    expect(field.getAttribute("placeholder")).not.toMatch(/saved/i);
    fireEvent.change(field, { target: { value: "B names the refund date" } });
    fireEvent.blur(field);
    await settle();
    expect(reviewRequests()).toEqual([]);
    expect(savedToasts()).toEqual([]);
    expect(screen.queryByText(/\bsaved\b/i)).toBeNull();
  });

  it("Auto-advance to the next row stays on screen as read, turned off, described by the sentence; a click writes no setting", () => {
    renderPair();
    // The switch's own input is see-through; its label is what shows.
    expect(screen.getByText("Auto-advance to the next row")).toBeVisible();
    const toggle = screen.getByLabelText("Auto-advance to the next row");
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAccessibleDescription(SENTENCE);
    fireEvent.click(toggle);
    expect(window.localStorage.getItem("fa.review.autoAdvance")).toBeNull();
    expect(screen.getAllByText(SENTENCE)).toHaveLength(1);
  });

  it('Auto-advance shows a stored "0" as off, and the stored value stays "0" after a click', () => {
    window.localStorage.setItem("fa.review.autoAdvance", "0");
    renderPair();
    const toggle = screen.getByLabelText("Auto-advance to the next row");
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeDisabled();
    fireEvent.click(toggle);
    expect(window.localStorage.getItem("fa.review.autoAdvance")).toBe("0");
  });
});

describe("ReviewTab end-of-queue summary: saving reviews isn't available in this release", () => {
  it("shows the sentence once in place of the count and the votes line", () => {
    renderQueueEnd();
    const panel = within(screen.getByTestId("review-queue-done"));
    expect(panel.getByText(SENTENCE)).toBeVisible();
    expect(screen.getAllByText(SENTENCE)).toHaveLength(1);
    expect(
      panel.queryAllByText(/reviewed|better|worse|same|your votes/i),
    ).toHaveLength(0);
  });

  it("Review the skipped stays on screen, turned off, described by the sentence; a click keeps the summary open", () => {
    renderQueueEnd();
    const panel = within(screen.getByTestId("review-queue-done"));
    const skipped = panel.getByRole("button", { name: "Review the skipped" });
    expect(skipped).toBeVisible();
    expect(skipped).toBeDisabled();
    expect(skipped).toHaveAccessibleDescription(SENTENCE);
    fireEvent.click(skipped);
    expect(screen.getByTestId("review-queue-done")).toBeInTheDocument();
  });

  it("Review the skipped shows no count: its name and text are exactly Review the skipped, with no digit", () => {
    renderQueueEnd();
    const panel = within(screen.getByTestId("review-queue-done"));
    const skipped = panel.getByTestId("review-skipped");
    expect(skipped).toHaveAccessibleName("Review the skipped");
    expect(skipped.textContent).toBe("Review the skipped");
    expect(skipped.textContent).not.toMatch(/\d/);
    expect(skipped).toBeDisabled();
    expect(skipped).toHaveAccessibleDescription(SENTENCE);
  });

  it("Back to results stays on and closes the drawer; nothing sends a review request", async () => {
    const { onClose } = renderQueueEnd();
    const panel = within(screen.getByTestId("review-queue-done"));
    const back = panel.getByRole("button", { name: "Back to results" });
    expect(back).toBeEnabled();
    fireEvent.click(back);
    await settle();
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(reviewRequests()).toEqual([]);
  });
});

describe("ReviewTab correction card: saving reviews isn't available in this release", () => {
  it("Save correction stays on screen, turned off, described by the sentence shown once; a click sends no request", async () => {
    renderRubric();
    fireEvent.click(
      screen.getByRole("button", { name: "Correct this answer" }),
    );
    const card = within(screen.getByTestId("correct-answer"));
    fireEvent.change(card.getByLabelText("Corrected answer"), {
      target: { value: "Refund issued on 3 March" },
    });
    const save = card.getByRole("button", { name: "Save correction" });
    expect(save).toBeVisible();
    expect(save).toBeDisabled();
    expect(save).toHaveAccessibleDescription(SENTENCE);
    expect(card.getAllByText(SENTENCE)).toHaveLength(1);
    fireEvent.click(save);
    await settle();
    expect(reviewRequests()).toEqual([]);
    expect(screen.queryByText(/\bsaved\b/i)).toBeNull();
  });
});
