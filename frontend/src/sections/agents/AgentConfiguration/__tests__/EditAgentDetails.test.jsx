import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import EditAgentDetails from "../EditAgentDetails";

// The form's inputs aren't under test: only whether the sync button is offered.
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: ({ fieldName }) => <input aria-label={fieldName} />,
}));
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: () => null,
}));
vi.mock("src/components/FormCheckboxField", () => ({
  FormCheckboxField: () => null,
}));
vi.mock("src/components/image", () => ({ default: () => null }));
vi.mock("src/components/Switch/SwitchField", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({
    default: () => null,
  }),
);
vi.mock("../../CreateNewAgent/AgentBasicInfoStep/LanguageMultiSelect", () => ({
  default: () => null,
}));
vi.mock("src/api/knowledge-base/files", () => ({
  useKnowledgeBaseList: () => ({ data: [] }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ orgLimit: {} }),
}));
vi.mock("react-router", () => ({
  useParams: () => ({ agentDefinitionId: "agent-1" }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: { agentDefinitions: { fetchAssistantFromProvider: "/fetch" } },
}));

function Harness({ provider }) {
  const form = useForm({
    defaultValues: {
      agentType: "voice",
      provider,
      apiKey: "",
      assistantId: "",
      agentName: "Agent",
      languages: [],
    },
  });
  return (
    <EditAgentDetails
      control={form.control}
      errors={{}}
      trigger={form.trigger}
      setValue={form.setValue}
      getValues={form.getValues}
      clearErrors={form.clearErrors}
      agentKind="source"
    />
  );
}

Harness.propTypes = { provider: PropTypes.string };

const renderWith = (provider) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Harness provider={provider} />
    </QueryClientProvider>,
  );

describe("EditAgentDetails sync with provider", () => {
  // The form seeds an empty string for an agent with no provider; the API gives null.
  it.each([null, ""])(
    "is not offered to a voice agent whose provider is %j",
    (provider) => {
      renderWith(provider);
      expect(screen.queryByText("Sync with provider")).not.toBeInTheDocument();
      // Only the button is gated; the Assistant ID field stays.
      expect(screen.getByLabelText("assistantId")).toBeInTheDocument();
    },
  );

  it("is offered to a voice Vapi agent", () => {
    renderWith("vapi");
    expect(screen.getByText("Sync with provider")).toBeInTheDocument();
  });

  it("is not offered to a LiveKit agent", () => {
    renderWith("livekit");
    expect(screen.queryByText("Sync with provider")).not.toBeInTheDocument();
  });
});
