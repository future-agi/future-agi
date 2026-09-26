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
  credentials: {
    scanned_files: 0,
    detected_connectors: ["vapi"],
    requirements: [{ environment_name: "VAPI_API_KEY", purpose: "target_provider", required: true, status: "configured" }],
    credential_choices: [],
    probe: [{ provider: "vapi_target", label: "Vapi agent", aliases: ["VAPI_API_KEY"], ok: true, message: "Agent found" }],
  },
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
    // 3 coming-soon agent types (Code / Computer use / Robotics) + the 3
    // coming-soon voice platforms (Bland, ElevenLabs, LiveKit — none of them a
    // live hosted connector yet, so a submit would 400).
    expect(screen.getAllByLabelText("Coming soon")).toHaveLength(6);
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

    // Chat defaults to Retell (the only chat connector); OpenAI Assistants is a
    // coming-soon chip. Switching agent type clears the id/key.
    expect(screen.getByText("OpenAI Assistants")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("agent_9f2c…").value).toBe("");
    expect(screen.getByPlaceholderText("sk-…").value).toBe("");
  });

  it("shows the voice contact block (Web/Phone + Agent speaks first) only for voice", () => {
    render(<PanelHostedPlatform />);
    expect(screen.getByText("Web simulation (WebRTC)")).toBeInTheDocument();
    expect(screen.getByText("Agent speaks first")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Chat"));
    expect(screen.queryByText("Agent speaks first")).toBeNull();
    expect(screen.queryByText("Web simulation (WebRTC)")).toBeNull();
  });

  it("switches the voice sim to Phone and reveals Country Code + Contact Number + Inbound Calls", () => {
    render(<PanelHostedPlatform />);
    expect(screen.queryByText("Country Code")).toBeNull();
    fireEvent.click(screen.getByText("Phone"));
    expect(screen.getByText("Telephony simulation (PSTN)")).toBeInTheDocument();
    expect(screen.getByText("Country Code")).toBeInTheDocument();
    expect(screen.getByText("Contact Number")).toBeInTheDocument();
    expect(screen.getByText("Inbound Calls")).toBeInTheDocument();
  });

  it("selects Others → System prompt instead of ID/key, phone-only contact, gated on the prompt", () => {
    render(<PanelHostedPlatform />);
    fireEvent.click(screen.getByText("Others"));
    expect(screen.getByText("System prompt")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("asst_9f2c…")).toBeNull();
    // Others has no WebRTC path — the Web/Phone header is hidden and the number
    // is required.
    expect(screen.queryByText("Web simulation (WebRTC)")).toBeNull();
    expect(screen.getByText("Contact Number")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeDisabled();
  });

  it("Others: a letters-only contact number never unlocks preflight", () => {
    render(<PanelHostedPlatform />);
    fireEvent.click(screen.getByText("Others"));
    fireEvent.change(screen.getByPlaceholderText("You are a friendly returns agent for Acme…"), {
      target: { value: "You are a returns agent." },
    });
    const number = screen.getByPlaceholderText("Number to call for the simulation");

    fireEvent.change(number, { target: { value: "abc e" } });
    expect(number).toHaveValue("");
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeDisabled();

    fireEvent.change(number, { target: { value: "9258565747" } });
    expect(number).toHaveValue("9258565747");
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeEnabled();
  });

  it("gates both actions until both credential fields are filled", () => {
    render(<PanelHostedPlatform />);
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeDisabled();
    expect(buildBtn()).toBeDisabled();
    expect(screen.getByText("Run preflight to check your setup before building")).toBeInTheDocument();

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
