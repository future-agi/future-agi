import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

import NewObserve from "./NewObserve";

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
  default: () => <div>Instrumentation options</div>,
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
        sample_request_code: "anthropic python smoke",
      },
      TypeScript: {
        code: 'import { AnthropicInstrumentation } from "@traceai/anthropic";',
        sample_request_code: "anthropic ts smoke",
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
  },
};

const returnToFromApiKeyHref = (href) =>
  new URLSearchParams(href.split("?")[1]).get("return_to");

describe("NewObserve onboarding setup", () => {
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
      within(guide).getByText("Connect your package, then send one trace"),
    ).toBeVisible();
    expect(within(guide).getByText("Pick package")).toBeVisible();
    expect(within(guide).getByText("Paste setup")).toBeVisible();
    expect(within(guide).getByText("Run package request")).toBeVisible();
    expect(within(guide).getByText("Review and add eval")).toBeVisible();
    expect(within(guide).getByText("pip install futureagi")).toBeVisible();
    expect(
      within(guide).getByText("export FUTUREAGI_API_KEY=test"),
    ).toBeVisible();
    expect(
      within(guide).getByText(
        "Create a Future AGI API key and secret key before running the snippet.",
      ),
    ).toBeVisible();
    const apiKeysLink = within(guide).getByRole("link", {
      name: /Create API key/i,
    });
    expect(apiKeysLink).toBeVisible();
    expect(apiKeysLink).toHaveAttribute(
      "href",
      "/dashboard/settings/api_keys?source=onboarding&target=observe_first_trace&action=create&key_name=Observe+first+trace&return_to=%2Fdashboard%2Fobserve%3Fsetup%3Dtrue%26source%3Donboarding%26credential_step%3Ddone",
    );
    expect(within(guide).getByLabelText("Copy install command")).toBeVisible();
    expect(within(guide).getByLabelText("Copy project keys")).toBeVisible();
    expect(
      within(guide).getByLabelText("Copy project registration"),
    ).toBeVisible();
    expect(
      within(guide).getByLabelText("Copy package instrumentation"),
    ).toBeVisible();
    expect(
      within(guide).getByLabelText("Copy package smoke test"),
    ).toHaveTextContent("run_your_existing_your_package_request()");
    expect(
      within(guide).getByTestId("observe-setup-verification"),
    ).toHaveTextContent("Checking for your first trace");
    expect(screen.queryByText("Full setup reference")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Instrumentation options"),
    ).not.toBeInTheDocument();
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
    expect(within(guide).getByText("anthropic python smoke")).toBeVisible();

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
});
