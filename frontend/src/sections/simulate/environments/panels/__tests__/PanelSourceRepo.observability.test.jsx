import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render as baseRender,
  screen,
  fireEvent,
  waitFor,
} from "src/utils/test-utils";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("src/api/harness/harness", () => ({
  uploadHarnessSource: vi.fn(),
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
}));

const { preflightHarnessJob, createHarnessJob } = await import(
  "src/api/harness/harness"
);
const { default: PanelSourceRepo } = await import("../PanelSourceRepo");
const { resetEnvironmentsStore } = await import(
  "../../store/useEnvironmentsStore"
);

const PASS = {
  ready_to_submit: true,
  credentials: {
    scanned_files: 1,
    detected_connectors: [],
    requirements: [],
    credential_choices: [],
    probe: [],
  },
};

const HELPER =
  "We can observe your agent only if its code already sends telemetry (OpenTelemetry or traceAI). If it doesn't, the Environment still builds, without observability.";

const render = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return baseRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
};

const preflightBtn = () => screen.getByRole("button", { name: "Run preflight" });
const buildBtn = () => screen.getByRole("button", { name: /Build environment/ });
const box = () => screen.getByRole("checkbox", { name: "Turn on observability" });

const typeRepo = () =>
  fireEvent.change(screen.getByPlaceholderText("owner/repo"), {
    target: { value: "acme/support-bot" },
  });

const buildBody = async () => {
  fireEvent.click(preflightBtn());
  await waitFor(() => expect(buildBtn()).toBeEnabled());
  fireEvent.click(buildBtn());
  await waitFor(() => expect(createHarnessJob).toHaveBeenCalledTimes(1));
  return createHarnessJob.mock.calls[0][0];
};

beforeEach(() => {
  resetEnvironmentsStore();
  navigate.mockReset();
  preflightHarnessJob.mockReset();
  createHarnessJob.mockReset();
  preflightHarnessJob.mockResolvedValue(PASS);
  createHarnessJob.mockResolvedValue({ job: { job_id: "job-repo" } });
});

describe("PanelSourceRepo observability choice", () => {
  it("ticked, the create body carries metadata.observability_enabled true", async () => {
    render(<PanelSourceRepo />);
    typeRepo();
    fireEvent.click(box());
    const body = await buildBody();
    expect(body.source.kind).toBe("github");
    expect(body.metadata.observability_enabled).toBe(true);
  });

  it("unticked, the create body has no observability_enabled key", async () => {
    render(<PanelSourceRepo />);
    typeRepo();
    expect(box()).not.toBeChecked();
    const body = await buildBody();
    expect("observability_enabled" in body.metadata).toBe(false);
  });

  it("ticked after a passing preflight, Build is disabled until preflight runs again", async () => {
    render(<PanelSourceRepo />);
    typeRepo();
    fireEvent.click(preflightBtn());
    await waitFor(() => expect(buildBtn()).toBeEnabled());

    fireEvent.click(box());
    expect(buildBtn()).toBeDisabled();

    fireEvent.click(preflightBtn());
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    expect(preflightHarnessJob).toHaveBeenCalledTimes(2);
  });

  it("shows the helper line and lets the box be ticked with no telemetry information at all", () => {
    render(<PanelSourceRepo />);
    expect(screen.getByText(HELPER)).toBeInTheDocument();
    expect(box()).toHaveAccessibleDescription(HELPER);
    expect(box()).toBeEnabled();
    fireEvent.click(box());
    expect(box()).toBeChecked();
  });
});
