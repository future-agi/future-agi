import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import {
  mutations,
  resetMutations,
  useFakeMutation,
} from "./agentVersionsTestUtils";

const { privateGithubAvailable } = vi.hoisted(() => ({
  privateGithubAvailable: vi.fn(() => undefined),
}));

vi.mock("src/api/simulate-environments/versions", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateAgentVersion: vi.fn(() => useFakeMutation("create")),
}));
// The deployment's answer to "are private GitHub sources offered here?":
// undefined (not known yet) unless a test says otherwise.
vi.mock("../../../hooks/usePrivateGithubAvailable", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => privateGithubAvailable(),
}));
vi.mock("src/api/harness/harness", async (importOriginal) => ({
  ...(await importOriginal()),
  uploadHarnessSource: vi.fn(),
}));
vi.mock("src/pages/dashboard/harness/sourceUpload", async (importOriginal) => ({
  ...(await importOriginal()),
  prepareSourceFolder: vi.fn(),
}));

const { uploadHarnessSource } = await import("src/api/harness/harness");
const { prepareSourceFolder } = await import(
  "src/pages/dashboard/harness/sourceUpload"
);
const { default: AgentSourceChange } = await import("../AgentSourceChange");

const SENTENCE =
  "Private GitHub repositories are not available on Future AGI cloud. Upload the code to keep using this environment.";
const NOW = new Date("2026-11-11T10:00:00.000Z");
const RUNNING = [
  {
    id: "a9",
    preflightStatus: "pending",
    requestedHere: true,
    createdAt: "2026-11-11T09:58:00.000Z",
  },
];

const github = (visibility) => ({
  source: {
    kind: "github",
    repository: "acme/agent",
    ref: "main",
    visibility,
  },
});
const renderFor = (defaultVersion, versions) =>
  render(
    <AgentSourceChange
      envId="env-1"
      defaultVersion={defaultVersion}
      versions={versions}
    />,
  );
const uploadButton = () =>
  screen.queryByText("Upload new source")?.closest(".MuiButton-root") ?? null;

beforeEach(() => {
  resetMutations();
  uploadHarnessSource.mockReset();
  prepareSourceFolder.mockReset();
  privateGithubAvailable.mockReset();
  privateGithubAvailable.mockReturnValue(undefined);
});
afterEach(() => {
  vi.useRealTimers();
});

describe("AgentSourceChange on a private repository the deployment no longer reads", () => {
  it("offers the upload under the one sentence that says so", () => {
    privateGithubAvailable.mockReturnValue(false);
    renderFor(github("private"));

    const banner = screen.getByTestId("private-github-unavailable");
    expect(within(banner).getByText(SENTENCE)).toBeVisible();
    const control = within(banner)
      .getByText("Upload new source")
      .closest(".MuiButton-root");
    expect(control).not.toHaveClass("Mui-disabled");
    expect(screen.getAllByText(SENTENCE)).toHaveLength(1);
    expect(screen.getAllByText("Upload new source")).toHaveLength(1);
    expect(
      screen.getByText("Built from acme/agent @ main"),
    ).toBeInTheDocument();
  });

  it("makes the next version from the picked folder, as an uploaded source does", async () => {
    privateGithubAvailable.mockReturnValue(false);
    mutations.create.data = { agent_version: { id: "a8", label: "v8" } };
    const file = new File(["print()"], "main.py");
    prepareSourceFolder.mockReturnValue({
      files: [file],
      paths: ["main.py"],
      name: "agent",
    });
    uploadHarnessSource.mockResolvedValue({ source_id: "src-9" });
    const { container } = renderFor(github("private"));

    fireEvent.change(container.querySelector("input[type=file]"), {
      target: { files: [file] },
    });

    await waitFor(() =>
      expect(mutations.create.calls).toEqual([
        { archive_artifact_id: "src-9" },
      ]),
    );
    expect(uploadHarnessSource).toHaveBeenCalledTimes(1);
    expect(
      screen.getByText("New version v8 is being checked."),
    ).toBeInTheDocument();
  });

  it("holds the upload while another change's check runs", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    privateGithubAvailable.mockReturnValue(false);
    renderFor(github("private"), RUNNING);

    expect(uploadButton()).toHaveClass("Mui-disabled");
  });

  it("offers no upload on a public repository", () => {
    privateGithubAvailable.mockReturnValue(false);
    const { container } = renderFor(github("public"));

    expect(screen.getByText("Watching acme/agent @ main")).toBeInTheDocument();
    expect(uploadButton()).toBeNull();
    expect(container.querySelector("input[type=file]")).toBeNull();
  });

  it.each([
    ["offers private repositories", true],
    ["hasn't answered yet", undefined],
  ])(
    "offers no upload on a private repository where the deployment %s",
    (_, available) => {
      privateGithubAvailable.mockReturnValue(available);
      const { container } = renderFor(github("private"));

      expect(uploadButton()).toBeNull();
      expect(container.querySelector("input[type=file]")).toBeNull();
      expect(screen.queryByText(SENTENCE)).toBeNull();
    },
  );

  it("keeps an uploaded source's upload as it was, without the sentence", () => {
    privateGithubAvailable.mockReturnValue(false);
    renderFor({ source: { kind: "archive" } });

    expect(uploadButton()).not.toHaveClass("Mui-disabled");
    expect(screen.queryByText(SENTENCE)).toBeNull();
    expect(privateGithubAvailable).not.toHaveBeenCalled();
  });
});
