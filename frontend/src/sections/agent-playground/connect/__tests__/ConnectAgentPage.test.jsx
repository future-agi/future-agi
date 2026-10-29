import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { fireEvent, renderWithRouter, screen, within } from "src/utils/test-utils";
import ConnectAgentPage from "../ConnectAgentPage";

const mockNavigate = vi.fn();
const mockGitHubConnection = vi.fn();
const mockRepositories = vi.fn();
const mockBranches = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("src/api/integrations", () => ({
  useGitHubConnection: (...args) => mockGitHubConnection(...args),
  useSourceRepositories: (...args) => mockRepositories(...args),
  useSourceBranches: (...args) => mockBranches(...args),
}));

vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));

const CONNECTION = { id: "connection-1", status: "active", display_name: "acme" };
const REPOSITORY = {
  id: 7,
  full_name: "acme/support-agent",
  default_branch: "trunk",
  private: true,
};

const renderPage = (route = "/dashboard/agents/connect") =>
  renderWithRouter(
    <HelmetProvider>
      <Routes>
        <Route path="/dashboard/agents/connect" element={<ConnectAgentPage />} />
      </Routes>
    </HelmetProvider>,
    { route },
  );

const row = (name) => screen.getByRole("button", { name: new RegExp(`^${name}`) });

describe("ConnectAgentPage", () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockGitHubConnection.mockReturnValue({ data: null, isLoading: false, isError: false });
    mockRepositories.mockReturnValue({ data: [], isFetching: false, isError: false });
    mockBranches.mockReturnValue({ data: [], isFetching: false });
  });

  it("opens on Code, where only GitHub can be connected", () => {
    renderPage();

    expect(screen.getByText("From a repository")).toBeInTheDocument();
    expect(row("GitHub")).toBeEnabled();
    expect(within(row("GitHub")).getByText("Install the GitHub app, then pick the repository")).toBeInTheDocument();
    for (const name of ["GitLab", "Bitbucket", "LangGraph", "CrewAI"]) {
      expect(row(name)).toBeDisabled();
    }
    expect(
      screen.getAllByLabelText("Coming soon", { selector: "span" }).length,
    ).toBeGreaterThan(0);
  });

  it("shows hosted platforms as coming soon", () => {
    renderPage();

    fireEvent.click(screen.getByRole("tab", { name: /Hosted platforms/ }));

    expect(row("Vapi")).toBeDisabled();
    expect(within(row("Vapi")).getByText("Coming soon")).toBeInTheDocument();
  });

  it("searches every way in and says when nothing matches", () => {
    renderPage();
    const search = screen.getByRole("textbox", { name: "Search ways to connect" });

    fireEvent.change(search, { target: { value: "repo" } });
    expect(row("GitHub")).toBeInTheDocument();
    expect(row("GitLab")).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "zzz" } });
    expect(screen.getByText('Nothing matches "zzz"')).toBeInTheDocument();
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

  it("picks a repository on its default branch and keeps Connect agent for later", () => {
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
    expect(screen.getByText("Reading the agent and creating it are coming soon")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect agent" })).toBeDisabled();
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
