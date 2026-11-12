import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as baseRender, screen, fireEvent, waitFor } from "src/utils/test-utils";

const navigate = vi.fn();
let keySeq = 0;
vi.mock("notistack", async () => ({
  ...(await vi.importActual("notistack")),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

// A hosted platform's API key is sent write-only with preflight and create.
vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: vi.fn(() => `idem-${(keySeq += 1)}`),
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
}));

const { preflightHarnessJob, createHarnessJob } = await import(
  "src/api/harness/harness"
);
const { enqueueSnackbar } = await import("notistack");
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
  createHarnessJob.mockReset();
  enqueueSnackbar.mockReset();
});

const createdBody = () => createHarnessJob.mock.calls[0][0];

describe("PanelHostedPlatform", () => {
  it("renders five agent-type chips, three coming soon", () => {
    render(<PanelHostedPlatform />);
    expect(screen.getByText("Voice")).toBeInTheDocument();
    expect(screen.getByText("Chat")).toBeInTheDocument();
    expect(screen.getByText("Computer use")).toBeInTheDocument();
    expect(screen.getByText("Robotics")).toBeInTheDocument();
    // 3 coming-soon agent types (Code / Computer use / Robotics) + the 2
    // coming-soon voice platforms (Bland, ElevenLabs — neither a live hosted
    // connector yet, so a submit would 400).
    expect(screen.getAllByLabelText("Coming soon")).toHaveLength(5);
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

  it("LiveKit asks for name, URL, key, secret and prompt, stays on web, and gates preflight on all five", () => {
    render(<PanelHostedPlatform />);
    fireEvent.click(screen.getByText("LiveKit"));

    expect(screen.queryByText("Web simulation (WebRTC)")).toBeNull();
    expect(screen.queryByText("Phone")).toBeNull();
    expect(screen.getByText("Agent speaks first")).toBeInTheDocument();

    const fields = [
      ["returns-line-agent", "returns-agent"],
      ["wss://your-project.livekit.cloud", "wss://demo.livekit.cloud"],
      ["sk-…", "lk-key"],
      ["secret…", "lk-secret"],
      ["You are a friendly returns agent for Acme…", "You handle returns."],
    ];
    fields.forEach(([placeholder, value]) => {
      expect(screen.getByRole("button", { name: "Run preflight" })).toBeDisabled();
      fireEvent.change(screen.getByPlaceholderText(placeholder), { target: { value } });
    });
    expect(screen.getByRole("button", { name: "Run preflight" })).toBeEnabled();
  });

  it("LiveKit sends key + secret write-only, name/URL/prompt as config, and stages a draft without the secret", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    createHarnessJob.mockResolvedValue({ job: { job_id: "job-lk" } });
    render(<PanelHostedPlatform />);
    fireEvent.click(screen.getByText("LiveKit"));
    fireEvent.change(screen.getByPlaceholderText("returns-line-agent"), {
      target: { value: "returns-agent" },
    });
    fireEvent.change(screen.getByPlaceholderText("wss://your-project.livekit.cloud"), {
      target: { value: "wss://demo.livekit.cloud" },
    });
    fireEvent.change(screen.getByPlaceholderText("sk-…"), { target: { value: "lk-key" } });
    fireEvent.change(screen.getByPlaceholderText("secret…"), { target: { value: "lk-secret" } });
    fireEvent.change(screen.getByPlaceholderText("You are a friendly returns agent for Acme…"), {
      target: { value: "You handle returns." },
    });
    runPreflight();

    await waitFor(() =>
      expect(preflightHarnessJob).toHaveBeenCalledWith(
        expect.objectContaining({
          agent: expect.objectContaining({
            connector: "livekit",
            mode: "connect_only",
            config: {
              agent_name: "returns-agent",
              livekit_url: "wss://demo.livekit.cloud",
              target_system_prompt: "You handle returns.",
              inbound: true,
              target_speaks_first: false,
            },
            secret_refs: {},
          }),
          credential_values: { LIVEKIT_API_KEY: "lk-key", LIVEKIT_API_SECRET: "lk-secret" },
        }),
      ),
    );

    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("/dashboard/simulate/environments/job-lk"),
    );
    expect(createdBody().credential_values).toEqual({
      LIVEKIT_API_KEY: "lk-key",
      LIVEKIT_API_SECRET: "lk-secret",
    });
    const staged = useEnvironmentsStore.getState().draft;
    expect(staged).toMatchObject({ provider: "livekit", agentId: "returns-agent" });
    expect(staged.apiKey).toBeUndefined();
    expect(staged.apiSecret).toBeUndefined();
    expect(JSON.stringify(useEnvironmentsStore.getState())).not.toContain("lk-secret");
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

  it("runs preflight and builds with the key write-only, staging a redacted draft", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    render(<PanelHostedPlatform />);
    fillCreds();
    runPreflight();

    expect(await screen.findByText("Ready to build")).toBeInTheDocument();
    // The raw value rides write-only as credential_values; no ref is stored.
    expect(preflightHarnessJob).toHaveBeenCalledWith(
      expect.objectContaining({
        agent: expect.objectContaining({ connector: "vapi", secret_refs: {} }),
        credential_values: { VAPI_API_KEY: "sk-x" },
      }),
    );

    createHarnessJob.mockResolvedValue({ job: { job_id: "job-plat" } });
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("/dashboard/simulate/environments/job-plat"),
    );
    expect(createdBody().credential_values).toEqual({ VAPI_API_KEY: "sk-x" });
    const staged = useEnvironmentsStore.getState().draft;
    expect(staged).toMatchObject({
      kind: "platform",
      provider: "vapi",
      agentId: "asst_x",
      callDirection: "inbound",
    });
    // The raw key is redacted out of the staged draft and the persisted store.
    expect(staged.apiKey).toBeUndefined();
    expect("secret_refs" in staged).toBe(false);
    expect(JSON.stringify(useEnvironmentsStore.getState())).not.toContain("sk-x");
  });

  it("builds with the value checked by the latest preflight after an edit", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    createHarnessJob.mockResolvedValue({ job: { job_id: "job-plat" } });
    render(<PanelHostedPlatform />);
    fillCreds();
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());

    fireEvent.change(screen.getByPlaceholderText("sk-…"), { target: { value: "sk-new" } });
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());

    await waitFor(() => expect(createHarnessJob).toHaveBeenCalled());
    expect(createdBody().credential_values).toEqual({ VAPI_API_KEY: "sk-new" });
  });

  it("shows a failed create's message and retries it under the same key", async () => {
    preflightHarnessJob.mockResolvedValue(PASS);
    createHarnessJob.mockRejectedValueOnce({
      error: "agent_preflight_failed",
      message: "The agent's credentials didn't pass their check",
      statusCode: 422,
    });
    render(<PanelHostedPlatform />);
    fillCreds();
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "The agent's credentials didn't pass their check",
        { variant: "error" },
      ),
    );
    createHarnessJob.mockRejectedValueOnce({
      detail: "Hosted harness scheduler is unavailable",
      statusCode: 503,
    });
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());

    await waitFor(() => expect(createHarnessJob).toHaveBeenCalledTimes(2));
    const [first, retry] = createHarnessJob.mock.calls.map((call) => call[1]);
    expect(retry).toBe(first);
    // A new preflight is a new build, so it gets a new key.
    createHarnessJob.mockResolvedValue({ job: { job_id: "job-plat" } });
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.change(screen.getByPlaceholderText("sk-…"), { target: { value: "sk-y" } });
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());
    await waitFor(() => expect(createHarnessJob).toHaveBeenCalledTimes(3));
    expect(createHarnessJob.mock.calls[2][1]).not.toBe(first);
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
