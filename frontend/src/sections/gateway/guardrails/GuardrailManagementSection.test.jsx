import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import GuardrailManagementSection from "./GuardrailManagementSection";

// Saving is Owner or Admin only (useGatewayAdmin); these cases act as an
// Admin. AdminGatingOwned.test.jsx covers the other levels.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const { post } = vi.hoisted(() => ({ post: vi.fn() }));

let mockOrgConfigReturn = {
  data: null,
  isLoading: false,
};
let mockTab;

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ tab: mockTab }),
    useNavigate: () => vi.fn(),
  };
});

vi.mock("src/utils/axios", () => ({
  default: { post, get: vi.fn() },
  endpoints: {
    gateway: {
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
      testPlayground: (id) => `/agentcc/gateways/${id}/test-playground/`,
    },
  },
  createQueryString: vi.fn(),
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => mockOrgConfigReturn,
}));

vi.mock("../providers/hooks/useGatewayConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  useToggleGuardrail: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1", isLoading: false }),
}));

vi.mock("./GuardrailAnalyticsTab", () => ({
  default: () => <div>Analytics tab</div>,
}));

vi.mock("./FeedbackSummaryCard", () => ({
  default: () => <div>Feedback summary</div>,
}));

vi.mock("./EditGuardrailDialog", () => ({
  default: () => null,
}));

// Stands in for the rules editor: one button applies an edit the way the real
// tab does, as the checks map with each rule's original name kept.
const EDITED_CHECKS = {
  checks: {
    "pii-detection": {
      enabled: false,
      action: "block",
      confidence_threshold: 0.8,
      stage: "pre",
      mode: "sync",
      config: {},
      _originalName: "pii-detector",
    },
    "openai-moderation": {
      enabled: true,
      action: "block",
      confidence_threshold: 0.8,
      stage: "pre",
      mode: "sync",
      config: { api_key: "__encrypted__" },
      _originalName: "openai-moderation",
    },
  },
};

const MockGuardrailConfigTab = ({ onChange }) => (
  <button type="button" onClick={() => onChange(EDITED_CHECKS)}>
    Disable PII check
  </button>
);
MockGuardrailConfigTab.propTypes = { onChange: PropTypes.func.isRequired };

// A test can switch to the real rules editor, to save what it really emits.
let mockRealConfigTab = false;

vi.mock("../settings/GuardrailConfigTab", async (importOriginal) => {
  const { default: RealGuardrailConfigTab } = await importOriginal();
  return {
    default: (props) =>
      mockRealConfigTab ? (
        <RealGuardrailConfigTab {...props} />
      ) : (
        <MockGuardrailConfigTab {...props} />
      ),
  };
});

function renderWithQueryClient(ui) {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

describe("GuardrailManagementSection", () => {
  beforeEach(() => {
    mockTab = undefined;
    mockRealConfigTab = false;
    post.mockReset();
    mockOrgConfigReturn = {
      data: null,
      isLoading: false,
    };
  });

  it("derives the overview type label from the guardrail name", () => {
    mockOrgConfigReturn = {
      data: {
        guardrails: {
          rules: [
            {
              name: "pii-detector",
              stage: "pre",
              action: "block",
              enabled: true,
            },
            {
              name: "futureagi-eval",
              stage: "pre",
              action: "block",
              enabled: true,
            },
          ],
        },
      },
      isLoading: false,
    };

    render(<GuardrailManagementSection />);

    expect(screen.getByText("PII")).toBeInTheDocument();
    expect(screen.getByText("Model-based")).toBeInTheDocument();
  });

  // The gateway never receives a rule's stage: an org's check runs where its
  // type runs, and only data leakage and hallucination checks read the
  // answer. The column shows that, not the stage stored with the rule.
  it("shows the stage the gateway runs each check at, not the stored one", () => {
    mockOrgConfigReturn = {
      data: {
        guardrails: {
          rules: [
            {
              name: "pii-detector",
              stage: "post",
              action: "block",
              enabled: true,
            },
            {
              name: "data-leakage-prevention",
              stage: "pre",
              action: "block",
              enabled: true,
            },
            { name: "hallucination-detection", action: "warn", enabled: true },
          ],
        },
      },
      isLoading: false,
    };

    render(<GuardrailManagementSection />);

    const stageOf = (name) =>
      within(screen.getByText(name).closest("tr")).getByText(/LLM/).textContent;
    expect(stageOf("pii-detector")).toBe("Before LLM");
    expect(stageOf("data-leakage-prevention")).toBe("After LLM");
    expect(stageOf("hallucination-detection")).toBe("After LLM");
    // A blocked stream ends with the guardrail's error.
    expect(
      screen.getByText(/a blocked stream ends with the guardrail's error/),
    ).toBeInTheDocument();
    // The note states what the gateway does when a stream ends.
    expect(screen.queryByText(/withhold/)).toBeNull();
    // The gateway gives the checks the answer on every route that answers
    // with a chat-style answer: chat, completions, Anthropic Messages,
    // Gemini and Responses, passed through or translated, streamed or not,
    // whether or not request bodies are logged. A stream is read when it
    // ends, and a blocked one ends with the guardrail's error. A background
    // Responses request, whose answer never reaches the gateway, is refused
    // when such a check would block it. The note says so, and limits the
    // checks to no subset of these requests.
    const note = screen.getByText(/After LLM checks read/).textContent;
    expect(note).toMatch(
      /on chat, completions, Anthropic Messages, Gemini and Responses requests, streamed or not/,
    );
    expect(note).toMatch(
      /After LLM checks read the text of the model's answer, not the prompt/,
    );
    expect(note).toMatch(
      /On a stream they read the answer when the stream ends/,
    );
    expect(note).toMatch(/a blocked stream ends with the guardrail's error/);
    expect(note).toMatch(
      /A background Responses request is refused when an After LLM check would block it/,
    );
    expect(note).not.toMatch(/except/);
    expect(note).not.toMatch(/unless/);
  });

  it("says nothing about streams when every check reads the request", () => {
    mockOrgConfigReturn = {
      data: {
        guardrails: {
          rules: [
            {
              name: "pii-detector",
              stage: "post",
              action: "block",
              enabled: true,
            },
          ],
        },
      },
      isLoading: false,
    };

    render(<GuardrailManagementSection />);

    expect(screen.getByText("Before LLM")).toBeInTheDocument();
    expect(screen.queryByText("After LLM")).toBeNull();
    expect(screen.queryByText(/stream/)).toBeNull();
  });

  it("does not blame the prompt when the test request is blocked", async () => {
    const user = userEvent.setup();
    mockTab = "playground";
    post.mockResolvedValue({
      data: {
        result: {
          status_code: 403,
          blocked: true,
          warned: false,
          body: { error: { code: "content_blocked" } },
        },
      },
    });

    render(<GuardrailManagementSection />);

    await user.type(
      screen.getByPlaceholderText("Enter a test prompt..."),
      "hi",
    );
    await user.click(screen.getByRole("button", { name: "Run Test" }));

    expect(
      await screen.findByText(/refused the prompt or the model's answer/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/The prompt triggered/)).toBeNull();
    expect(post).toHaveBeenCalledWith(
      "/agentcc/gateways/gateway-1/test-playground/",
      { prompt: "hi" },
    );
  });

  it("shows guardrail action in the overview instead of execution mode", () => {
    mockOrgConfigReturn = {
      data: {
        guardrails: {
          rules: [
            {
              name: "keyword-blocklist",
              stage: "pre",
              mode: "sync",
              action: "block",
              enabled: true,
              config: { words: ["hello", "world"] },
            },
          ],
        },
      },
      isLoading: false,
    };

    render(<GuardrailManagementSection />);

    expect(screen.getByText("Action")).toBeInTheDocument();
    expect(screen.getByText("block")).toBeInTheDocument();
    expect(screen.queryByText("sync")).not.toBeInTheDocument();
  });

  it("saves the rules through update-config as a guardrails-only patch", async () => {
    const user = userEvent.setup();
    mockTab = "configuration";
    post.mockResolvedValue({
      data: { result: { version: 6, gateway_synced: true } },
    });
    mockOrgConfigReturn = {
      data: {
        guardrails: {
          rules: [
            {
              name: "pii-detector",
              stage: "pre",
              mode: "sync",
              action: "block",
              threshold: 0.8,
              enabled: true,
              config: {},
            },
            {
              name: "openai-moderation",
              stage: "pre",
              mode: "sync",
              action: "block",
              threshold: 0.8,
              enabled: true,
              config: { api_key: "__encrypted__" },
              _policy: "policy-1",
            },
          ],
        },
        routing: { strategy: "cost" },
        managed_models: { enabled: true },
      },
      isLoading: false,
    };

    renderWithQueryClient(<GuardrailManagementSection />);

    await user.click(screen.getByRole("button", { name: "Disable PII check" }));
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/agentcc/gateways/gateway-1/update-config/");
    expect(Object.keys(body)).toEqual(["guardrails"]);
    expect(body.guardrails.rules).toEqual([
      expect.objectContaining({ name: "pii-detector", enabled: false }),
      // The rule's credential policy survives the round trip through the
      // checks map, so the push can swap its credential in.
      expect.objectContaining({
        name: "openai-moderation",
        _policy: "policy-1",
        config: { api_key: "__encrypted__" },
      }),
    ]);
    post.mock.calls.forEach(([postedUrl]) =>
      expect(postedUrl).not.toMatch(/org-configs/),
    );
  });

  it("saves a section the policy sync stored as rules, emptying its checks list rather than sending null", async () => {
    const user = userEvent.setup();
    mockTab = "configuration";
    mockRealConfigTab = true;
    post.mockResolvedValue({
      data: { result: { version: 7, gateway_synced: true } },
    });
    // agentcc/services/guardrail_sync.py writes the section in this shape on
    // every guardrail-policy create or update.
    mockOrgConfigReturn = {
      data: {
        guardrails: {
          enabled: true,
          checks: [
            {
              name: "pii-detector",
              enabled: true,
              action: "block",
              threshold: 0.6,
              config: { entities: ["EMAIL_ADDRESS"] },
              _policy: "policy-pii",
              _policy_name: "PII",
              _mode: "sync",
              _scope: "global",
            },
            {
              name: "openai-moderation",
              enabled: true,
              action: "warn",
              threshold: 0.9,
              config: { api_key: "__encrypted__" },
              _policy: "policy-mod",
              _policy_name: "Moderation",
              _mode: "sync",
              _scope: "global",
            },
          ],
        },
        managed_models: { enabled: true },
      },
      isLoading: false,
    };

    renderWithQueryClient(<GuardrailManagementSection />);

    const piiCard = screen.getByText("PII Detection").closest(".MuiCard-root");
    await user.click(within(piiCard).getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/agentcc/gateways/gateway-1/update-config/");
    // The checks list is replaced by an empty one, so the push reads the
    // rules. Each rule keeps its policy (for the encrypted credential) and
    // its threshold.
    expect(body).toEqual({
      guardrails: {
        checks: [],
        rules: [
          {
            name: "pii-detector",
            stage: "pre",
            mode: "sync",
            action: "block",
            threshold: 0.6,
            enabled: false,
            config: { entities: ["EMAIL_ADDRESS"] },
            _policy: "policy-pii",
          },
          {
            name: "openai-moderation",
            stage: "pre",
            mode: "sync",
            action: "warn",
            threshold: 0.9,
            enabled: true,
            config: { api_key: "__encrypted__" },
            _policy: "policy-mod",
          },
        ],
      },
    });
  });
});
