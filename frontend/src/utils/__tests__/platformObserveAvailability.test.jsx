import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FormProvider, useForm } from "react-hook-form";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import axios, { endpoints } from "src/utils/axios";
import ObservabilityToggle from "src/sections/agent-playground/connected/detail/ObservabilityToggle";
import ObservabilityChoice from "src/sections/simulate/environments/panels/ObservabilityChoice";
import AgentVoiceForm from "src/sections/agents/CreateNewAgent/AgentConfigurationStep/AgentVoiceForm";
import AgentBehaviourStepRightSection from "src/sections/agents/CreateNewAgent/AgentBehaviourStep/AgentBehaviourStepRightSection";
import { localScaffoldSteps } from "src/sections/simulate/environments/useTemplate.constants";
import { BUILD_PIPELINE } from "src/sections/simulate/environments/buildEnvironment/buildPipeline.constants";
import { EVALS_COPY } from "src/sections/simulate/environments/workspace/evals/evals.constants";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner", orgLimit: {} }),
}));
const mutate = vi.hoisted(() => vi.fn());
vi.mock("src/api/agents/agentDetail", () => ({
  useSetObservability: () => ({ isPending: false, mutate }),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(), post: vi.fn() },
}));

const VOICE_NOTICE =
  "Calls imported from connected voice providers and spans from agents run in hosted sandboxes aren't stored in Observe in this release.";
const SANDBOX_NOTICE =
  "Calls imported from connected voice providers and spans from agents run in hosted sandboxes aren't stored in Observe in this release.";
const assertKnownIssues = () => {
  const link = screen.getByRole("link", {
    name: "2.0.0 release notes: Known issues",
  });
  expect(link).toHaveAttribute(
    "href",
    "https://github.com/future-agi/future-agi/releases/tag/v2.0.0",
  );
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
};

function VoiceForm() {
  const methods = useForm({
    defaultValues: {
      agentType: "voice",
      provider: "vapi",
      authenticationMethod: "api_key",
      inbound: true,
      apiKey: "fixture-key",
      assistantId: "fixture-agent",
      observabilityEnabled: false,
    },
  });
  return (
    <FormProvider {...methods}>
      <AgentVoiceForm />
      <output data-testid="voice-preference">
        {String(methods.watch("observabilityEnabled"))}
      </output>
      <output data-testid="voice-description">
        {methods.watch("description")}
      </output>
      <output data-testid="voice-commit">
        {methods.watch("commitMessage")}
      </output>
    </FormProvider>
  );
}

beforeEach(() => vi.clearAllMocks());

describe("platform Observe availability in this release", () => {
  it.each([
    ["hosted", false, VOICE_NOTICE],
    ["hosted", true, VOICE_NOTICE],
    ["source", false, SANDBOX_NOTICE],
    ["source", true, SANDBOX_NOTICE],
  ])(
    "keeps %s connected controls visible with saved enabled=%s",
    (agentKind, observabilityEnabled, notice) => {
      render(
        <ObservabilityToggle
          agent={{ id: "agent-1", agentKind, observabilityEnabled }}
        />,
      );
      expect(screen.getByText(notice)).toBeVisible();
      assertKnownIssues();
      const box = screen.getByRole("checkbox", { name: "Observability" });
      expect(box).toBeEnabled();
      expect(box.checked).toBe(observabilityEnabled);
      fireEvent.click(box);
      expect(mutate).toHaveBeenCalledWith(
        { enabled: !observabilityEnabled },
        expect.any(Object),
      );
    },
  );

  it.each([false, true])(
    "keeps sandbox opt-in working without claiming export (checked=%s)",
    (checked) => {
      const onChange = vi.fn();
      render(<ObservabilityChoice checked={checked} onChange={onChange} />);
      expect(screen.getByText(SANDBOX_NOTICE)).toBeVisible();
      assertKnownIssues();
      const box = screen.getByRole("checkbox", {
        name: "Turn on observability",
      });
      expect(box).toBeEnabled();
      fireEvent.click(box);
      expect(onChange).toHaveBeenCalledWith(!checked);
    },
  );

  it("keeps voice fields and required markers working without a validation resolver", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <VoiceForm />
      </QueryClientProvider>,
    );
    expect(screen.getByText(VOICE_NOTICE)).toBeVisible();
    assertKnownIssues();
    const box = screen.getByRole("checkbox", { name: "Observability" });
    expect(box).toBeEnabled();
    expect(screen.getByTestId("voice-preference")).toHaveTextContent("false");
    await userEvent.click(box);
    // This resolver-free fixture only checks fields; production validation POSTs
    // are covered by the production-resolver regression tests.
    expect(screen.getByTestId("voice-preference")).toHaveTextContent("true");
    expect(screen.getByLabelText(/Provider API Key/)).toBeRequired();
    expect(screen.getByLabelText(/Assistant ID/)).toBeRequired();
    await userEvent.click(box);
    expect(screen.getByTestId("voice-preference")).toHaveTextContent("false");
    expect(screen.getByLabelText(/Provider API Key/)).not.toBeRequired();
    expect(screen.getByLabelText(/Assistant ID/)).not.toBeRequired();
  });

  it("still syncs provider configuration and resets the preference when credentials are cleared", async () => {
    axios.post.mockResolvedValue({
      data: {
        result: {
          name: "Support",
          assistant_id: "synced-agent",
          prompt: "Synced instructions",
          commit_message: "Synced configuration",
        },
      },
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <VoiceForm />
      </QueryClientProvider>,
    );
    const box = screen.getByRole("checkbox", { name: "Observability" });
    await userEvent.click(box);
    fireEvent.change(screen.getByLabelText(/Assistant ID/), {
      target: { value: "requested-agent" },
    });
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        endpoints.agentDefinitions.fetchAssistantFromProvider,
        {
          api_key: "fixture-key",
          assistant_id: "requested-agent",
          provider: "vapi",
        },
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("voice-description")).toHaveTextContent(
        "Synced instructions",
      ),
    );
    expect(screen.getByTestId("voice-commit")).toHaveTextContent(
      "Synced configuration",
    );
    expect(screen.getByLabelText(/Assistant ID/)).toHaveValue("synced-agent");
    expect(box).toBeChecked();
    fireEvent.change(screen.getByLabelText(/Provider API Key/), {
      target: { value: "" },
    });
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: "Observability" }),
      ).not.toBeChecked(),
    );
    expect(
      screen.getByRole("checkbox", { name: "Observability" }),
    ).toBeDisabled();
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it.each([false, true])(
    "does not present a saved preference as available export in the summary (enabled=%s)",
    (observabilityEnabled) => {
      function Summary() {
        const { control } = useForm({
          defaultValues: {
            agentType: "voice",
            provider: "vapi",
            authenticationMethod: "api_key",
            observabilityEnabled,
          },
        });
        return <AgentBehaviourStepRightSection control={control} />;
      }
      render(<Summary />);
      expect(
        screen.getByText(
          "Calls imported from connected voice providers and spans from agents run in hosted sandboxes aren't stored in Observe in this release.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(/^(ON|OFF)$/)).not.toBeInTheDocument();
    },
  );

  it("does not promise Observe traces after deploying a template", () => {
    expect(
      localScaffoldSteps({ id: "template-1", name: "Support" })[2].body,
    ).toBe(
      "Runs execute on our infrastructure. Simulation results remain in the simulation views; spans from agents run in hosted sandboxes aren't stored in Observe in this release.",
    );
  });

  it("describes persisted simulation artifacts without promising traces", () => {
    expect(BUILD_PIPELINE.find(({ id }) => id === "upload").detail).toBe(
      "Persisting transcripts, tool logs and evaluation results",
    );
  });

  it("does not promise traces for runs without evaluations", () => {
    expect(EVALS_COPY.emptyNoSuggestions).toBe(
      "You can run without adding evaluations. Simulation results remain in the simulation views.",
    );
  });
});
