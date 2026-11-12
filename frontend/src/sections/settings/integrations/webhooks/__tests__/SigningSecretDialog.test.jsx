import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "src/utils/test-utils";
import SigningSecretDialog, { COPY_FAILED } from "../SigningSecretDialog";

const mocks = vi.hoisted(() => ({ post: vi.fn(), copy: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    webhooks: { test: (id) => `/webhooks/endpoints/${id}/test/` },
  },
}));
vi.mock("src/utils/utils", () => ({ copyToClipboard: mocks.copy }));

const endpointA = {
  id: "whep_a",
  subscriptions: [{ event_type: "prompt.version.created" }],
};
const endpointB = {
  id: "whep_b",
  subscriptions: [{ event_type: "prompt.version.created" }],
};

function dialog(props) {
  return (
    <SigningSecretDialog open onClose={() => {}} title="ready" {...props} />
  );
}

describe("SigningSecretDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.copy.mockResolvedValue(true);
    mocks.post.mockResolvedValue({
      status: 200,
      data: {
        status: true,
        result: { status_code: 200, outcome: "success", latency_ms: 12 },
      },
    });
  });

  it("starts clean for a new secret: no 'Copied', no earlier test answer", async () => {
    const client = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const { rerender } = render(
      <QueryClientProvider client={client}>
        {dialog({ secret: "whsec_AAAA", endpoint: endpointA })}
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(mocks.copy).toHaveBeenCalledWith("whsec_AAAA");
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Send a test now" }));
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toMatch(
        /^Your endpoint answered 200/,
      ),
    );
    expect(mocks.post).toHaveBeenCalledWith(
      "/webhooks/endpoints/whep_a/test/",
      { event_type: "prompt.version.created" },
      expect.anything(),
    );

    rerender(
      <QueryClientProvider client={client}>
        {dialog({ secret: "whsec_BBBB", endpoint: endpointB })}
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Copied" })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Signing secret" }).value).toBe(
      "whsec_BBBB",
    );
  });

  it("says so when the clipboard refused the secret, and never says Copied (review M3)", async () => {
    mocks.copy.mockResolvedValue(false);
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        {dialog({ secret: "whsec_CCCC", endpoint: endpointA })}
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByText(COPY_FAILED)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Copied" })).toBeNull();
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
  });

  it("keeps the secret out of session replays (review M4)", () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        {dialog({ secret: "whsec_DDDD", endpoint: endpointA })}
      </QueryClientProvider>,
    );
    const field = screen.getByRole("textbox", { name: "Signing secret" });
    const blocked = field.closest(".ph-no-capture");
    expect(blocked).not.toBeNull();
    expect(blocked.classList.contains("sensitive")).toBe(true);
  });
});
