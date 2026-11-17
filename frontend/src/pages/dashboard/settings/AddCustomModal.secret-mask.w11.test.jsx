import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AddCustomModal from "./AddCustomModal";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  snackbar: vi.fn(),
  track: vi.fn(),
  debug: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post, get: mocks.get, patch: vi.fn() },
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
  trackEvent: mocks.track,
  Events: { customModelAdded: "customModelAdded" },
  PropertyName: { formFields: "formFields" },
}));
vi.mock("src/utils/logger", () => ({
  default: { debug: mocks.debug, error: vi.fn(), info: vi.fn(), warn: vi.fn() },
}));
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

// Synthetic value only; never a real credential.
const SECRET = "test-secret-placeholder-w11";

function renderDrawer() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AddCustomModal open onClose={onClose} onRefresh={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onClose };
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

function serialized(calls) {
  const seen = new WeakSet();
  return JSON.stringify(calls, (_key, value) => {
    if (typeof value === "object" && value !== null) {
      if (seen.has(value)) return "[circular]";
      seen.add(value);
    }
    return value;
  });
}

function expectHidden(input) {
  expect(input).toHaveAttribute("type", "password");
  expect(input).toHaveAttribute("autocomplete", "new-password");
  expect(document.body.textContent).not.toContain(SECRET);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.post.mockResolvedValue({ data: { data: { id: "created-model" } } });
});

describe("Add Model hides API keys while typing (J25.FA-003 F7)", () => {
  it.each([
    ["Open AI", "API Key"],
    ["Azure", "API Key"],
    ["AWS Bedrock", "Secret Access Key"],
    ["AWS Sagemaker", "Secret Access Key"],
  ])(
    "%s masks the %s field and reveals it only on demand",
    async (provider, label) => {
      renderDrawer();
      await selectProvider(provider);
      expectHidden(screen.getByLabelText(label));

      await change(label, SECRET);
      const input = screen.getByLabelText(label);
      expect(input).toHaveValue(SECRET);
      expectHidden(input);

      fireEvent.click(screen.getByRole("button", { name: `Show ${label}` }));
      expect(screen.getByLabelText(label)).toHaveAttribute("type", "text");
      expect(screen.getByLabelText(label)).toHaveValue(SECRET);

      fireEvent.click(screen.getByRole("button", { name: `Hide ${label}` }));
      expectHidden(screen.getByLabelText(label));
      expect(screen.getByLabelText(label)).toHaveValue(SECRET);
    },
  );

  it.each([
    ["Open AI", ["Model Name", "Enter Base URL (optional)"]],
    ["Azure", ["API Base"]],
    ["AWS Bedrock", ["Access Key ID", "Region Name"]],
  ])("%s keeps identifiers and URLs readable", async (provider, labels) => {
    renderDrawer();
    await selectProvider(provider);
    for (const label of labels) {
      expect(screen.getByLabelText(label)).toHaveAttribute("type", "text");
    }
  });

  it("masks every custom model header value with its own toggle", async () => {
    renderDrawer();
    fireEvent.click(screen.getByLabelText("Configure Custom Model"));
    const keys = await screen.findAllByLabelText("Custom Key");
    const values = screen.getAllByLabelText("Custom Value");
    expect(values).toHaveLength(keys.length);
    const secretRows = values.filter((_v, i) => keys[i].value !== "api_base");
    expect(secretRows.length).toBeGreaterThan(0);
    secretRows.forEach((input) => expectHidden(input));

    const [header] = secretRows;
    await act(async () => {
      fireEvent.change(header, { target: { value: SECRET } });
    });
    expect(header).toHaveValue(SECRET);
    expectHidden(header);

    const field = within(header.parentElement);
    fireEvent.click(field.getByRole("button", { name: "Show Custom Value" }));
    expect(header).toHaveAttribute("type", "text");
    fireEvent.click(field.getByRole("button", { name: "Hide Custom Value" }));
    expectHidden(header);
  });

  it("stays disabled until the key is set, submits the key, and never logs or tracks it", async () => {
    const { onClose } = renderDrawer();
    await selectProvider("Open AI");
    await fillModel();
    const button = screen.getByRole("button", { name: "Add custom model" });
    await waitFor(() => expect(button).toBeDisabled());

    await change("API Key", SECRET);
    await submit();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));

    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post.mock.calls[0][1].model_provider).toBe("openai");
    expect(mocks.post.mock.calls[0][1].config_json.key).toBe(SECRET);
    expect(
      mocks.track.mock.calls.some(([event]) => event === "customModelAdded"),
    ).toBe(true);
    expect(serialized(mocks.track.mock.calls)).not.toContain(SECRET);
    expect(serialized(mocks.debug.mock.calls)).not.toContain(SECRET);
  });

  it("keeps the masked key after a failed creation", async () => {
    mocks.post.mockRejectedValue(new Error("Model validation failed"));
    const { onClose } = renderDrawer();
    await selectProvider("Open AI");
    await fillModel();
    await change("API Key", SECRET);
    await submit();
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add custom model" }),
      ).toBeEnabled(),
    );
    expect(onClose).not.toHaveBeenCalled();
    const input = screen.getByLabelText("API Key");
    expect(input).toHaveValue(SECRET);
    expectHidden(input);
    expect(serialized(mocks.debug.mock.calls)).not.toContain(SECRET);
  });

  it("does not echo a key typed in the Form tab on the JSON tab", async () => {
    renderDrawer();
    await selectProvider("Open AI");
    await change("API Key", SECRET);
    fireEvent.click(screen.getByRole("tab", { name: "JSON" }));
    const editor = await screen.findByLabelText("openAiCredentialJson");
    expect(editor.value ?? "").not.toContain(SECRET);
    expect(document.body.textContent).not.toContain(SECRET);
  });
});

// Synthetic service-account-shaped credentials; the key is a placeholder.
const PRIVATE_KEY = "test-private-key-placeholder-w11";
const SERVICE_ACCOUNT = {
  project_id: "test-project-w11",
  private_key: PRIVATE_KEY,
  client_email: "test-w11@example.invalid",
};
const SERVICE_ACCOUNT_JSON = JSON.stringify(SERVICE_ACCOUNT, null, 2);

function vertexEditor() {
  return screen.getByLabelText("vertexCredentialJson");
}

function vertexRegion() {
  return vertexEditor().closest("[data-secret]");
}

describe("Add Model hides service-account JSON until revealed (J25.FA-003 F7 C1)", () => {
  it("Vertex AI keeps pasted credentials hidden until Show Vertex Credentials", async () => {
    renderDrawer();
    await selectProvider("Vertex AI");
    expect(vertexRegion()).toHaveAttribute("data-secret", "hidden");

    await change("vertexCredentialJson", SERVICE_ACCOUNT_JSON);
    expect(vertexEditor()).toHaveValue(SERVICE_ACCOUNT_JSON);
    expect(vertexRegion()).toHaveAttribute("data-secret", "hidden");

    fireEvent.click(
      within(vertexRegion()).getByRole("button", {
        name: "Show Vertex Credentials",
      }),
    );
    expect(vertexRegion()).toHaveAttribute("data-secret", "shown");
    expect(vertexEditor()).toHaveValue(SERVICE_ACCOUNT_JSON);

    fireEvent.click(
      within(vertexRegion()).getByRole("button", {
        name: "Hide Vertex Credentials",
      }),
    );
    expect(vertexRegion()).toHaveAttribute("data-secret", "hidden");
    expect(vertexEditor()).toHaveValue(SERVICE_ACCOUNT_JSON);
  });

  it("Vertex AI keeps the hidden credentials after a failed creation", async () => {
    mocks.post.mockRejectedValue(new Error("Model validation failed"));
    const { onClose } = renderDrawer();
    await selectProvider("Vertex AI");
    await fillModel();
    await change("vertexCredentialJson", SERVICE_ACCOUNT_JSON);
    await submit();
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add custom model" }),
      ).toBeEnabled(),
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(vertexEditor()).toHaveValue(SERVICE_ACCOUNT_JSON);
    expect(vertexRegion()).toHaveAttribute("data-secret", "hidden");
    expect(serialized(mocks.debug.mock.calls)).not.toContain(PRIVATE_KEY);
  });

  it("Vertex AI stays disabled until credentials are pasted, submits them unchanged, and never logs or tracks them", async () => {
    const { onClose } = renderDrawer();
    await selectProvider("Vertex AI");
    await fillModel();
    const button = screen.getByRole("button", { name: "Add custom model" });
    await waitFor(() => expect(button).toBeDisabled());

    const location = screen.getByLabelText("Location (optional)");
    expect(location).toHaveAttribute("type", "text");
    expect(location.closest("[data-secret]")).toBeNull();

    await change("vertexCredentialJson", SERVICE_ACCOUNT_JSON);
    await change("Location (optional)", "us-central1");
    await submit();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));

    expect(mocks.post).toHaveBeenCalledTimes(1);
    const body = mocks.post.mock.calls[0][1];
    expect(body.model_provider).toBe("vertex_ai");
    expect(body.config_json).toEqual({
      ...SERVICE_ACCOUNT,
      location: "us-central1",
    });
    expect(serialized(mocks.track.mock.calls)).not.toContain(PRIVATE_KEY);
    expect(serialized(mocks.debug.mock.calls)).not.toContain(PRIVATE_KEY);
  });
});
