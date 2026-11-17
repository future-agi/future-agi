import React from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FormProvider, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import AgentVoiceForm from "src/sections/agents/CreateNewAgent/AgentConfigurationStep/AgentVoiceForm";
import {
  createAgentDefinitionSchema,
  defaultAgentDefinitionValues,
} from "src/sections/agents/helper";
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner", orgLimit: {} }),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(), post: vi.fn() },
}));
function VoiceForm() {
  const methods = useForm({
    mode: "onSubmit",
    resolver: zodResolver(createAgentDefinitionSchema(), undefined, {
      mode: "async",
    }),
    defaultValues: {
      ...defaultAgentDefinitionValues,
      agentType: "voice",
      agentName: "Fixture agent",
      provider: "vapi",
      authenticationMethod: "api_key",
      inbound: true,
      apiKey: "fixture-key",
      assistantId: "fixture-agent",
      description: "Fixture instructions",
      commitMessage: "Fixture version",
      observabilityEnabled: false,
    },
  });
  return (
    <FormProvider {...methods}>
      <AgentVoiceForm />
    </FormProvider>
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({ data: {} });
});
it("turning the create-voice preference on sends both validation requests through the production resolver", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <VoiceForm />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "Observability" }));
  await waitFor(() =>
    expect(axios.post).toHaveBeenCalledWith(
      endpoints.agentDefinitions.verifyApiKey,
      { provider: "vapi", api_key: "fixture-key" },
    ),
  );
  await waitFor(() =>
    expect(axios.post).toHaveBeenCalledWith(
      endpoints.agentDefinitions.verifyAssistantId,
      {
        provider: "vapi",
        api_key: "fixture-key",
        assistant_id: "fixture-agent",
      },
    ),
  );
  expect(axios.post).toHaveBeenCalledTimes(2);
});
