import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  fireEvent,
  screen,
  waitFor,
} from "src/utils/test-utils";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  uploadHarnessSource: vi.fn(),
}));

const { preflightHarnessJob, storeHarnessSecretValues } = await import(
  "src/api/harness/harness"
);
const { default: HostedForm } = await import("../forms/HostedForm");
const { default: LiveKitForm } = await import("../forms/LiveKitForm");
const { default: PhoneForm } = await import("../forms/PhoneForm");
const { default: RepoForm } = await import("../forms/RepoForm");
const { CATALOG_ITEMS } = await import("../buildDrawerCatalog");

const itemOf = (id) => CATALOG_ITEMS.find((it) => it.id === id);
const render = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return baseRender(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
};
const type = (name, value) =>
  fireEvent.change(screen.getByRole("textbox", { name }), {
    target: { value },
  });
const typeSecret = (placeholder, value) =>
  fireEvent.change(screen.getAllByPlaceholderText(placeholder)[0], {
    target: { value },
  });
const runPreflight = () =>
  fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
const sentAgent = () => preflightHarnessJob.mock.calls[0][0].agent;
const footerHint = (text) => screen.getByText(text);

beforeEach(() => {
  preflightHarnessJob.mockReset();
  preflightHarnessJob.mockResolvedValue({ ready_to_submit: false });
  storeHarnessSecretValues.mockReset();
  storeHarnessSecretValues.mockResolvedValue({ secret_refs: { KEY: "ref-1" } });
});

describe("Vapi and Retell", () => {
  it("asks for the key, then the ID, saying what's missing", () => {
    render(<HostedForm item={itemOf("vapi")} />);
    expect(footerHint("Add the key")).toBeInTheDocument();
    expect(screen.queryByText("Assistant ID")).toBeNull();
    typeSecret("Paste the key", "vk");
    expect(screen.getByText("Assistant ID")).toBeInTheDocument();
    expect(footerHint("Add the assistant ID")).toBeInTheDocument();
  });

  it("builds Vapi voice over the phone with its number", async () => {
    render(<HostedForm item={itemOf("vapi")} />);
    typeSecret("Paste the key", "vk");
    type("Assistant ID", "asst_1");
    fireEvent.click(screen.getByRole("button", { name: "Phone" }));
    type("Contact Number", "4155550142");
    runPreflight();
    await waitFor(() => expect(preflightHarnessJob).toHaveBeenCalled());
    expect(sentAgent()).toMatchObject({
      connector: "vapi",
      config: { assistant_id: "asst_1", phone_number: "+14155550142" },
    });
  });

  it("builds Retell chat with the retell_chat connector", async () => {
    render(<HostedForm item={itemOf("retell")} />);
    fireEvent.click(screen.getByRole("button", { name: /Chat/ }));
    typeSecret("Paste the key", "rk");
    type("Chat agent ID", "agent_1");
    runPreflight();
    await waitFor(() => expect(preflightHarnessJob).toHaveBeenCalled());
    expect(sentAgent()).toMatchObject({
      connector: "retell_chat",
      config: { agent_id: "agent_1" },
    });
  });
});

describe("LiveKit", () => {
  it("sends the URL, the agent name and the prompt, and the key and secret write-only", async () => {
    render(<LiveKitForm />);
    type("LiveKit server URL", "wss://demo.livekit.cloud");
    typeSecret("Paste the key", "lk-key");
    typeSecret("Paste the secret", "lk-secret");
    type("Agent name", "returns-agent");
    expect(footerHint("Paste the agent's system prompt")).toBeInTheDocument();
    type("System prompt", "You handle returns.");
    runPreflight();
    await waitFor(() => expect(preflightHarnessJob).toHaveBeenCalled());
    // Sent write-only with the preflight, never exchanged for stored refs.
    expect(storeHarnessSecretValues).not.toHaveBeenCalled();
    expect(preflightHarnessJob.mock.calls[0][0].credential_values).toEqual({
      LIVEKIT_API_KEY: "lk-key",
      LIVEKIT_API_SECRET: "lk-secret",
    });
    expect(sentAgent()).toMatchObject({
      connector: "livekit",
      config: {
        agent_name: "returns-agent",
        livekit_url: "wss://demo.livekit.cloud",
        target_system_prompt: "You handle returns.",
      },
    });
  });
});

describe("Phone number", () => {
  it("dials the number with the pasted instructions, always inbound", async () => {
    render(<PhoneForm />);
    expect(footerHint("Add the number to call")).toBeInTheDocument();
    type("Number to call", "4155550142");
    type("Its instructions", "You book tables.");
    runPreflight();
    await waitFor(() => expect(preflightHarnessJob).toHaveBeenCalled());
    expect(sentAgent()).toMatchObject({
      connector: "phone",
      call_direction: "inbound",
      config: {
        phone_number: "+14155550142",
        target_system_prompt: "You book tables.",
        inbound: true,
      },
    });
  });

  it("offers outbound and steps, UI only", () => {
    render(<PhoneForm />);
    expect(screen.getByRole("button", { name: /Outbound/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /Steps/ })).toBeEnabled();
    expect(screen.queryByText("Coming soon")).toBeNull();
  });
});

describe("GitHub", () => {
  it("reads a pasted repository link", async () => {
    render(<RepoForm />);
    expect(footerHint("Paste the repository link")).toBeInTheDocument();
    type("Repository link", "https://github.com/acme/agent");
    runPreflight();
    await waitFor(() => expect(preflightHarnessJob).toHaveBeenCalled());
    expect(JSON.stringify(preflightHarnessJob.mock.calls[0][0])).toContain(
      "acme/agent",
    );
  });

  it("asks a private repository for its GitHub App installation", () => {
    render(<RepoForm />);
    fireEvent.click(screen.getByRole("button", { name: "Private" }));
    expect(
      screen.getByRole("textbox", { name: "GitHub App installation ID" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: "Use an access token" }),
    ).toBeEnabled();
  });
});
