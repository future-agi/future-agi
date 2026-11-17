import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import {
  mutations,
  resetMutations,
  useFakeMutation,
} from "./agentVersionsTestUtils";

vi.mock("src/api/simulate-environments/versions", async () => ({
  ...(await vi.importActual("src/api/simulate-environments/versions")),
  useCreateAgentVersion: vi.fn(() => useFakeMutation("create")),
}));
vi.mock("src/api/harness/harness", () => ({
  uploadHarnessSource: vi.fn(),
}));
vi.mock("src/pages/dashboard/harness/sourceUpload", async () => ({
  ...(await vi.importActual("src/pages/dashboard/harness/sourceUpload")),
  prepareSourceFolder: vi.fn(),
}));

const { uploadHarnessSource } = await import("src/api/harness/harness");
const { prepareSourceFolder } = await import(
  "src/pages/dashboard/harness/sourceUpload"
);
const { default: AgentSourceChange } = await import("../AgentSourceChange");
const { STUCK_AFTER_MS } = await import(
  "src/api/simulate-environments/versions"
);
const {
  simulateApiHarnessEnvironmentsVersionsCreateAgentVersionBodyPromptMax:
    PROMPT_MAX_CHARS,
} = await import("src/generated/api-contracts/api.zod");

const PROMPT = "Answer billing questions briefly.";
const NUMBER = "+14155550100";
const PHONE = {
  source: { kind: "phone", phone_number: NUMBER },
  prompt: PROMPT,
};

const renderFor = (defaultVersion, versions) =>
  render(
    <AgentSourceChange
      envId="env-1"
      defaultVersion={defaultVersion}
      versions={versions}
    />,
  );
const RUNNING = [
  {
    id: "a9",
    preflightStatus: "pending",
    requestedHere: true,
    createdAt: new Date().toISOString(),
  },
];
const button = (name) => screen.getByRole("button", { name });
const promptField = () => screen.getByLabelText("Prompt");
const created = (label = "v8") => {
  mutations.create.data = { agent_version: { id: "a8", label } };
};

beforeEach(() => {
  resetMutations();
  uploadHarnessSource.mockReset();
  prepareSourceFolder.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("AgentSourceChange", () => {
  it("says a public GitHub source is watched and offers no action", () => {
    renderFor({
      source: {
        kind: "github",
        repository: "acme/agent",
        ref: "main",
        visibility: "public",
      },
    });
    expect(screen.getByText("Watching acme/agent @ main")).toBeInTheDocument();
    expect(
      screen.getByText(/New commits become new agent versions automatically/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says a private repository isn't watched, and names the default branch", () => {
    renderFor({
      source: {
        kind: "github",
        repository: "acme/agent",
        visibility: "private",
      },
    });
    expect(
      screen.getByText("Built from acme/agent @ the default branch"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Watching/)).toBeNull();
    expect(
      screen.getByText("Private repositories aren't watched for new commits."),
    ).toBeInTheDocument();
  });

  it("says a GitHub source pinned to a commit isn't watched", () => {
    renderFor({
      source: {
        kind: "github",
        repository: "acme/agent",
        ref: "a".repeat(40),
        visibility: "public",
      },
    });
    expect(
      screen.getByText(`Built from acme/agent @ ${"a".repeat(40)}`),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Watching/)).toBeNull();
    expect(screen.queryByText(/New commits become/)).toBeNull();
  });

  it.each([
    ["an upload", { source: { kind: "archive" } }, "Upload new source"],
    ["a prompt edit", PHONE, "Edit prompt"],
  ])("holds %s while another change's check runs", (_, version, name) => {
    const action = () => screen.getByText(name).closest(".MuiButton-root");
    const { rerender } = renderFor(version, RUNNING);
    expect(action()).toHaveClass("Mui-disabled");
    expect(
      screen.getByText("Another change is still being checked"),
    ).toBeVisible();

    rerender(
      <AgentSourceChange
        envId="env-1"
        defaultVersion={version}
        versions={[]}
      />,
    );
    expect(action()).not.toHaveClass("Mui-disabled");
    expect(
      screen.queryByText("Another change is still being checked"),
    ).toBeNull();
  });

  it("frees the actions once a running check is stuck", () => {
    vi.useFakeTimers();
    const started = new Date(Date.now() - STUCK_AFTER_MS + 1000).toISOString();
    renderFor({ source: { kind: "archive" } }, [
      { ...RUNNING[0], createdAt: started },
    ]);
    const action = () =>
      screen.getByText("Upload new source").closest(".MuiButton-root");
    expect(action()).toHaveClass("Mui-disabled");

    act(() => vi.advanceTimersByTime(1001));
    expect(action()).not.toHaveClass("Mui-disabled");
  });

  it("holds an open prompt edit while another change's check runs", () => {
    const { rerender } = renderFor(PHONE);
    fireEvent.click(button("Edit prompt"));
    fireEvent.change(promptField(), { target: { value: "New prompt" } });
    rerender(
      <AgentSourceChange
        envId="env-1"
        defaultVersion={PHONE}
        versions={RUNNING}
      />,
    );
    expect(button("Save")).toBeDisabled();
    expect(
      screen.getByText("Another change is still being checked"),
    ).toBeVisible();
  });

  it("keeps a refusal shown while another change's check runs", () => {
    mutations.create.answer = {
      error: { statusCode: 422, error: "version_not_runnable" },
    };
    const { rerender } = renderFor(PHONE);
    fireEvent.click(button("Edit prompt"));
    fireEvent.change(promptField(), { target: { value: "New prompt" } });
    fireEvent.click(button("Save"));
    rerender(
      <AgentSourceChange
        envId="env-1"
        defaultVersion={PHONE}
        versions={RUNNING}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "A changed agent can't run yet",
    );
  });

  it("shows the version being made once the upload is done", async () => {
    mutations.create.answer = "pending";
    prepareSourceFolder.mockReturnValue({ files: [], paths: [], name: "a" });
    uploadHarnessSource.mockResolvedValue({ source_id: "src-9" });
    const { container } = renderFor({ source: { kind: "archive" } });

    fireEvent.change(container.querySelector("input[type=file]"), {
      target: { files: [new File(["x"], "a.py")] },
    });

    expect(await screen.findByText("Saving…")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("uploads a new folder and makes the next version from it", async () => {
    created();
    const file = new File(["print()"], "main.py");
    prepareSourceFolder.mockReturnValue({
      files: [file],
      paths: ["main.py"],
      name: "agent",
    });
    let progress;
    let finish;
    uploadHarnessSource.mockImplementation((formData, onProgress) => {
      progress = onProgress;
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    const { container } = renderFor({ source: { kind: "archive" } });

    fireEvent.change(container.querySelector("input[type=file]"), {
      target: { files: [file] },
    });
    await waitFor(() => expect(uploadHarnessSource).toHaveBeenCalled());
    progress({ loaded: 1, total: 4 });
    expect(await screen.findByText("Uploading… 25%")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    finish({ source_id: "src-9" });

    await waitFor(() =>
      expect(mutations.create.calls).toEqual([
        { archive_artifact_id: "src-9" },
      ]),
    );
    expect(
      screen.getByText("New version v8 is being checked."),
    ).toBeInTheDocument();
  });

  it("explains an expired upload", async () => {
    mutations.create.answer = {
      error: { statusCode: 422, error: "archive_source_not_found" },
    };
    prepareSourceFolder.mockReturnValue({ files: [], paths: [], name: "a" });
    uploadHarnessSource.mockResolvedValue({ source_id: "src-9" });
    const { container } = renderFor({ source: { kind: "archive" } });

    fireEvent.change(container.querySelector("input[type=file]"), {
      target: { files: [new File(["x"], "a.py")] },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The upload expired; upload the folder again.",
    );
  });

  it("names too many files when the runner refuses the upload", async () => {
    prepareSourceFolder.mockReturnValue({ files: [], paths: [], name: "a" });
    uploadHarnessSource.mockRejectedValue({ statusCode: 400 });
    const { container } = renderFor({ source: { kind: "archive" } });

    fireEvent.change(container.querySelector("input[type=file]"), {
      target: { files: [new File(["x"], "a.py")] },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This folder has too many files for the runner to accept in one upload.",
    );
    expect(mutations.create.calls).toEqual([]);
  });

  it("refuses a folder it can't upload before sending anything", async () => {
    prepareSourceFolder.mockImplementation(() => {
      throw new Error("Source uploads support at most 200 MiB.");
    });
    const { container } = renderFor({ source: { kind: "archive" } });

    fireEvent.change(container.querySelector("input[type=file]"), {
      target: { files: [new File(["x"], "a.py")] },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Source uploads support at most 200 MiB.",
    );
    expect(uploadHarnessSource).not.toHaveBeenCalled();
    expect(mutations.create.calls).toEqual([]);
  });

  it("edits a phone agent's prompt from the default's", () => {
    created();
    renderFor(PHONE);

    fireEvent.click(button("Edit prompt"));
    expect(promptField()).toHaveValue(PROMPT);
    expect(button("Save")).toBeDisabled();

    fireEvent.change(promptField(), {
      target: { value: "x".repeat(PROMPT_MAX_CHARS + 1) },
    });
    expect(button("Save")).toBeDisabled();
    fireEvent.change(promptField(), { target: { value: "  " } });
    expect(button("Save")).toBeDisabled();

    fireEvent.change(promptField(), { target: { value: "Be brief. " } });
    fireEvent.click(button("Save"));

    expect(mutations.create.calls).toEqual([{ prompt: "Be brief." }]);
    expect(
      screen.getByText("New version v8 is being checked."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Prompt")).toBeNull();
  });

  it("sends a new phone number as the agent's settings", () => {
    renderFor(PHONE);
    fireEvent.click(button("Edit prompt"));

    fireEvent.change(screen.getByLabelText("Phone number"), {
      target: { value: "+14155550199" },
    });
    fireEvent.click(button("Save"));

    expect(mutations.create.calls).toEqual([
      { agent_payload: { config: { phone_number: "+14155550199" } } },
    ]);
  });

  it("explains a refused change and keeps the editor open", () => {
    mutations.create.answer = {
      error: { statusCode: 422, error: "version_not_runnable" },
    };
    renderFor(PHONE);
    fireEvent.click(button("Edit prompt"));
    fireEvent.change(promptField(), { target: { value: "New prompt" } });
    fireEvent.click(button("Save"));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "A changed agent can't run yet: preparing new combinations is turned off.",
    );
    expect(promptField()).toBeInTheDocument();

    fireEvent.click(button("Cancel"));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("labels a provider assistant's prompt as evaluation instructions", () => {
    renderFor({
      source: { kind: "provider", provider: "vapi", assistant_id: "asst-1" },
      prompt: "Judge this.",
    });

    expect(
      screen.getByText(
        "Your vapi assistant keeps its own prompt; this only changes how runs are judged.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(button("Instructions used for evaluation"));
    expect(
      screen.getByLabelText("Instructions used for evaluation"),
    ).toHaveValue("Judge this.");
    expect(screen.queryByLabelText("Phone number")).toBeNull();
  });

  it.each([
    ["no default", null],
    ["no source", { source: null }],
    ["a remote target", { source: { kind: "provider", provider: "remote" } }],
  ])("renders nothing for %s", (_, defaultVersion) => {
    const { container } = renderFor(defaultVersion);
    expect(container).toBeEmptyDOMElement();
  });
});
