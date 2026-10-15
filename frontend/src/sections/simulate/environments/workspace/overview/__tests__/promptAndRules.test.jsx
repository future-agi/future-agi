import { describe, it, expect, vi, beforeEach } from "vitest";
import { enqueueSnackbar } from "notistack";
import { fireEvent, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import AgentSummarySection from "../AgentSummarySection";

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

const PROMPT =
  "You are the support agent.\nCheck identity first.\nNever invent orders.";

describe("AgentSummarySection prompt", () => {
  it("offers no prompt when the API sent none", () => {
    render(<AgentSummarySection subject={{ name: "Support" }} />);

    expect(screen.queryByText("View prompt")).toBeNull();
  });

  it("opens the full prompt with its origin and line count", async () => {
    render(
      <AgentSummarySection
        subject={{ name: "Support" }}
        systemPrompt={PROMPT}
        systemPromptOrigin={{ kind: "system_prompt", field: "general_prompt" }}
      />,
    );

    await userEvent.click(screen.getByText("View prompt"));

    expect(await screen.findByText("Support · prompt")).toBeInTheDocument();
    expect(screen.getByText("3 lines")).toBeInTheDocument();
    expect(screen.getByText("SYSTEM PROMPT")).toBeInTheDocument();
    expect(screen.getByText(/Check identity first\./)).toBeInTheDocument();
  });

  it("shows the provider field a hosted agent's prompt was read from", async () => {
    render(
      <AgentSummarySection
        subject={{ name: "Support" }}
        systemPrompt={PROMPT}
        systemPromptOrigin={{ kind: "provider_field", field: "general_prompt" }}
      />,
    );

    await userEvent.click(screen.getByText("View prompt"));

    expect(await screen.findByText("PROVIDER FIELD")).toBeInTheDocument();
  });
});

describe("PromptDrawer copy", () => {
  beforeEach(() => vi.mocked(enqueueSnackbar).mockClear());

  const openAndCopy = async (writeText) => {
    render(<AgentSummarySection subject={{ name: "Support" }} systemPrompt={PROMPT} />);
    await userEvent.click(screen.getByText("View prompt"));
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    fireEvent.click(await screen.findByRole("button", { name: "Copy prompt" }));
  };

  it("says the prompt was copied once the clipboard accepts it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    await openAndCopy(writeText);

    expect(writeText).toHaveBeenCalledWith(PROMPT);
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Copied to clipboard", { variant: "info" }),
    );
  });

  it("says the copy failed when the clipboard rejects it", async () => {
    await openAndCopy(vi.fn().mockRejectedValue(new Error("denied")));

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Couldn't copy the prompt", { variant: "error" }),
    );
    expect(enqueueSnackbar).not.toHaveBeenCalledWith("Copied to clipboard", expect.anything());
  });
});
