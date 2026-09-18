import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import { INSTALLATION_ID_LABEL } from "../repoProviders";

// usePanelBuild navigates on a passing Build; spy it and keep the rest real.
const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

// The panel runs the real preflight inline. Repo drafts carry no plaintext
// secrets, so storeHarnessSecretValues is never hit — but the environments tree
// (via EnvironmentValues) imports the module, so provide the whole surface.
vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
}));

const { preflightHarnessJob } = await import("src/api/harness/harness");
const { default: PanelSourceRepo } = await import("../panels/PanelSourceRepo");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../store/useEnvironmentsStore"
);

const PASS = {
  ready_to_submit: true,
  state: "connected",
  checks: [
    { id: "source", label: "Source", status: "passed", detail: "Repo reachable", missing: [], fix: null },
    { id: "credentials_present", label: "Credentials present", status: "passed", detail: "", missing: [], fix: null },
  ],
};

const FAIL = {
  ready_to_submit: false,
  state: "failed",
  checks: [
    { id: "source", label: "Source", status: "passed", detail: "Repo reachable", missing: [], fix: null },
    {
      id: "credentials_present",
      label: "Credentials present",
      status: "failed",
      detail: "No secret refs found",
      missing: ["VAPI_API_KEY"],
      fix: "Add VAPI_API_KEY to the environment values.",
    },
  ],
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
  fireEvent.change(screen.getByPlaceholderText("owner/repo"), { target: { value } });

const runPreflight = () =>
  fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));

const buildBtn = () => screen.getByRole("button", { name: /Build environment/ });

beforeEach(() => {
  resetEnvironmentsStore();
  navigate.mockReset();
  preflightHarnessJob.mockReset();
});

describe("PanelSourceRepo", () => {
  it("renders three provider chips, two coming soon", () => {
    renderPanel();
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    expect(screen.getByText("GitLab")).toBeInTheDocument();
    expect(screen.getByText("Bitbucket")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Coming soon")).toHaveLength(2);
  });

  it("keeps GitHub selected when a coming-soon provider is clicked", async () => {
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
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeDisabled();
    expect(buildBtn()).toBeDisabled();
    expect(screen.getByText("Run preflight to continue")).toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeDisabled();
  });

  it("runs preflight, renders the passing checks, and enables Build", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    renderPanel();
    typeRepo("owner/repo");
    runPreflight();

    expect(await screen.findByText("Ready to build")).toBeInTheDocument();
    // The check rows are rendered from the real response.
    expect(screen.getByText("Credentials present")).toBeInTheDocument();
    await waitFor(() => expect(buildBtn()).toBeEnabled());
  });

  it("renders a failing preflight with the fix block and keeps Build disabled", async () => {
    preflightHarnessJob.mockResolvedValue(FAIL);
    renderPanel();
    typeRepo("owner/repo");
    runPreflight();

    expect(await screen.findByText("1 check to resolve")).toBeInTheDocument();
    expect(screen.getByText("VAPI_API_KEY")).toBeInTheDocument();
    expect(
      screen.getByText(/Add VAPI_API_KEY to the environment values/),
    ).toBeInTheDocument();
    expect(buildBtn()).toBeDisabled();
  });

  it("stages the redacted draft and navigates when Build is clicked after a pass", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    renderPanel();
    typeRepo("owner/repo");
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());

    fireEvent.click(buildBtn());

    const state = useEnvironmentsStore.getState();
    expect(state.pendingBuild).toMatchObject({
      draft: { kind: "repo", provider: "github", value: "owner/repo", visibility: "public" },
      preflight: PASS,
    });
    // beginBuild mirrors the draft into the persisted slot too.
    expect(state.draft?.kind).toBe("repo");
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/build",
    );
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
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeInTheDocument();
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
    renderPanel();
    typeRepo("owner/repo");
    fireEvent.click(screen.getByText("Private"));
    fireEvent.change(screen.getByPlaceholderText("12345678"), { target: { value: "42" } });
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());

    expect(useEnvironmentsStore.getState().pendingBuild.draft).toMatchObject({
      visibility: "private",
      installationId: "42",
    });
  });
});
