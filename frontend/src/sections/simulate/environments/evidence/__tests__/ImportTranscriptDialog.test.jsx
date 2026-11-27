import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

const { default: axios } = await import("src/utils/axios");
const { default: ImportTranscriptDialog } = await import(
  "../ImportTranscriptDialog"
);
const { guessFormat } = await import("../evidence.constants");

const ENV = "11111111-1111-1111-1111-111111111111";
const IMPORT = "22222222-2222-2222-2222-222222222222";
const URL = `/simulate/api/harness-environments/${ENV}/evidence-imports/`;
const PASTED = "User: Refund my order\nAgent: Done.";

// The way in (Add scenarios, then Upload transcript or file) is the Add
// scenarios panel's test; this opens the dialog itself.
const openDialog = async () => {
  const onImported = vi.fn();
  const onClose = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <ImportTranscriptDialog
        open
        onClose={onClose}
        envId={ENV}
        onImported={onImported}
      />
    </QueryClientProvider>,
  );
  return { onImported, onClose };
};

const paste = (text) =>
  fireEvent.change(screen.getByLabelText("Transcript"), {
    target: { value: text },
  });

// A browser File reads its text; this jsdom's has no text().
const file = (text, name, type) =>
  Object.assign(new File([text], name, { type }), { text: async () => text });

beforeEach(() => {
  axios.post.mockReset();
});

describe("ImportTranscriptDialog", () => {
  it("says what a transcript import replaces and keeps", async () => {
    await openDialog();
    expect(
      screen.getByText(
        "Emails, phone and card numbers, SSNs and API keys become placeholders. Up to 60,000 characters per import.",
      ),
    ).toBeInTheDocument();
  });

  it("imports a pasted transcript, not called production, and opens its review", async () => {
    axios.post.mockResolvedValue({ data: { id: IMPORT, cases: [] } });
    const { onImported, onClose } = await openDialog();
    const importButton = screen.getByRole("button", {
      name: "Create environment test",
    });
    expect(importButton).toBeDisabled();

    paste(PASTED);
    expect(screen.getByRole("combobox", { name: /^Format/ })).toHaveTextContent(
      "User: / Agent: lines",
    );
    await userEvent.click(importButton);

    await waitFor(() => expect(onImported).toHaveBeenCalledWith(IMPORT));
    expect(axios.post).toHaveBeenCalledWith(URL, {
      source_kind: "transcript",
      transcripts: [
        { name: "Pasted transcript", format: "text", text: PASTED },
      ],
      production: false,
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("sends production only when the person says so, with the name they gave", async () => {
    axios.post.mockResolvedValue({ data: { id: IMPORT, cases: [] } });
    await openDialog();
    const messages = '[{"role": "user", "content": "hi"}]';

    paste(messages);
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Escalation 7" },
    });
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: "From production",
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create environment test" }),
    );

    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(URL, {
        source_kind: "transcript",
        transcripts: [{ name: "Escalation 7", format: "json", text: messages }],
        production: true,
      }),
    );
  });

  it("imports uploaded files as one transcript each, with their guessed formats", async () => {
    axios.post.mockResolvedValue({ data: { id: IMPORT, cases: [] } });
    await openDialog();
    const csv = "role,text\nuser,Where is it?\nassistant,On its way.\n";

    // The drop zone's own file input, the one Browse files opens.
    fireEvent.change(document.querySelector('input[type="file"]'), {
      target: {
        files: [
          file(csv, "calls.csv", "text/csv"),
          file(PASTED, "chat.txt", "text/plain"),
        ],
      },
    });

    const listed = await screen.findByTestId("transcript-files");
    expect(within(listed).getByText("calls.csv")).toBeInTheDocument();
    expect(
      within(listed)
        .getAllByRole("combobox")
        .map((one) => one.textContent),
    ).toEqual(["CSV with role and text", "User: / Agent: lines"]);
    expect(screen.queryByLabelText("Transcript")).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Create environment test" }),
    );

    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(URL, {
        source_kind: "transcript",
        transcripts: [
          { name: "calls.csv", format: "csv", text: csv },
          { name: "chat.txt", format: "text", text: PASTED },
        ],
        production: false,
      }),
    );
  });

  it("offers a drop zone that names the files it takes", async () => {
    await openDialog();
    expect(
      screen.getByText("Choose files or drag & drop them here"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "JSON messages (.json), User: / Agent: lines (.txt) or CSV with role and text (.csv)",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Up to 20 files, 500,000 characters each"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Browse files" }),
    ).toBeInTheDocument();
  });

  it("adds dropped files to the ones already there, and removes one", async () => {
    await openDialog();
    const input = () => document.querySelector('input[type="file"]');

    fireEvent.change(input(), {
      target: { files: [file(PASTED, "one.txt", "text/plain")] },
    });
    await screen.findByText("one.txt");
    fireEvent.change(input(), {
      target: { files: [file(PASTED, "two.txt", "text/plain")] },
    });
    await screen.findByText("two.txt");
    expect(screen.getByText("one.txt")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Remove one.txt" }),
    );
    expect(screen.queryByText("one.txt")).toBeNull();
    expect(screen.getByText("two.txt")).toBeInTheDocument();
  });

  it("shows the line the backend could not read, and keeps the text", async () => {
    const message =
      "Transcript 1 (Pasted transcript): Line 2: Unknown speaker 'Boss': use User or Agent.";
    axios.post.mockRejectedValue({
      statusCode: 400,
      error: "transcript_unreadable",
      message,
    });
    const { onImported } = await openDialog();

    paste("User: hi\nBoss: no");
    await userEvent.click(
      screen.getByRole("button", { name: "Create environment test" }),
    );

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(onImported).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Transcript")).toHaveValue(
      "User: hi\nBoss: no",
    );
  });

  it("refuses a transcript longer than the backend reads, before sending", async () => {
    await openDialog();

    paste(`User: ${"x".repeat(500_000)}`);

    expect(
      screen.getByText("Pasted transcript is longer than 500,000 characters."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create environment test" }),
    ).toBeDisabled();
    expect(axios.post).not.toHaveBeenCalled();
  });
});

describe("guessFormat", () => {
  it.each([
    ["chat.json", "User: hi", "json"],
    ["calls.CSV", "anything", "csv"],
    ["notes.txt", "[1]", "text"],
    ["", '  {"messages": []}', "json"],
    ["", "\ufeffSpeaker,Message\nuser,hi", "csv"],
    ["", "User: hi, there", "text"],
  ])("%s %j is %s", (name, text, expected) => {
    expect(guessFormat(name, text)).toBe(expected);
  });
});
