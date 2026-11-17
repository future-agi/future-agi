import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import { INSTALLATION_ID_LABEL } from "../repoProviders";

// Word for word the backend's PRIVATE_GITHUB_UNAVAILABLE_MESSAGE in
// simulate/services/github_app_tenancy.py; literal, so a wording drift fails.
const PRIVATE_GITHUB_UNAVAILABLE_MESSAGE =
  "Private GitHub repositories are not available on Future AGI cloud. Use a public repository or upload the code.";

// The backend's answer (harness health `private_github_available`). Undefined
// is the loading, error or older-server state, which keeps today's form.
const privateGithub = vi.hoisted(() => ({ available: undefined }));
vi.mock("../hooks/usePrivateGithubAvailable", () => ({
  default: () => privateGithub.available,
}));

// usePanelBuild navigates on a passing Build; spy it and keep the rest real.
const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const { enqueueSnackbar } = vi.hoisted(() => ({ enqueueSnackbar: vi.fn() }));
vi.mock("notistack", async () => ({
  ...(await vi.importActual("notistack")),
  enqueueSnackbar,
}));

// The panel runs the real preflight inline. The environments tree (via
// EnvironmentValues) imports the module, so provide the whole surface.
vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
}));

const { preflightHarnessJob, createHarnessJob } = await import(
  "src/api/harness/harness"
);
const { default: PanelSourceRepo } = await import("../panels/PanelSourceRepo");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../store/useEnvironmentsStore"
);

const PASS = {
  ready_to_submit: true,
  credentials: {
    scanned_files: 12,
    detected_connectors: [],
    requirements: [],
    credential_choices: [],
    probe: [],
  },
};

const FAIL = {
  ready_to_submit: false,
  credentials: {
    scanned_files: 12,
    detected_connectors: ["vapi"],
    requirements: [
      {
        environment_name: "VAPI_API_KEY",
        purpose: "target_provider",
        required: true,
        status: "missing",
      },
    ],
    credential_choices: [],
    probe: [],
  },
};

const renderPanel = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PanelSourceRepo />
    </QueryClientProvider>,
  );
};

const typeRepo = (value) =>
  fireEvent.change(screen.getByPlaceholderText("owner/repo"), {
    target: { value },
  });

const runPreflight = () =>
  fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));

const buildBtn = () =>
  screen.getByRole("button", { name: /Build environment/ });

const chip = (name) => screen.getByRole("button", { name });

const reason = () => screen.queryByText(PRIVATE_GITHUB_UNAVAILABLE_MESSAGE);

beforeEach(() => {
  resetEnvironmentsStore();
  navigate.mockReset();
  preflightHarnessJob.mockReset();
  privateGithub.available = undefined;
});

describe("PanelSourceRepo", () => {
  it("renders three provider chips, two disabled with the line", () => {
    renderPanel();
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    for (const name of ["GitLab", "Bitbucket"]) {
      const chip = screen.getByText(name).closest('[role="button"]');
      expect(chip).toHaveAttribute("aria-disabled", "true");
      expect(
        within(chip).getByText("Not available in this release"),
      ).toBeInTheDocument();
    }
    expect(
      screen.getAllByText("Not available in this release"),
    ).toHaveLength(2);
    expect(screen.queryByText(/coming soon/i)).toBeNull();
  });

  it("keeps GitHub selected when an unreleased provider is clicked", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    renderPanel();
    fireEvent.click(screen.getByText("GitLab"));
    typeRepo("owner/repo");
    runPreflight();

    await screen.findByText("Ready to build");
    expect(preflightHarnessJob).toHaveBeenCalledWith(
      expect.objectContaining({
        source: expect.objectContaining({ repository: "owner/repo" }),
      }),
    );
  });

  it("gates both actions until a valid repository is entered", () => {
    renderPanel();
    // Run preflight is disabled with nothing typed; Build is disabled with a hint.
    expect(
      screen.getByRole("button", { name: "Run preflight" }),
    ).toBeDisabled();
    expect(buildBtn()).toBeDisabled();
    expect(
      screen.getByText("Run preflight to check your setup before building"),
    ).toBeInTheDocument();
  });

  it("enables Run preflight on a valid repo but keeps Build disabled until it passes", () => {
    renderPanel();
    typeRepo("owner/repo");
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeEnabled();
    expect(buildBtn()).toBeDisabled();
  });

  it("flags an unparseable repository and keeps Run preflight disabled", () => {
    renderPanel();
    typeRepo("asd asd not a repo");
    expect(
      screen.getByText(/Enter a repository as owner\/repo or a GitHub URL/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Run preflight" }),
    ).toBeDisabled();
  });

  it("renders hosted credential readiness and enables Build", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    renderPanel();
    typeRepo("owner/repo");
    runPreflight();

    expect(await screen.findByText("Ready to build")).toBeInTheDocument();
    expect(screen.getByText("12 source files scanned")).toBeInTheDocument();
    await waitFor(() => expect(buildBtn()).toBeEnabled());
  });

  it("shows missing credentials and keeps Build disabled", async () => {
    preflightHarnessJob.mockResolvedValue(FAIL);
    renderPanel();
    typeRepo("owner/repo");
    runPreflight();

    expect(
      await screen.findByText("Credentials need attention"),
    ).toBeInTheDocument();
    expect(screen.getByText("VAPI_API_KEY")).toBeInTheDocument();
    expect(screen.getByText("missing")).toBeInTheDocument();
    expect(buildBtn()).toBeDisabled();
  });

  it("creates the job and navigates to its workspace when Build is clicked after a pass", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    createHarnessJob.mockResolvedValue({ job: { job_id: "job-123" } });
    renderPanel();
    typeRepo("owner/repo");
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());

    fireEvent.click(buildBtn());

    // The create builds the passing draft and we route straight to the workspace.
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(
        "/dashboard/simulate/environments/job-123",
      ),
    );
    expect(createHarnessJob).toHaveBeenCalledWith(
      expect.objectContaining({
        source: expect.objectContaining({
          kind: "github",
          repository: "owner/repo",
        }),
      }),
      "idem-test",
    );
    // The passing draft is kept in the persisted slot for form rehydrate.
    expect(useEnvironmentsStore.getState().draft).toMatchObject({
      kind: "repo",
      provider: "github",
      value: "owner/repo",
      visibility: "public",
    });
  });

  it("tags the kept draft with the environment it built, for Start a new build", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    createHarnessJob.mockResolvedValue({ job: { job_id: "job-123" } });
    renderPanel();
    typeRepo("owner/repo");
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());

    fireEvent.click(buildBtn());

    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(useEnvironmentsStore.getState().draft).toMatchObject({
      kind: "repo",
      value: "owner/repo",
      envId: "job-123",
    });
  });

  it("starts from the kept draft's non-secret fields", () => {
    useEnvironmentsStore.getState().setDraft({
      kind: "repo",
      provider: "github",
      value: "acme/support-bot",
      ref: "release",
      entry: "src/agent.py",
      visibility: "private",
      installationId: "4242",
      egress: "api.acme.test",
      scenarioCount: 12,
      envId: "job-failed",
    });
    renderPanel();

    expect(screen.getByPlaceholderText("owner/repo")).toHaveValue(
      "acme/support-bot",
    );
    expect(screen.getByDisplayValue("release")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("src/agent/index.ts")).toHaveValue(
      "src/agent.py",
    );
    // Private stays picked, so the installation id shows with its value.
    expect(screen.getByText(INSTALLATION_ID_LABEL)).toBeInTheDocument();
    expect(screen.getByDisplayValue("4242")).toBeInTheDocument();
    // A filled-in form still has to pass preflight before it can build.
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeEnabled();
    expect(buildBtn()).toBeDisabled();
  });

  it("shows why a private repository was refused at Build", async () => {
    const message =
      "Private GitHub repositories can't be versioned yet; use a public repository or upload the folder";
    preflightHarnessJob.mockResolvedValue(PASS);
    createHarnessJob.mockRejectedValue({
      statusCode: 422,
      error: "github_private_unsupported",
      message,
    });
    enqueueSnackbar.mockReset();
    renderPanel();
    typeRepo("owner/repo");
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());

    fireEvent.click(buildBtn());

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(message, {
        variant: "error",
      }),
    );
    expect(navigate).not.toHaveBeenCalled();
    expect(buildBtn()).toBeEnabled();
  });

  it("resets a passing preflight when a field is edited (stale-green guard)", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    renderPanel();
    typeRepo("owner/repo");
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());

    // Editing the repo invalidates the green result — Build re-disables and the
    // trigger returns.
    typeRepo("owner/other-repo");
    expect(buildBtn()).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Run preflight" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Ready to build")).not.toBeInTheDocument();
  });

  it("defaults to Public and hides the installation-id field", () => {
    renderPanel();
    expect(screen.queryByText(INSTALLATION_ID_LABEL)).toBeNull();
  });

  it("reveals the installation-id field when Private is chosen", () => {
    renderPanel();
    fireEvent.click(screen.getByText("Private"));
    expect(screen.getByText(INSTALLATION_ID_LABEL)).toBeInTheDocument();
  });

  it("carries visibility and installation id into the staged draft when Private", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    createHarnessJob.mockResolvedValue({ job: { job_id: "job-priv" } });
    renderPanel();
    typeRepo("owner/repo");
    fireEvent.click(screen.getByText("Private"));
    fireEvent.change(screen.getByPlaceholderText("12345678"), {
      target: { value: "42" },
    });
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());

    // setDraft runs synchronously on click, before the create resolves.
    expect(useEnvironmentsStore.getState().draft).toMatchObject({
      visibility: "private",
      installationId: "42",
    });
  });

  describe("when the deployment refuses private GitHub sources", () => {
    beforeEach(() => {
      privateGithub.available = false;
    });

    it("withdraws Private with the backend's reason and no installation-id field", () => {
      renderPanel();
      expect(reason()).toBeInTheDocument();
      const privateChip = chip("Private");
      expect(privateChip.closest("[aria-disabled='true']")).not.toBeNull();

      fireEvent.click(privateChip);

      expect(screen.queryByText(INSTALLATION_ID_LABEL)).toBeNull();
      expect(screen.queryByPlaceholderText("12345678")).toBeNull();
      expect(chip("Public")).toHaveAttribute("aria-pressed", "true");
    });

    it("switches a kept Private draft to Public and keeps the reason visible", async () => {
      useEnvironmentsStore.getState().setDraft({
        kind: "repo",
        provider: "github",
        value: "acme/support-bot",
        visibility: "private",
        installationId: "4242",
      });
      preflightHarnessJob.mockResolvedValue(PASS);
      renderPanel();

      expect(screen.queryByText(INSTALLATION_ID_LABEL)).toBeNull();
      expect(screen.queryByDisplayValue("4242")).toBeNull();
      expect(chip("Public")).toHaveAttribute("aria-pressed", "true");
      expect(reason()).toBeInTheDocument();

      // The preflight request names a public source with no installation.
      runPreflight();
      await waitFor(() => expect(preflightHarnessJob).toHaveBeenCalled());
      const { source } = preflightHarnessJob.mock.calls[0][0];
      expect(source.visibility).toBe("public");
      expect("installation_id" in source).toBe(false);
    });
  });

  describe("when private GitHub is available or the answer is unknown", () => {
    it.each([
      ["available", true],
      ["unknown", undefined],
    ])("keeps Private and its installation-id field (%s)", (_label, value) => {
      privateGithub.available = value;
      renderPanel();
      expect(reason()).toBeNull();
      const privateChip = chip("Private");
      expect(privateChip.closest("[aria-disabled='true']")).toBeNull();

      fireEvent.click(privateChip);

      expect(screen.getByText(INSTALLATION_ID_LABEL)).toBeInTheDocument();
    });
  });
});
