import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  bodyDefinitionOf,
  definitionSchema,
  unknownFields,
} from "src/api/contracts/strictBody";
import CreateModelModal from "./CreateModelModal";

const mocks = vi.hoisted(() => ({ post: vi.fn(), snackbar: vi.fn() }));
const createPath = "/model-hub/custom_models/create/";

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    model: { create: "/model-hub/custom_models/create/" },
    settings: {
      customModal: {
        createCustomModal: "/model-hub/custom_models/create/",
        editCustomModel: "/model-hub/custom_models/edit/",
      },
    },
  },
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.snackbar }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.snackbar }));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: { formFields: "formFields" },
}));
vi.mock("src/utils/logger", () => ({ default: { debug: vi.fn() } }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/image", () => ({ default: () => null }));
// Monaco needs a browser worker; keep react-hook-form's real controlled value.
vi.mock("src/components/form-code-editor", async () => {
  const { Controller } = await import("react-hook-form");
  return {
    // eslint-disable-next-line react/prop-types
    FormCodeEditor: ({ control, fieldName }) => (
      <Controller
        control={control}
        name={fieldName}
        render={({ field }) => <textarea aria-label={fieldName} {...field} />}
      />
    ),
  };
});

function renderModal() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const onClose = vi.fn();
  const view = (open) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CreateModelModal open={open} onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const rendered = render(view(true));
  return {
    onClose,
    invalidate,
    setOpen: (open) => rendered.rerender(view(open)),
  };
}

async function change(label, value) {
  await act(async () => {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  });
}

async function selectProvider(label) {
  fireEvent.click(screen.getByLabelText(/Model Provider/));
  fireEvent.click(await screen.findByText(label, { exact: true }));
}

async function fillModel() {
  await change("Model Name", "provider-model-id");
  await change("Input Token Cost Per Million Tokens", "0");
  await change("Output Token Cost Per Million Tokens", "1.5");
}

async function submit() {
  const button = screen.getByRole("button", { name: "Add custom model" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
}

function expectContractPayload(provider, config) {
  expect(mocks.post).toHaveBeenCalledTimes(1);
  const [url, body] = mocks.post.mock.calls[0];
  expect(url).toBe(createPath);
  expect(body).toEqual({
    model_provider: provider,
    model_name: "provider-model-id",
    input_token_cost: 0,
    output_token_cost: 1.5,
    config_json: config,
  });
  const definition = bodyDefinitionOf("post", url);
  expect(definition).toBe("CustomAIModelCreateRequest");
  expect(unknownFields(body, definition)).toEqual([]);
  for (const field of definitionSchema(definition).required) {
    expect(body[field]).toBeTruthy();
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.post.mockResolvedValue({ data: { data: { id: "created-model" } } });
});

describe("Models Add Model request (J25.FA-003)", () => {
  it("collects a provider, model id, credentials and prices, then refreshes Models", async () => {
    const { onClose, invalidate } = renderModal();
    await selectProvider("Open AI");
    await fillModel();
    await change("API Key", "test-placeholder");
    await change("Enter Base URL (optional)", "https://provider.example/v1");
    await submit();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expectContractPayload("openai", {
      key: "test-placeholder",
      api_base: "https://provider.example/v1",
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["models"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["customModals"] });
  });

  it.each([
    ["AWS Bedrock", "bedrock"],
    ["AWS Sagemaker", "sagemaker"],
  ])("submits the %s credential configuration", async (label, provider) => {
    const { onClose } = renderModal();
    await selectProvider(label);
    await fillModel();
    await change("Access Key ID", "test-access-placeholder");
    await change("Secret Access Key", "test-secret-placeholder");
    await change("Region Name", "us-east-1");
    await submit();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expectContractPayload(provider, {
      aws_access_key_id: "test-access-placeholder",
      aws_secret_access_key: "test-secret-placeholder",
      aws_region_name: "us-east-1",
    });
  });

  it("submits the Azure endpoint and credential configuration", async () => {
    const { onClose } = renderModal();
    await selectProvider("Azure");
    await fillModel();
    fireEvent.click(screen.getByLabelText(/Azure Endpoint Type/));
    fireEvent.click(await screen.findByText("Azure AI Foundry / AI Studio"));
    await change("API Base", "https://provider.example");
    await change("API Key", "test-placeholder");
    await submit();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expectContractPayload("azure", {
      api_base: "https://provider.example",
      api_key: "test-placeholder",
      azure_endpoint_type: "foundry",
    });
  });

  it("submits parsed provider credential JSON rather than a JSON string", async () => {
    const { onClose } = renderModal();
    await selectProvider("Vertex AI");
    await fillModel();
    await change("vertexCredentialJson", '{"project_id":"test-project"}');
    await change("Location (optional)", "us-central1");
    await submit();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expectContractPayload("vertex_ai", {
      project_id: "test-project",
      location: "us-central1",
    });
  });

  it("does not submit without provider credentials or a token price", async () => {
    renderModal();
    await selectProvider("Open AI");
    await fillModel();
    const button = screen.getByRole("button", { name: "Add custom model" });
    await waitFor(() => expect(button).toBeDisabled());
    await change("API Key", "test-placeholder");
    await waitFor(() => expect(button).toBeEnabled());
    await change("Input Token Cost Per Million Tokens", "");
    await waitFor(() => expect(button).toBeDisabled());
    fireEvent.click(button);
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("keeps the form open and does not refresh Models when creation fails", async () => {
    mocks.post.mockRejectedValue(new Error("Model validation failed"));
    const { onClose, invalidate } = renderModal();
    await selectProvider("Open AI");
    await fillModel();
    await change("API Key", "test-placeholder");
    await submit();
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add custom model" }),
      ).toBeEnabled(),
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(screen.getByLabelText("API Key")).toHaveValue("test-placeholder");
  });

  it("clears provider credentials and prices when closed and reopened", async () => {
    const { setOpen } = renderModal();
    await selectProvider("Open AI");
    await fillModel();
    await change("API Key", "test-placeholder");
    setOpen(false);
    setOpen(true);
    await selectProvider("Open AI");
    expect(screen.getByLabelText("API Key")).toHaveValue("");
    expect(screen.getByLabelText("Model Name")).toHaveValue("");
    expect(
      screen.getByLabelText("Input Token Cost Per Million Tokens"),
    ).toHaveValue(null);
  });
});
