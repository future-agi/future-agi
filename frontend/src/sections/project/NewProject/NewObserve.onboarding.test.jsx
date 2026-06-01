import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

import NewObserve from "./NewObserve";
import { persistSetupQuickStartAttribution } from "src/sections/auth/jwt/setup-org-quick-starts";

const mocks = vi.hoisted(() => ({
  useQuery: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: (args) => mocks.useQuery(args),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    project: {
      getCodeBlockTracer: "/project/code-block-tracer",
    },
  },
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {
    apikeys: "apikeys",
  },
  handleOnDocsClicked: vi.fn(),
  trackEvent: vi.fn(),
}));

vi.mock("./InstructionCodeCopy", () => ({
  default: ({ ariaLabel, language, text }) => (
    <pre
      aria-label={ariaLabel}
      data-language={language}
      data-testid="code-copy"
    >
      {text}
    </pre>
  ),
}));

vi.mock("./ObserveInstuments", () => ({
  default: () => <div>Package options</div>,
}));

const codeBlockFixture = {
  installationGuide: {
    Python: "pip install futureagi",
    TypeScript: "npm install @futureagi/tracer",
  },
  keys: {
    Python: "export FUTUREAGI_API_KEY=test",
    TypeScript: "process.env.FUTUREAGI_API_KEY = 'test'",
  },
  projectAddCode: {
    Python: "from futureagi import trace",
    TypeScript: "import { trace } from '@futureagi/tracer'",
  },
  instruments: [],
};

const codeBlockWithInstrumentsFixture = {
  ...codeBlockFixture,
  instruments: {
    anthropic: {
      name: "Anthropic",
      Python: {
        code: "from traceai_anthropic import AnthropicInstrumentor",
        sample_request_code:
          'import os\nimport anthropic\n\nclient = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])\n\nmessage = client.messages.create(\n    model="claude-sonnet-4-20250514",\n    max_tokens=256,\n    messages=[{"role": "user", "content": "Say hello in one sentence."}],\n)\n\nprint(message.content)',
      },
      TypeScript: {
        code: 'import { AnthropicInstrumentation } from "@traceai/anthropic";',
        sample_request_code: "anthropic ts smoke",
      },
    },
    bedrock: {
      name: "Bedrock",
      Python: {
        code: "from traceai_bedrock import BedrockInstrumentor",
        sample_request_code: "bedrock python smoke",
      },
    },
    langchain: {
      name: "LangChain",
      Python: {
        code: "from traceai_langchain import LangChainInstrumentor",
        sample_request_code: "langchain python smoke",
      },
    },
    llama_index: {
      name: "LlamaIndex",
      Python: {
        code: "from traceai_llamaindex import LlamaIndexInstrumentor",
        sample_request_code: "llama python smoke",
      },
    },
    mcp: {
      name: "MCP",
      Python: {
        code: "from traceai_mcp import MCPInstrumentor",
        sample_request_code: "mcp python smoke",
      },
    },
    openai: {
      name: "OpenAI",
      Python: {
        code: "from traceai_openai import OpenAIInstrumentor",
        sample_request_code: "openai python smoke",
      },
      TypeScript: {
        code: 'import { OpenAIInstrumentation } from "@traceai/openai";',
        sample_request_code: "openai ts smoke",
      },
    },
    openai_agents: {
      name: "OpenAI Agents",
      Python: {
        code: "from traceai_openai_agents import OpenAIAgentsInstrumentor",
        sample_request_code: "openai agents python smoke",
      },
    },
  },
};

const returnToFromApiKeyHref = (href) =>
  new URLSearchParams(href.split("?")[1]).get("return_to");

describe("NewObserve onboarding setup", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("renders a compact first-trace guide before the full setup reference", () => {
    mocks.useQuery.mockReturnValue({
      data: codeBlockFixture,
      error: null,
      isLoading: false,
      isSuccess: true,
    });

    renderWithRouter(
      <NewObserve
        showFirstTraceGuide
        setupVerification={{
          description:
            "Keep this page open after running your app. We check every few seconds and move you forward when data arrives.",
          status: "waiting",
          title: "Checking for your first trace",
        }}
      />,
      { route: "/dashboard/observe?setup=true&source=onboarding" },
    );

    const guide = screen.getByTestId("observe-first-trace-guide");
    expect(guide).toBeVisible();
    expect(within(guide).getByText("Setup guide")).toBeVisible();
    expect(
      within(guide).getByText("Connect OpenAI, then send one trace"),
    ).toBeVisible();
    expect(
      within(guide).getByTestId("observe-package-specific-code-alert"),
    ).toHaveTextContent("OpenAI Python code selected");
    expect(within(guide).getByText("Pick SDK package")).toBeVisible();
    expect(within(guide).getByText("Create keys")).toBeVisible();
    expect(within(guide).getByText("Run one request")).toBeVisible();
    expect(within(guide).getByText("Review and create eval")).toBeVisible();
    expect(
      within(guide).getByText("pip install traceAI-openai openai"),
    ).toBeVisible();
    expect(
      within(guide).getByText("export FUTUREAGI_API_KEY=test"),
    ).toBeVisible();
    expect(
      within(guide).getByText(
        "Create a Future AGI API key and secret key before running the snippet.",
      ),
    ).toBeVisible();
    expect(
      within(guide).getByLabelText("Copy OpenAI runtime keys"),
    ).toHaveTextContent('os.environ.setdefault("OPENAI_API_KEY", "...")');
    expect(
      within(guide).getByLabelText("Copy complete package setup"),
    ).toHaveTextContent('os.environ.setdefault("OPENAI_API_KEY", "...")');
    expect(
      within(guide).getByLabelText("Copy complete package setup"),
    ).toHaveTextContent("OpenAIInstrumentor");
    const apiKeysLink = within(guide).getByRole("link", {
      name: /Create API key/i,
    });
    expect(apiKeysLink).toBeVisible();
    expect(apiKeysLink).toHaveAttribute(
      "href",
      "/dashboard/settings/api_keys?source=onboarding&target=observe_first_trace&action=create&key_name=Observe+first+trace&return_to=%2Fdashboard%2Fobserve%3Fsetup%3Dtrue%26source%3Donboarding%26credential_step%3Ddone%26provider%3Dopenai%26language%3Dpython",
    );
    expect(within(guide).getByLabelText("Copy install command")).toBeVisible();
    expect(within(guide).getByLabelText("Copy project keys")).toBeVisible();
    expect(
      within(guide).getByLabelText("Copy project registration"),
    ).toBeVisible();
    expect(
      within(guide).getByLabelText("Copy package setup code"),
    ).toHaveTextContent(
      "OpenAIInstrumentor().instrument(tracer_provider=trace_provider)",
    );
    expect(
      within(guide).getByLabelText("Copy package request"),
    ).toHaveTextContent("client.responses.create");
    expect(
      within(guide).getByTestId("observe-setup-verification"),
    ).toHaveTextContent("Checking for your first trace");
    expect(screen.queryByText("Full setup reference")).not.toBeInTheDocument();
    expect(screen.queryByText("Package options")).not.toBeInTheDocument();
  });

  it("uses built-in package snippets when the code-block response omits the requested package", () => {
    mocks.useQuery.mockReturnValue({
      data: codeBlockFixture,
      error: null,
      isLoading: false,
      isSuccess: true,
    });

    renderWithRouter(<NewObserve showFirstTraceGuide />, {
      route:
        "/dashboard/observe?setup=true&source=onboarding&provider=anthropic&language=python",
    });

    const guide = screen.getByTestId("observe-first-trace-guide");
    expect(
      within(guide).getByText("Connect Anthropic, then send one trace"),
    ).toBeVisible();
    expect(
      within(guide).getByText("pip install traceAI-anthropic anthropic"),
    ).toBeVisible();
    expect(
      within(guide).getByLabelText("Copy package setup code"),
    ).toHaveTextContent(
      "AnthropicInstrumentor().instrument(tracer_provider=trace_provider)",
    );
    expect(
      within(guide).getByLabelText("Copy package request"),
    ).toHaveTextContent("client.messages.create");
    expect(
      screen.queryByText(/requested package is not available/i),
    ).not.toBeInTheDocument();
  });

  it("keeps quick-start attribution through API key creation", () => {
    persistSetupQuickStartAttribution({
      quickStartGoal: "monitor_production_ai_app",
      quickStartId: "observe",
      quickStartPrimaryPath: "observe",
    });
    mocks.useQuery.mockReturnValue({
      data: codeBlockFixture,
      error: null,
      isLoading: false,
      isSuccess: true,
    });

    renderWithRouter(<NewObserve showFirstTraceGuide />, {
      route:
        "/dashboard/observe?setup=true&source=onboarding&provider=anthropic&language=python",
    });

    const guide = screen.getByTestId("observe-first-trace-guide");
    const returnTo = returnToFromApiKeyHref(
      within(guide)
        .getByRole("link", { name: /Create API key/i })
        .getAttribute("href"),
    );
    const returnParams = new URLSearchParams(returnTo.split("?")[1]);

    expect(returnParams.get("provider")).toBe("anthropic");
    expect(returnParams.get("language")).toBe("python");
    expect(returnParams.get("quick_start_goal")).toBe(
      "monitor_production_ai_app",
    );
    expect(returnParams.get("quick_start_id")).toBe("observe");
    expect(returnParams.get("quick_start_primary_path")).toBe("observe");
  });

  it("acknowledges copied credentials after returning from key creation", () => {
    mocks.useQuery.mockReturnValue({
      data: codeBlockFixture,
      error: null,
      isLoading: false,
      isSuccess: true,
    });

    renderWithRouter(
      <NewObserve
        showFirstTraceGuide
        setupVerification={{
          description: "Run one request after pasting the keys.",
          status: "waiting",
          title: "Checking for your first trace",
        }}
      />,
      {
        route:
          "/dashboard/observe?setup=true&source=onboarding&credential_step=done",
      },
    );

    const guide = screen.getByTestId("observe-first-trace-guide");
    expect(
      within(guide).getByText(
        "Credentials copied. Paste both values into the snippet, then run one request.",
      ),
    ).toBeVisible();
    expect(
      within(guide).getByRole("link", { name: /Create another key/i }),
    ).toBeVisible();
  });

  it("acknowledges copied credentials from lifecycle email links", () => {
    mocks.useQuery.mockReturnValue({
      data: codeBlockFixture,
      error: null,
      isLoading: false,
      isSuccess: true,
    });

    renderWithRouter(<NewObserve showFirstTraceGuide />, {
      route:
        "/dashboard/observe?setup=true&source=onboarding_email&credential_step=done",
    });

    const guide = screen.getByTestId("observe-first-trace-guide");
    expect(
      within(guide).getByText(
        "Credentials copied. Paste both values into the snippet, then run one request.",
      ),
    ).toBeVisible();
  });

  it("shows package-specific instrumentation for the selected provider", async () => {
    mocks.useQuery.mockReturnValue({
      data: codeBlockWithInstrumentsFixture,
      error: null,
      isLoading: false,
      isSuccess: true,
    });

    renderWithRouter(<NewObserve showFirstTraceGuide />, {
      route:
        "/dashboard/observe?setup=true&source=onboarding&provider=anthropic",
    });

    const guide = screen.getByTestId("observe-first-trace-guide");
    expect(screen.getByTestId("observe-instrument-picker")).toBeVisible();
    expect(
      within(guide).getByText("Connect Anthropic, then send one trace"),
    ).toBeVisible();
    expect(
      within(guide).getByRole("button", { name: /anthropic/i }),
    ).toBeVisible();
    expect(within(guide).getByText("1. Install Anthropic")).toBeVisible();
    expect(
      within(guide).getByText("pip install traceAI-anthropic anthropic"),
    ).toBeVisible();
    expect(
      within(guide).getByText(
        "from traceai_anthropic import AnthropicInstrumentor",
      ),
    ).toBeVisible();
    expect(
      within(guide).getByText("4. Run one Anthropic request"),
    ).toBeVisible();
    expect(
      within(guide).getAllByText(/ANTHROPIC_API_KEY/).length,
    ).toBeGreaterThan(0);
    expect(
      within(guide).getAllByText(/client\.messages\.create/).length,
    ).toBeGreaterThan(0);
    expect(
      within(guide).getByText("If the Anthropic trace does not arrive"),
    ).toBeVisible();
    expect(
      within(guide).getByLabelText("Copy Anthropic runtime keys"),
    ).toHaveTextContent('os.environ.setdefault("ANTHROPIC_API_KEY", "...")');
    expect(
      within(guide).getByLabelText("Copy complete package setup"),
    ).toHaveTextContent('os.environ.setdefault("ANTHROPIC_API_KEY", "...")');
    expect(
      within(guide).getByText(
        "Confirm ANTHROPIC_API_KEY is loaded where the request runs.",
      ),
    ).toBeVisible();

    await userEvent.click(
      within(guide).getByRole("tab", { name: /typescript/i }),
    );

    expect(
      within(guide).getByText(
        "npm install @traceai/fi-core @traceai/anthropic @opentelemetry/instrumentation @anthropic-ai/sdk",
      ),
    ).toBeVisible();
    expect(
      within(guide).getByText(
        'import { AnthropicInstrumentation } from "@traceai/anthropic";',
      ),
    ).toBeVisible();
    expect(within(guide).getByText("anthropic ts smoke")).toBeVisible();
    expect(
      within(guide).getByLabelText("Copy Anthropic runtime keys"),
    ).toHaveTextContent('process.env.ANTHROPIC_API_KEY = "..."');
    expect(
      returnToFromApiKeyHref(
        within(guide)
          .getByRole("link", { name: /Create API key/i })
          .getAttribute("href"),
      ),
    ).toBe(
      "/dashboard/observe?setup=true&source=onboarding&credential_step=done&provider=anthropic&language=typescript",
    );

    await userEvent.click(
      within(guide).getByRole("button", { name: /^openai$/i }),
    );

    expect(await within(guide).findByText("1. Install OpenAI")).toBeVisible();
    await waitFor(() => {
      const params = new URLSearchParams(window.location.search);
      expect(params.get("provider")).toBe("openai");
      expect(params.get("language")).toBe("typescript");
    });
    expect(
      within(guide).getByText(
        "npm install @traceai/fi-core @traceai/openai @opentelemetry/instrumentation openai",
      ),
    ).toBeVisible();
    expect(within(guide).getByText("openai ts smoke")).toBeVisible();
    expect(
      returnToFromApiKeyHref(
        within(guide)
          .getByRole("link", { name: /Create API key/i })
          .getAttribute("href"),
      ),
    ).toBe(
      "/dashboard/observe?setup=true&source=onboarding&credential_step=done&provider=openai&language=typescript",
    );
  });

  it("restores selected package and language after API key handoff", () => {
    mocks.useQuery.mockReturnValue({
      data: codeBlockWithInstrumentsFixture,
      error: null,
      isLoading: false,
      isSuccess: true,
    });

    renderWithRouter(<NewObserve showFirstTraceGuide />, {
      route:
        "/dashboard/observe?setup=true&source=onboarding&provider=Anthropic&language=TypeScript",
    });

    const guide = screen.getByTestId("observe-first-trace-guide");
    expect(within(guide).getByText("1. Install Anthropic")).toBeVisible();
    expect(
      within(guide).getByText(
        "npm install @traceai/fi-core @traceai/anthropic @opentelemetry/instrumentation @anthropic-ai/sdk",
      ),
    ).toBeVisible();
    expect(
      within(guide).getByText(
        'import { AnthropicInstrumentation } from "@traceai/anthropic";',
      ),
    ).toBeVisible();
  });

  it.each([
    {
      provider: "langchain",
      title: "LangChain",
      install: "pip install traceAI-langchain langchain-openai",
      smoke: "langchain python smoke",
    },
    {
      provider: "openai_agents",
      title: "OpenAI Agents",
      install: "pip install traceAI-openai-agents openai-agents",
      smoke: "openai agents python smoke",
    },
    {
      provider: "llamaindex",
      title: "LlamaIndex",
      install: "pip install traceAI-llamaindex llama-index",
      smoke: "llama python smoke",
    },
    {
      provider: "bedrock",
      title: "Bedrock",
      install: "pip install traceAI-bedrock boto3",
      smoke: "bedrock python smoke",
    },
    {
      provider: "mcp",
      title: "MCP",
      install: "pip install traceAI-mcp traceAI-openai-agents openai-agents",
      smoke: "mcp python smoke",
    },
  ])(
    "shows package-specific Python smoke setup for $title",
    ({ install, provider, smoke, title }) => {
      mocks.useQuery.mockReturnValue({
        data: codeBlockWithInstrumentsFixture,
        error: null,
        isLoading: false,
        isSuccess: true,
      });

      renderWithRouter(<NewObserve showFirstTraceGuide />, {
        route: `/dashboard/observe?setup=true&source=onboarding&provider=${provider}&language=typescript`,
      });

      const guide = screen.getByTestId("observe-first-trace-guide");
      expect(within(guide).getByText(`1. Install ${title}`)).toBeVisible();
      expect(within(guide).getByText(install)).toBeVisible();
      expect(
        within(guide).getByText(`4. Run one ${title} request`),
      ).toBeVisible();
      expect(within(guide).getByText(smoke)).toBeVisible();
      expect(
        within(guide).getByTestId("observe-trace-troubleshooting"),
      ).toHaveTextContent(`If the ${title} trace does not arrive`);
      expect(
        within(guide).getByRole("tab", { name: /typescript/i }),
      ).toBeDisabled();
      expect(new URLSearchParams(window.location.search).get("language")).toBe(
        "python",
      );
    },
  );
});
