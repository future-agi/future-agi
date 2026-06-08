import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import {
  renderWithRouter,
  screen,
  userEvent,
  within,
} from "src/utils/test-utils";

import ApiKeysLandingPage from "src/pages/dashboard/settings/api-keys/ApiKeysLandingPage";
import NewObserve from "./NewObserve";

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

const mocks = vi.hoisted(() => ({
  copyToClipboard: vi.fn(),
  mutationData: null,
  refreshServerSide: vi.fn(),
  resetMutation: vi.fn(),
  useQuery: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: (options) => ({
    data: mocks.mutationData,
    isPending: false,
    mutate: () => {
      mocks.mutationData = {
        data: {
          result: {
            api_key: "api-key",
            masked_api_key: "api-****",
            masked_secret_key: "secret-****",
            secret_key: "secret-key",
          },
        },
      };
      options?.onSuccess?.();
    },
    reset: mocks.resetMutation,
  }),
  useQuery: (args) => mocks.useQuery(args),
}));

vi.mock("ag-grid-react", async () => {
  const React = await import("react");
  const AgGridReact = React.forwardRef((_props, ref) => {
    React.useImperativeHandle(ref, () => ({
      api: {
        refreshServerSide: mocks.refreshServerSide,
      },
    }));
    return <div data-testid="api-keys-grid" />;
  });
  AgGridReact.displayName = "AgGridReact";

  return {
    AgGridReact,
  };
});

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: () => ({}),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
  endpoints: {
    keys: {
      generateSecretKey: "/accounts/key/generate_secret_key/",
      getKeys: "/accounts/key/get_secret_keys/",
    },
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

vi.mock("src/utils/utils", () => ({
  copyToClipboard: mocks.copyToClipboard,
}));

vi.mock("src/components/iconify", () => ({
  default: (props) => <span data-testid="iconify" {...props} />,
}));

vi.mock("src/components/image", () => ({
  default: (props) => <img alt="" {...props} />,
}));

vi.mock("src/components/svg-color", () => ({
  default: (props) => <span data-testid="svg-color" {...props} />,
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

const ObserveSetupRoute = () => (
  <NewObserve
    showFirstTraceGuide
    setupVerification={{
      description:
        "Keep this page open after running your app. We check every few seconds and move you forward when data arrives.",
      status: "waiting",
      title: "Checking for your first trace",
    }}
  />
);

describe("Observe API key onboarding handoff", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.mutationData = null;
    mocks.useQuery.mockReturnValue({
      data: codeBlockFixture,
      error: null,
      isLoading: false,
      isSuccess: true,
    });
  });

  it("returns from key creation to the credential-ready observe setup state", async () => {
    const user = userEvent.setup();

    renderWithRouter(
      <Routes>
        <Route path="/dashboard/observe" element={<ObserveSetupRoute />} />
        <Route
          path="/dashboard/settings/api_keys"
          element={<ApiKeysLandingPage />}
        />
      </Routes>,
      { route: "/dashboard/observe?setup=true&source=onboarding" },
    );

    const guide = screen.getByTestId("observe-first-trace-guide");
    await user.click(within(guide).getByRole("button", { name: /^openai$/i }));
    expect(
      await within(guide).findByText("Connect OpenAI, then send one trace"),
    ).toBeVisible();

    await user.click(
      within(guide).getByRole("link", { name: /create api key/i }),
    );

    expect(window.location.pathname).toBe("/dashboard/settings/api_keys");
    expect(screen.getByRole("textbox", { name: /key name/i })).toHaveValue(
      "Observe first trace",
    );

    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(
      screen.getByText("Copy both keys before returning to trace setup."),
    ).toBeVisible();

    const returnAction = screen.getByRole("link", {
      name: /back to trace setup/i,
    });
    expect(returnAction).toHaveAttribute("aria-disabled", "true");

    await user.click(screen.getByRole("button", { name: /copy api key/i }));
    expect(mocks.copyToClipboard).toHaveBeenCalledWith("api-key");

    await user.click(screen.getByRole("button", { name: /copy secret key/i }));
    expect(mocks.copyToClipboard).toHaveBeenCalledWith("secret-key");

    expect(returnAction).not.toHaveAttribute("aria-disabled", "true");
    await user.click(returnAction);

    expect(window.location.pathname).toBe("/dashboard/observe");
    expect(
      new URLSearchParams(window.location.search).get("credential_step"),
    ).toBe("done");
    expect(new URLSearchParams(window.location.search).get("provider")).toBe(
      "openai",
    );
    expect(
      within(screen.getByTestId("observe-first-trace-guide")).getByText(
        "Credentials copied. Paste both values into the snippet, then run one request.",
      ),
    ).toBeVisible();
  });
});
