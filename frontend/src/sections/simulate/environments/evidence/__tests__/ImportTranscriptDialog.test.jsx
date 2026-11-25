import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

const nav = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => nav.navigate,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

const { default: axios } = await import("src/utils/axios");
const { default: ImportEvidenceButton } = await import(
  "../ImportEvidenceButton"
);
const { guessFormat } = await import("../evidence.constants");

const ENV = "11111111-1111-1111-1111-111111111111";
const IMPORT = "22222222-2222-2222-2222-222222222222";
const URL = `/simulate/api/harness-environments/${ENV}/evidence-imports/`;
const PASTED = "User: Refund my order\nAgent: Done.";

// The way in, as the Scenarios tab shows it: Import evidence, then Add a
// transcript.
const openDialog = async () => {
  const onImported = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <ImportEvidenceButton envId={ENV} onImported={onImported} />
    </QueryClientProvider>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Import evidence" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Add a transcript" }),
  );
  return { onImported };
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
  nav.navigate.mockReset();
});

describe("ImportEvidenceButton", () => {
  it("offers a way in for each source the hint names", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ImportEvidenceButton envId={ENV} onImported={vi.fn()} />
      </QueryClientProvider>,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Import evidence" }),
    );
    expect(
      screen.getByText(
        /or rows in a dataset, then choose Create environment test/,
      ),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Open a dataset" }),
    );
    expect(nav.navigate).toHaveBeenLastCalledWith("/dashboard/develop");
    await userEvent.click(screen.getByRole("button", { name: "Open Observe" }));
    expect(nav.navigate).toHaveBeenLastCalledWith("/dashboard/observe");
  });

  it("says what a transcript import replaces and keeps", async () => {
    await openDialog();
    expect(
      screen.getByText(
        /Emails, phone, card and ID numbers become placeholders; an import keeps 60,000 characters, shared by its files\.$/,
      ),
    ).toBeInTheDocument();
  });
});

describe("ImportTranscriptDialog", () => {
  it("imports a pasted transcript, not called production, and opens its review", async () => {
    axios.post.mockResolvedValue({ data: { id: IMPORT, cases: [] } });
    const { onImported } = await openDialog();
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
    expect(screen.queryByLabelText("Transcript")).toBeNull();
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
        name: "These transcripts come from production",
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

    fireEvent.change(screen.getByTestId("transcript-file-input"), {
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
