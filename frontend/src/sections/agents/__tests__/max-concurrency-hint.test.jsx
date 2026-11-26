import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

// The "Max Concurrent Sessions" hint must never read "max: undefined". The
// auth context does not carry an org limit today, so the max part is left out
// unless a real positive number is known.

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});

const auth = { value: {} };
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => auth.value,
}));

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useParams: () => ({ agentDefinitionId: "agent-1" }) };
});

vi.mock("src/api/knowledge-base/files", () => ({
  useKnowledgeBaseList: () => ({ data: [] }),
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    enqueueSnackbar: vi.fn(),
    useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
  };
});

vi.mock("src/utils/Mixpanel", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, trackEvent: vi.fn(), handleOnDocsClicked: vi.fn() };
});

vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({ default: () => null }),
);

const { default: axios } = await import("src/utils/axios");
const { useAgentDetailsStore } = await import("../store/agentDetailsStore");
const { concurrencyFieldProps } = await import("../constants");
const { default: AgentVoiceForm } = await import(
  "../CreateNewAgent/AgentConfigurationStep/AgentVoiceForm"
);
const { default: AgentConfigurationView } = await import(
  "../AgentConfiguration/AgentConfigurationView"
);

const testTheme = createTheme({
  palette: palette("light"),
  spacing: (factor) => `${0.25 * factor}rem`,
});

function Providers({ children }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider theme={testTheme}>{children}</ThemeProvider>
    </QueryClientProvider>
  );
}
Providers.propTypes = { children: PropTypes.node };

const RETELL_AGENT = {
  id: "v1",
  agent_kind: "hosted",
  api_key: "sk-test",
  configuration_snapshot: {
    agent_name: "Support agent",
    languages: ["en"],
    observability_enabled: false,
    description: "Answers support questions",
    inbound: true,
    contact_number: "",
    commit_message: "",
    agent_type: "voice",
    provider: "retell",
    assistant_id: "asst_1",
    authentication_method: "api_key",
  },
};

function CreateRetellForm() {
  const methods = useForm({
    defaultValues: {
      agentType: "voice",
      agentName: "Test agent",
      provider: "retell",
      authenticationMethod: "api_key",
      apiKey: "",
      assistantId: "",
      inbound: false,
      observabilityEnabled: false,
      headers: [],
    },
  });
  return (
    <FormProvider {...methods}>
      <AgentVoiceForm />
    </FormProvider>
  );
}

const MOUNTS = [
  {
    name: "the Current Configuration page",
    mount: () => {
      useAgentDetailsStore.setState({
        agentDetails: RETELL_AGENT,
        selectedVersion: "v1",
      });
      render(
        <Providers>
          <AgentConfigurationView />
        </Providers>,
      );
    },
  },
  {
    name: "the Create Agent voice step",
    mount: () =>
      render(
        <Providers>
          <CreateRetellForm />
        </Providers>,
      ),
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  auth.value = {};
  axios.get.mockResolvedValue({ data: {} });
  axios.post.mockResolvedValue({ data: {} });
});

describe.each(MOUNTS)("Max Concurrent Sessions hint on $name", ({ mount }) => {
  it.each([
    ["no org limit", {}],
    ["an object org limit", { orgLimit: {} }],
    ["a zero org limit", { orgLimit: 0 }],
  ])("leaves out the max part with %s", async (_label, ctx) => {
    auth.value = ctx;
    mount();
    const input = await screen.findByLabelText(/Max Concurrent Sessions/);
    const hint = await screen.findByText(/Max simultaneous test calls/);
    expect(hint).toHaveTextContent("(default: 5)");
    expect(hint.textContent).not.toMatch(/max:/);
    expect(hint.textContent).not.toMatch(/undefined|object/);
    expect(input).not.toHaveAttribute("max");
    expect(input).toHaveAttribute("min", "1");
  });

  it("shows a known org limit as the max", async () => {
    auth.value = { orgLimit: 25 };
    mount();
    const input = await screen.findByLabelText(/Max Concurrent Sessions/);
    expect(
      await screen.findByText(/Max simultaneous test calls/),
    ).toHaveTextContent("(default: 5, max: 25)");
    expect(input).toHaveAttribute("max", "25");
  });
});

describe("concurrencyFieldProps", () => {
  it.each([undefined, null, {}, "25", 0, -3, NaN, Infinity])(
    "omits max for %s",
    (orgLimit) => {
      const props = concurrencyFieldProps(orgLimit);
      expect(props.inputProps).toEqual({ min: 1 });
      expect(props.helperText).toMatch(/\(default: 5\)$/);
    },
  );

  it("includes a positive limit", () => {
    const props = concurrencyFieldProps(10);
    expect(props.inputProps).toEqual({ min: 1, max: 10 });
    expect(props.helperText).toMatch(/\(default: 5, max: 10\)$/);
  });
});
