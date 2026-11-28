import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, renderWithRouter, screen, waitFor, within } from "src/utils/test-utils";
import ConnectAgentPage from "../ConnectAgentPage";

const mockNavigate = vi.fn();
const mockGitHubConnection = vi.fn();
const mockRepositories = vi.fn();
const mockBranches = vi.fn();
const mockStartScan = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("src/api/integrations", () => ({
  useGitHubConnection: (...args) => mockGitHubConnection(...args),
  useSourceRepositories: (...args) => mockRepositories(...args),
  useSourceBranches: (...args) => mockBranches(...args),
}));

vi.mock("src/api/agents/agentConnect", () => ({
  useStartScan: () => ({ mutate: mockStartScan, isPending: false }),
  useAgentScan: () => ({ data: null, isLoading: false, isError: false }),
  useConnectAgent: () => ({ mutate: vi.fn(), isPending: false }),
}));

const mockUpload = vi.hoisted(() => vi.fn());
vi.mock("src/pages/dashboard/harness/sourceUpload", async (importOriginal) => ({
  ...(await importOriginal()),
  uploadSourceFolder: (...args) => mockUpload(...args),
}));

vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));

vi.mock("../useConnectPreflight", () => ({
  default: () => ({
    ready: true,
    status: "idle",
    result: undefined,
    error: null,
    run: vi.fn(),
    reset: vi.fn(),
  }),
}));

const auth = vi.hoisted(() => ({ role: "Member" }));
vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: auth.role }),
}));

const CONNECTION = {
  id: "connection-1",
  status: "active",
  display_name: "acme",
  external_project_name: "4242",
};
const REPOSITORY = {
  id: 7,
  full_name: "acme/support-agent",
  default_branch: "trunk",
  private: true,
};

const renderPage = (route = "/dashboard/agents/connect") =>
  renderWithRouter(
    <QueryClientProvider client={new QueryClient()}>
      <HelmetProvider>
        <Routes>
          <Route path="/dashboard/agents/connect" element={<ConnectAgentPage />} />
        </Routes>
      </HelmetProvider>
    </QueryClientProvider>,
    { route },
  );

const row = (name) => screen.getByRole("button", { name: `Connect ${name}` });

describe("ConnectAgentPage", () => {
  beforeEach(() => {
    auth.role = "Member";
    mockNavigate.mockReset();
    mockStartScan.mockReset();
    mockGitHubConnection.mockReturnValue({ data: null, isLoading: false, isError: false });
    mockRepositories.mockReturnValue({ data: [], isFetching: false, isError: false });
    mockBranches.mockReturnValue({ data: [], isFetching: false });
  });

  it("opens on Code: GitHub, the SDK rows coming soon, and the folder drop", () => {
    renderPage();

    expect(screen.getByText("From a repository")).toBeInTheDocument();
    expect(row("GitHub")).toBeEnabled();
    expect(within(row("GitHub")).getByText("Install the GitHub app, then pick the repository")).toBeInTheDocument();
    for (const name of ["GitLab", "Bitbucket", "OpenAI, Anthropic or Gemini calls"]) {
      expect(screen.queryByText(name)).toBeNull();
    }
    expect(screen.getByRole("button", { name: /^LangGraph/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^CrewAI/ })).toBeDisabled();
    expect(screen.getAllByLabelText("Coming soon", { selector: "span" })).toHaveLength(17);
    expect(screen.getByText("Choose a folder or drag & drop it here")).toBeInTheDocument();
    expect(screen.queryByText(/No agent yet/)).toBeNull();
  });

  it("uploads a dropped folder on the page and opens on the agents in it", async () => {
    sessionStorage.clear();
    mockUpload.mockResolvedValue({ source_id: "src-1" });
    mockStartScan.mockImplementation((_source, { onSuccess }) => onSuccess({ id: "scan-1" }));
    const { container } = renderPage();
    const file = new File(["x"], "main.py");
    Object.defineProperty(file, "webkitRelativePath", { value: "agent/main.py" });

    fireEvent.change(container.querySelector('input[type="file"][webkitdirectory]'), {
      target: { files: [file] },
    });

    expect(await screen.findByRole("button", { name: "Next" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Find agents" })).toBeNull();
    await waitFor(() =>
      expect(document.querySelectorAll('input[type="file"][webkitdirectory]')).toHaveLength(0),
    );
  });

  it("connects Vapi and Retell AI as hosted platforms, nothing coming soon", () => {
    renderPage();

    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));

    expect(row("Vapi")).toBeEnabled();
    expect(row("Retell AI")).toBeEnabled();
    expect(screen.queryByText("ElevenLabs")).toBeNull();
    expect(screen.queryByText("Coming soon")).toBeNull();
  });

  it("connects a LiveKit project or a phone number under other ways", () => {
    renderPage();

    fireEvent.click(screen.getByRole("tab", { name: /Other ways/ }));

    expect(row("LiveKit project")).toBeEnabled();
    expect(row("Phone number")).toBeEnabled();
    expect(screen.queryByText("Coming soon")).toBeNull();
  });

  it("opens a hosted platform's form in the drawer", () => {
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));

    fireEvent.click(row("Vapi"));

    expect(screen.getByRole("textbox", { name: "Assistant ID" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect agent" })).toBeDisabled();
  });

  it("searches every way in and says when nothing matches", () => {
    renderPage();
    const search = screen.getByRole("textbox", { name: "Search ways to connect" });

    fireEvent.change(search, { target: { value: "voice" } });
    expect(row("Vapi")).toBeInTheDocument();
    expect(row("Phone number")).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "zzz" } });
    expect(screen.getByText(/No way to connect matches/)).toBeInTheDocument();
  });

  it("asks to install the GitHub App and comes back here afterwards", () => {
    renderPage();
    fireEvent.click(row("GitHub"));

    fireEvent.click(screen.getByRole("button", { name: "Install GitHub App" }));

    const target = new URL(mockNavigate.mock.calls[0][0], "https://app.example.com");
    expect(target.pathname).toBe("/dashboard/settings/integrations");
    expect(target.searchParams.get("platform")).toBe("github");
    expect(target.searchParams.get("return_to")).toBe("/dashboard/agents/connect?source=github");
  });

  it("won't let a viewer install the GitHub App", async () => {
    auth.role = "Viewer";
    renderPage();
    fireEvent.click(row("GitHub"));

    const install = screen.getByRole("button", { name: "Install GitHub App" });
    expect(install).toBeDisabled();
    fireEvent.mouseOver(install.parentElement);
    expect(
      await screen.findByText("You don't have permission to connect GitHub."),
    ).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("says why when the install waits for an organization owner", () => {
    renderPage("/dashboard/agents/connect?source=github&github=requested");

    expect(screen.getByText(/has to approve the install/)).toBeInTheDocument();
  });

  it("does not list repositories while the app is suspended", () => {
    mockGitHubConnection.mockReturnValue({
      data: { ...CONNECTION, status: "paused" },
      isLoading: false,
      isError: false,
    });

    renderPage("/dashboard/agents/connect?source=github");

    expect(screen.getByText(/suspended on GitHub/)).toBeInTheDocument();
    expect(mockRepositories).not.toHaveBeenCalled();
  });

  it("picks a repository on its default branch and finds the agents in it", () => {
    mockGitHubConnection.mockReturnValue({ data: CONNECTION, isLoading: false, isError: false });
    mockRepositories.mockReturnValue({ data: [REPOSITORY], isFetching: false, isError: false });
    mockBranches.mockReturnValue({
      data: [
        { name: "trunk", is_default: true },
        { name: "feature", is_default: false },
      ],
      isFetching: false,
    });
    renderPage("/dashboard/agents/connect?source=github");

    fireEvent.mouseDown(screen.getByPlaceholderText("Choose a repository"));
    fireEvent.click(screen.getByText("acme/support-agent"));

    expect(mockBranches).toHaveBeenLastCalledWith("connection-1", 7);
    expect(screen.getByText("trunk")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Find agents" }));

    expect(mockStartScan).toHaveBeenCalledWith(
      {
        kind: "github",
        repository: "acme/support-agent",
        ref: "trunk",
        visibility: "private",
        installation_id: "4242",
        repository_id: 7,
      },
      expect.any(Object),
    );
  });

  it("takes a public repository link instead of the app", () => {
    renderPage("/dashboard/agents/connect?source=github");

    fireEvent.click(screen.getByRole("radio", { name: /Paste a repo link/ }));
    fireEvent.change(screen.getByPlaceholderText("https://github.com/owner/repo"), {
      target: { value: "not a repo" },
    });

    expect(screen.getByText("Enter a GitHub link or owner/repo.")).toBeInTheDocument();
    expect(screen.getByText("Paste the repository link")).toBeInTheDocument();
  });
});
