import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

const checkAsk = vi.fn();
const launchAsk = vi.fn();
vi.mock("src/api/simulate-environments/runAsks", async (orig) => ({
  ...(await orig()),
  checkAsk: (...args) => checkAsk(...args),
  launchAsk: (...args) => launchAsk(...args),
}));

const { default: AskQuestionDialog } = await import("../AskQuestionDialog");

// What the backend's check returns for one question over four calls: three
// ready, one whose transcript was never uploaded.
const CHECK = {
  requested: 4,
  questions: [
    {
      name: "Did the agent confirm the refund?",
      kinds: ["transcript"],
      ready: 3,
      unmeasured: 1,
      not_applicable: 0,
      by_kind: {
        transcript: {
          present: 3,
          not_applicable: 0,
          not_captured: { not_uploaded: 1 },
        },
      },
    },
  ],
  answers: 3,
  credits_estimate: 1.5,
  priced_by_use: [],
  refusal: null,
};

const renderDialog = (props = {}) => {
  const onLaunched = vi.fn();
  const onClose = vi.fn();
  render(
    <AskQuestionDialog
      open
      onClose={onClose}
      executionId="ex1"
      selection={{ kind: "runs" }}
      onLaunched={onLaunched}
      {...props}
    />,
  );
  return { onLaunched, onClose };
};

const typeQuestion = (user, text = "Did the agent confirm the refund?") =>
  user.type(
    screen.getByLabelText("What do you want to know about each call?"),
    text,
  );
const checkButton = () => screen.getByRole("button", { name: /^Check/ });
const askButton = () => screen.getByRole("button", { name: /^Ask/ });

beforeEach(() => {
  checkAsk.mockReset();
  launchAsk.mockReset();
});

describe("AskQuestionDialog", () => {
  it("checks the whole run and shows requested, ready, unmeasured and why, and the cost", async () => {
    const user = userEvent.setup();
    checkAsk.mockResolvedValue(CHECK);
    renderDialog();

    expect(checkButton()).toBeDisabled();
    await typeQuestion(user);
    await user.click(checkButton());

    expect(checkAsk).toHaveBeenCalledWith("ex1", {
      selection: { kind: "runs" },
      questions: [
        {
          question: "Did the agent confirm the refund?",
          answer_type: "yes_no",
          kinds: ["transcript"],
        },
      ],
    });
    const summary = await screen.findByTestId("ask-check");
    expect(within(summary).getByText("4 calls requested")).toBeInTheDocument();
    expect(
      within(summary).getByText(
        "Question 1: 3 ready · 1 unmeasured · 0 not applicable",
      ),
    ).toBeInTheDocument();
    expect(
      within(summary).getByText("Transcript: 1 never uploaded"),
    ).toBeInTheDocument();
    expect(
      within(summary).getByText("3 answers · About 1.5 credits"),
    ).toBeInTheDocument();
    expect(askButton()).toHaveTextContent("Ask (3 answers)");
    expect(launchAsk).not.toHaveBeenCalled();
  });

  it("launches what was checked and hands back the new ask", async () => {
    const user = userEvent.setup();
    checkAsk.mockResolvedValue(CHECK);
    launchAsk.mockResolvedValue({ id: "ask-1", status: "running" });
    const { onLaunched } = renderDialog();

    await typeQuestion(user);
    await user.click(checkButton());
    await screen.findByTestId("ask-check");
    await user.click(askButton());

    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith("ask-1"));
    const [executionId, body, accepted] = launchAsk.mock.calls[0];
    expect(executionId).toBe("ex1");
    expect(body).toEqual(checkAsk.mock.calls[0][1]);
    expect(accepted).toEqual({ ...CHECK, launchKey: expect.any(String) });
  });

  it("sends the same launch key again when a failed launch is retried", async () => {
    const user = userEvent.setup();
    checkAsk.mockResolvedValue(CHECK);
    launchAsk
      .mockRejectedValueOnce({ message: "Network Error" })
      .mockResolvedValueOnce({ id: "ask-1", status: "running" });
    const { onLaunched } = renderDialog();

    await typeQuestion(user);
    await user.click(checkButton());
    await screen.findByTestId("ask-check");
    await user.click(askButton());
    expect(await screen.findByTestId("ask-error")).toHaveTextContent(
      "Network Error",
    );
    await user.click(askButton());

    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith("ask-1"));
    const [first, second] = launchAsk.mock.calls.map(([, , check]) => check);
    expect(first.launchKey).toEqual(expect.any(String));
    expect(second.launchKey).toBe(first.launchKey);
  });

  it("makes a new launch key for each new check", async () => {
    const user = userEvent.setup();
    checkAsk.mockResolvedValue(CHECK);
    launchAsk.mockResolvedValue({ id: "ask-1", status: "running" });
    renderDialog();

    await typeQuestion(user);
    await user.click(checkButton());
    await screen.findByTestId("ask-check");
    await user.click(askButton());
    await waitFor(() => expect(launchAsk).toHaveBeenCalledTimes(1));
    await typeQuestion(user, " Yes?");
    await user.click(checkButton());
    await screen.findByTestId("ask-check");
    await user.click(askButton());

    await waitFor(() => expect(launchAsk).toHaveBeenCalledTimes(2));
    const [first, second] = launchAsk.mock.calls.map(([, , check]) => check);
    expect(second.launchKey).not.toBe(first.launchKey);
  });

  it("keeps a launch that is still starting when the dialog is closed and opened again", async () => {
    const user = userEvent.setup();
    checkAsk.mockResolvedValue(CHECK);
    let land;
    launchAsk.mockReturnValue(
      new Promise((resolve) => {
        land = resolve;
      }),
    );
    const onLaunched = vi.fn();
    const props = {
      onClose: vi.fn(),
      executionId: "ex1",
      selection: { kind: "runs" },
      onLaunched,
    };
    const { rerender } = render(<AskQuestionDialog open {...props} />);

    await typeQuestion(user);
    await user.click(checkButton());
    await screen.findByTestId("ask-check");
    await user.click(askButton());
    rerender(<AskQuestionDialog open={false} {...props} />);
    rerender(<AskQuestionDialog open {...props} />);

    // Reopened while it starts: no second launch can start, and it says why.
    expect(await screen.findByTestId("ask-starting")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Starting…" })).toBeDisabled();
    land({ id: "ask-1", status: "running" });

    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith("ask-1"));
    expect(launchAsk).toHaveBeenCalledTimes(1);
  });

  it("launches once, however often Ask is pressed while it starts", async () => {
    const user = userEvent.setup();
    checkAsk.mockResolvedValue(CHECK);
    launchAsk.mockReturnValue(new Promise(() => {}));
    renderDialog();

    await typeQuestion(user);
    await user.click(checkButton());
    await screen.findByTestId("ask-check");
    await user.click(askButton());

    // While it starts, the button says so and takes no second press.
    const starting = screen.getByRole("button", { name: "Starting…" });
    expect(starting).toBeDisabled();
    fireEvent.click(starting);
    expect(checkButton()).toBeDisabled();
    expect(launchAsk).toHaveBeenCalledTimes(1);
  });

  it("asks about the ticked calls only", async () => {
    const user = userEvent.setup();
    checkAsk.mockResolvedValue({ ...CHECK, requested: 2 });
    renderDialog({ selection: { kind: "calls", callIds: ["c1", "c2"] } });

    expect(screen.getByText(/About the 2 selected calls/)).toBeInTheDocument();
    await typeQuestion(user);
    await user.click(checkButton());

    expect(checkAsk.mock.calls[0][1].selection).toEqual({
      kind: "calls",
      call_ids: ["c1", "c2"],
    });
  });

  it("throws the check away on any edit, so Ask needs a new check", async () => {
    const user = userEvent.setup();
    checkAsk.mockResolvedValue(CHECK);
    renderDialog();

    await typeQuestion(user);
    await user.click(checkButton());
    await screen.findByTestId("ask-check");
    await user.click(screen.getByRole("checkbox", { name: "Tool calls" }));

    expect(screen.queryByTestId("ask-check")).not.toBeInTheDocument();
    expect(askButton()).toBeDisabled();
  });

  it("shows why the launch would be refused and offers no Ask", async () => {
    const user = userEvent.setup();
    checkAsk.mockResolvedValue({
      ...CHECK,
      refusal: {
        code: "no_key",
        message: "Add a model key before asking questions.",
      },
    });
    renderDialog();

    await typeQuestion(user);
    await user.click(checkButton());

    expect(await screen.findByTestId("ask-refusal")).toHaveTextContent(
      "Add a model key before asking questions.",
    );
    expect(askButton()).toBeDisabled();
  });

  it("shows a refused check, such as calls still running, in the dialog", async () => {
    const user = userEvent.setup();
    checkAsk.mockRejectedValue({
      code: "not_finished",
      message: "1 of the 4 selected calls hasn't finished yet.",
      statusCode: 409,
    });
    renderDialog();

    await typeQuestion(user);
    await user.click(checkButton());

    expect(await screen.findByTestId("ask-error")).toHaveTextContent(
      "1 of the 4 selected calls hasn't finished yet.",
    );
    expect(askButton()).toBeDisabled();
  });

  it("checks again when the cost changed since the check, and does not launch", async () => {
    const user = userEvent.setup();
    checkAsk
      .mockResolvedValueOnce(CHECK)
      .mockResolvedValueOnce({ ...CHECK, credits_estimate: 4 });
    launchAsk.mockRejectedValue({
      code: "cost_changed",
      message: "The cost changed since it was checked. Check it again.",
      statusCode: 409,
    });
    const { onLaunched } = renderDialog();

    await typeQuestion(user);
    await user.click(checkButton());
    await screen.findByTestId("ask-check");
    await user.click(askButton());

    expect(await screen.findByTestId("ask-error")).toHaveTextContent(
      "The cost changed since it was checked.",
    );
    expect(
      await screen.findByText("3 answers · About 4 credits"),
    ).toBeInTheDocument();
    expect(checkAsk).toHaveBeenCalledTimes(2);
    expect(launchAsk).toHaveBeenCalledTimes(1);
    expect(onLaunched).not.toHaveBeenCalled();
  });

  it("needs two answers for a category question and sends them", async () => {
    const user = userEvent.setup();
    checkAsk.mockResolvedValue(CHECK);
    renderDialog();

    await typeQuestion(user, "How did the call end?");
    await user.click(screen.getByRole("radio", { name: "One of my answers" }));
    const answers = screen.getByLabelText("Answers, one per line (2 to 10)");
    await user.type(answers, "Resolved");
    expect(checkButton()).toBeDisabled();
    await user.type(answers, "{enter}Escalated{enter}Resolved");
    await user.click(checkButton());

    expect(checkAsk.mock.calls[0][1].questions[0]).toEqual({
      question: "How did the call end?",
      answer_type: "category",
      labels: ["Resolved", "Escalated"],
      kinds: ["transcript"],
    });
  });

  it("does not offer audio, which a typed question cannot read", () => {
    renderDialog();

    expect(
      screen.queryByRole("checkbox", { name: /audio/i }),
    ).not.toBeInTheDocument();
  });

  it("says when a question is billed on what it uses", async () => {
    const user = userEvent.setup();
    checkAsk.mockResolvedValue({
      ...CHECK,
      credits_estimate: null,
      priced_by_use: ["Tone check"],
    });
    renderDialog();

    await typeQuestion(user);
    await user.click(checkButton());

    expect(
      await screen.findByText(
        "3 answers · Tone check is billed on what it uses, so there is no price before it runs",
      ),
    ).toBeInTheDocument();
  });
});
