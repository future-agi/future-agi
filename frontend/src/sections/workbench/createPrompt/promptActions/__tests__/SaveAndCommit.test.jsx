import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import SaveAndCommit from "../SaveAndCommit";

const mocks = vi.hoisted(() => ({
  useMutation: vi.fn(),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: mocks.useMutation,
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock("react-router", () => ({
  useParams: () => ({ id: "prompt-123" }),
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));

function renderDialog() {
  return render(
    <SaveAndCommit
      open
      onClose={vi.fn()}
      data={{ version: "v1", isDefault: false, isDraft: false }}
      promptName="My Prompt"
    />,
  );
}

const getCommitButton = () => screen.getByRole("button", { name: /^Commit$/ });

describe("SaveAndCommit — Commit button state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false });
  });

  it("keeps the Commit button disabled while the message field is empty", () => {
    renderDialog();
    expect(getCommitButton()).toBeDisabled();
  });

  it("enables the Commit button once a message is entered", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(
      screen.getByPlaceholderText(/Enter a commit message/i),
      "my commit",
    );

    expect(getCommitButton()).toBeEnabled();
  });

  it("does not hardcode the disabled text color once the button is active", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(
      screen.getByPlaceholderText(/Enter a commit message/i),
      "my commit",
    );

    // Regression guard for #1384: the enabled button must not carry the
    // hardcoded `text.disabled` color that made it look greyed out.
    expect(getCommitButton()).not.toHaveStyle({ color: "rgba(0, 0, 0, 0.38)" });
  });

  it("shows a loading state and stays disabled while the request is in flight", () => {
    mocks.useMutation.mockReturnValue({ mutate: vi.fn(), isPending: true });
    renderDialog();
    expect(getCommitButton()).toBeDisabled();
  });
});
