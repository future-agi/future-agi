import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import PlatformConnectFlow from "../PlatformConnectFlow";

const mockNavigate = vi.fn();
const mockPost = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, post: (...args) => mockPost(...args) },
  };
});

const auth = vi.hoisted(() => ({ role: "Member" }));
vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: auth.role }),
}));

const renderFlow = (platform) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PlatformConnectFlow platform={platform} />
    </QueryClientProvider>,
  );

const type = (name, value) =>
  fireEvent.change(screen.getByRole("textbox", { name }), {
    target: { value },
  });
const typeSecret = (label, value) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const connect = () => screen.getByRole("button", { name: "Connect agent" });

describe("PlatformConnectFlow", () => {
  beforeEach(() => {
    auth.role = "Member";
    mockNavigate.mockReset();
    mockPost.mockReset();
  });

  it("saves a Vapi assistant to Agents and opens it", async () => {
    mockPost.mockResolvedValue({ data: { agent: { id: "agent-7" } } });
    renderFlow("vapi");

    expect(connect()).toBeDisabled();
    type("Agent name", "Returns line");
    type("Assistant ID", "asst_123");
    typeSecret("Vapi API key", "sk-test");
    expect(connect()).toBeEnabled();
    fireEvent.click(connect());

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    const [url, body] = mockPost.mock.calls[0];
    expect(url).toMatch(/agent-definitions\/create/);
    expect(body).toMatchObject({
      agent_type: "voice",
      agent_name: "Returns line",
      provider: "vapi",
      assistant_id: "asst_123",
      api_key: "sk-test",
      authentication_method: "api_key",
      inbound: true,
    });
    expect(body.contact_number).toBeUndefined();
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.stringMatching(/agent-7\/overview$/),
    );
  });

  it("asks a LiveKit project for its URL and secret too", () => {
    renderFlow("livekit");

    type("Agent name", "Returns line");
    type("LiveKit agent name", "returns-agent");
    typeSecret("LiveKit API key", "key");
    expect(connect()).toBeDisabled();
    type("LiveKit URL", "wss://acme.livekit.cloud");
    typeSecret("LiveKit API secret", "secret");
    expect(connect()).toBeEnabled();
  });

  it("needs only a name and a number for a phone agent", async () => {
    mockPost.mockResolvedValue({ data: { agent: { id: "agent-8" } } });
    renderFlow("phone");

    expect(screen.queryByRole("textbox", { name: "Agent ID" })).toBeNull();
    type("Agent name", "Front desk");
    fireEvent.change(screen.getByPlaceholderText(/Number to call/), {
      target: { value: "4155551234" },
    });
    fireEvent.click(connect());

    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(mockPost.mock.calls[0][1]).toMatchObject({
      provider: "others",
      contact_number: "+14155551234",
    });
  });

  it("won't let a viewer connect", () => {
    auth.role = "Viewer";
    renderFlow("retell");

    type("Agent name", "Returns line");
    type("Agent ID", "agent_1");
    typeSecret("Retell API key", "key");

    expect(connect()).toBeDisabled();
    expect(
      screen.getByText("You don't have permission to connect agents"),
    ).toBeInTheDocument();
  });
});
