import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as baseRender, screen, fireEvent, waitFor } from "src/utils/test-utils";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

// A hosted platform's API key is exchanged for a secret ref before preflight, so
// storeHarnessSecretValues is part of the flow here (unlike the repo panel).
vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
}));

const { preflightHarnessJob, storeHarnessSecretValues, createHarnessJob } = await import(
  "src/api/harness/harness"
);
const { default: PanelHostedPlatform } = await import("../panels/PanelHostedPlatform");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../store/useEnvironmentsStore"
);

const PASS = {
  ready_to_submit: true,
  state: "connected",
  checks: [
    { id: "provider_target", label: "Provider target", status: "passed", detail: "vapi reachable", missing: [], fix: null },
  ],
};

const render = (ui) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return baseRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
};

const fillCreds = () => {
  fireEvent.change(screen.getByPlaceholderText("asst_9f2c…"), { target: { value: "asst_x" } });
  fireEvent.change(screen.getByPlaceholderText("sk-…"), { target: { value: "sk-x" } });
};

const runPreflight = () => fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
const buildBtn = () => screen.getByRole("button", { name: /Build environment/ });

beforeEach(() => {
  resetEnvironmentsStore();
  navigate.mockReset();
  preflightHarnessJob.mockReset();
  storeHarnessSecretValues.mockReset();
  storeHarnessSecretValues.mockResolvedValue({ secret_refs: { VAPI_API_KEY: "ref-1" } });
});

describe("PanelHostedPlatform", () => {
  it("renders five agent-type chips, three coming soon", () => {
    render(<PanelHostedPlatform />);
    expect(screen.getByText("Voice")).toBeInTheDocument();
    expect(screen.getByText("Chat")).toBeInTheDocument();
    expect(screen.getByText("Computer use")).toBeInTheDocument();
    expect(screen.getByText("Robotics")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Coming soon")).toHaveLength(3);
  });

  it("keeps Voice selected when a coming-soon type is clicked", () => {
    render(<PanelHostedPlatform />);
    fireEvent.click(screen.getByText("Code"));
    expect(screen.getByLabelText("Vapi")).toBeInTheDocument();
  });

  it("shows the voice roster with wordmarks and named marks", () => {
    render(<PanelHostedPlatform />);
    expect(screen.queryByText("Vapi")).toBeNull();
    expect(screen.queryByText("Retell AI")).toBeNull();
    expect(screen.getByLabelText("Vapi")).toBeInTheDocument();
    expect(screen.getByLabelText("Retell AI")).toBeInTheDocument();
    expect(screen.getByText("Bland.ai")).toBeInTheDocument();
    expect(screen.getByText("ElevenLabs")).toBeInTheDocument();
    expect(screen.getByText("LiveKit")).toBeInTheDocument();
  });

  it("switches to the chat roster and clears the id/key fields", () => {
    render(<PanelHostedPlatform />);
    fireEvent.change(screen.getByPlaceholderText("asst_9f2c…"), { target: { value: "asst_x" } });
    fireEvent.change(screen.getByPlaceholderText("sk-…"), { target: { value: "sk-x" } });
    fireEvent.click(screen.getByText("Chat"));

    expect(screen.getByText("OpenAI Assistants")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("asst_9f2c…").value).toBe("");
    expect(screen.getByPlaceholderText("sk-…").value).toBe("");
  });

  it("only shows call direction for voice", () => {
    render(<PanelHostedPlatform />);
    expect(screen.getByText("Call direction")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Chat"));
    expect(screen.queryByText("Call direction")).toBeNull();
  });

  it("gates both actions until both credential fields are filled", () => {
    render(<PanelHostedPlatform />);
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeDisabled();
    expect(buildBtn()).toBeDisabled();
    expect(screen.getByText("Run preflight to continue")).toBeInTheDocument();

    fillCreds();
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeEnabled();
    expect(buildBtn()).toBeDisabled();
  });

  it("exchanges the key, runs preflight, and stages a redacted draft on Build", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    render(<PanelHostedPlatform />);
    fillCreds();
    runPreflight();

    // The plaintext key is exchanged for a secret ref before the POST.
    await waitFor(() =>
      expect(storeHarnessSecretValues).toHaveBeenCalledWith({ VAPI_API_KEY: "sk-x" }),
    );
    expect(await screen.findByText("Ready to build")).toBeInTheDocument();
    // The preflight body carries the exchanged ref for credentials_present AND
    // the raw value as write-only credential_values for the live probe.
    expect(preflightHarnessJob).toHaveBeenCalledWith(
      expect.objectContaining({
        agent: expect.objectContaining({
          connector: "vapi",
          secret_refs: { VAPI_API_KEY: "ref-1" },
        }),
        credential_values: { VAPI_API_KEY: "sk-x" },
      }),
    );

    createHarnessJob.mockResolvedValue({ job: { job_id: "job-plat" } });
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("/dashboard/simulate/environments/job-plat"),
    );
    const staged = useEnvironmentsStore.getState().draft;
    expect(staged).toMatchObject({
      kind: "platform",
      provider: "vapi",
      agentId: "asst_x",
      callDirection: "inbound",
      secret_refs: { VAPI_API_KEY: "ref-1" },
    });
    // The raw key is redacted out of the staged draft.
    expect(staged.apiKey).toBeUndefined();
  });

  it("discards an in-flight exchange when a credential is edited before it resolves", async () => {
    // Hold the secret exchange open so we can edit the key mid-flight.
    let resolveExchange;
    storeHarnessSecretValues.mockReturnValue(
      new Promise((res) => {
        resolveExchange = res;
      }),
    );
    preflightHarnessJob.mockResolvedValue(PASS);
    render(<PanelHostedPlatform />);
    fillCreds();
    runPreflight();

    // The trigger is disabled through the exchange (no double-exchange).
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Checking source and credentials/ }),
      ).toBeDisabled(),
    );

    // Edit the key while the exchange is still open, then let it resolve.
    fireEvent.change(screen.getByPlaceholderText("sk-…"), { target: { value: "sk-changed" } });
    resolveExchange({ secret_refs: { VAPI_API_KEY: "ref-stale" } });

    // The superseded exchange must never reach preflight, and Build stays gated.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Run preflight" })).toBeInTheDocument(),
    );
    expect(preflightHarnessJob).not.toHaveBeenCalled();
    expect(buildBtn()).toBeDisabled();
  });

  it("resets a passing preflight when a credential is edited", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    render(<PanelHostedPlatform />);
    fillCreds();
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());

    fireEvent.change(screen.getByPlaceholderText("sk-…"), { target: { value: "sk-y" } });
    expect(buildBtn()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeInTheDocument();
  });
});
