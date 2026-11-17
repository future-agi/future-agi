import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import AddProviderDialog from "./AddProviderDialog";
import ProviderManagementSection from "./ProviderManagementSection";
import { PROVIDER_MODEL_DISCOVERY_ENABLED } from "./providerDiscovery";

const { discover, hydrate, save, inventory } = vi.hoisted(() => ({
  discover: vi.fn(),
  hydrate: vi.fn(),
  save: vi.fn(),
  inventory: { config: {}, orgConfig: {} },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("./hooks/useGatewayConfig", () => ({
  gatewayConfigRefreshFailed: () => false,
  useGatewayConfig: () => ({ data: inventory.config }),
  useProviderHealth: () => ({ data: { providers: [] } }),
  useReloadConfig: () => ({ mutate: vi.fn() }),
  useUpdateProvider: () => ({ mutate: save, isPending: false }),
  useFetchProviderModels: () => ({
    isPending: false,
    mutate: (vars, callbacks) => {
      if (!vars.savedModelsOnly) return discover(vars, callbacks);
      hydrate(vars, callbacks);
    },
  }),
}));
vi.mock("./hooks/useOrgConfig", () => ({ useOrgConfig: () => ({ data: inventory.orgConfig }) }));
vi.mock("../context/useGatewayContext", () => ({ useGatewayContext: () => ({ gatewayId: "synthetic" }) }));
vi.mock("../hooks/useGatewayAdmin", () => ({ useGatewayAdmin: () => ({ canEdit: true }) }));
vi.mock("../components/GatewayAdminOnly", () => ({ default: ({ children }) => children }));
vi.mock("../components/frame", () => ({
  // eslint-disable-next-line react/prop-types
  GatewayPageFrame: ({ actions, children }) => <div>{actions}{children}</div>,
  GatewayEmptyState: () => null,
}));
vi.mock("./ProviderList", () => ({ default: () => null }));
vi.mock("./RoutingConfigView", () => ({ default: () => null }));
vi.mock("./CacheStatusView", () => ({ default: () => null }));
vi.mock("./ui/CreditBalanceChip", () => ({ default: () => null }));
vi.mock("./ui/FaIcon", () => ({ default: () => null }));

const withClient = (ui) => <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;
const show = (provider, props = {}) => render(withClient(
  <AddProviderDialog open onClose={vi.fn()} gatewayId="synthetic" provider={provider} {...props} />,
));
const pickPreset = async (label) => {
  await userEvent.click(screen.getByLabelText(/^Provider/));
  await userEvent.click(screen.getByRole("option", { name: label }));
};
const addModel = async (model) => {
  fireEvent.change(screen.getByLabelText("Model ID"), { target: { value: model } });
  await userEvent.click(screen.getByRole("button", { name: "Add model" }));
};
const pastDebounce = () => act(() => new Promise((resolve) => setTimeout(resolve, 700)));

beforeEach(() => {
  discover.mockReset();
  hydrate.mockReset();
  hydrate.mockImplementation((_vars, callbacks) => {
    setTimeout(() => callbacks.onSuccess({ models: ["gpt-4o"] }), 0);
  });
  save.mockReset();
  inventory.config = {};
  inventory.orgConfig = {};
});

const advancedToggle = () => screen.getByRole("button", { name: "Advanced connection settings" });
const openAdvanced = async () => {
  if (advancedToggle().getAttribute("aria-expanded") === "false") {
    await userEvent.click(advancedToggle());
  }
};

describe("Advanced connection settings", () => {
  it("starts common presets collapsed with credentials and manual models visible", async () => {
    show();
    expect(advancedToggle()).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByLabelText(/^API Key/)).toBeVisible();
    expect(screen.getByLabelText("Model ID")).toBeVisible();
    expect(screen.getByRole("button", { name: "Add model" })).toBeVisible();
    for (const label of ["Base URL", "API Format", "API Path Prefix", "Timeout", "Max Concurrent"]) {
      expect(screen.getByLabelText(label)).not.toBeVisible();
    }
    expect(screen.getByText(/Default settings.*openai.*Path prefix: \/v1.*Timeout: gateway default.*Concurrency: gateway default/)).toBeVisible();
    await openAdvanced();
    expect(screen.getByLabelText("Timeout")).toBeVisible();
    await userEvent.click(advancedToggle());
    await pastDebounce();
    expect(save).not.toHaveBeenCalled();
    expect(discover).not.toHaveBeenCalled();
    expect(hydrate).not.toHaveBeenCalled();
  });

  it("preserves custom timeout, concurrency, path and format across collapse and saves the unchanged defaults", async () => {
    show();
    fireEvent.change(screen.getByLabelText(/^API Key/), { target: { value: "synthetic-provider-key" } });
    await addModel("manual-model");
    await openAdvanced();
    fireEvent.change(screen.getByLabelText("Timeout"), { target: { value: "45s" } });
    fireEvent.change(screen.getByLabelText("Max Concurrent"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("API Path Prefix"), { target: { value: "/team/v2" } });
    await userEvent.click(screen.getByLabelText("API Format"));
    await userEvent.click(screen.getByRole("option", { name: "anthropic" }));
    await userEvent.click(advancedToggle());
    expect(screen.getByText(/Custom settings.*API format: anthropic.*Timeout: 45s.*Concurrency: 7/)).toBeVisible();
    expect(screen.queryByLabelText("API Path Prefix")).toBeNull();
    expect(save).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(save).toHaveBeenCalledOnce();
    expect(save.mock.calls[0][0].config).toEqual({
      base_url: "https://api.openai.com/v1", api_format: "anthropic",
      api_key: "synthetic-provider-key", models: ["manual-model"],
      default_timeout: 45, max_concurrent: 7,
    });
    // The existing wire contract omits a prefix for non-OpenAI formats, but
    // changing format back must recover the draft prefix, even after collapse.
    await openAdvanced();
    await userEvent.click(screen.getByLabelText("API Format"));
    await userEvent.click(screen.getByRole("option", { name: "openai" }));
    expect(screen.getByLabelText("API Path Prefix")).toHaveValue("/team/v2");
    await userEvent.click(advancedToggle());
    expect(screen.getByText(/Custom settings.*Path prefix: \/team\/v2/)).toBeVisible();
    expect(save).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1][0].config).toEqual({
      ...save.mock.calls[0][0].config, api_format: "openai", api_path_prefix: "/team/v2",
    });
    await pastDebounce();
    expect(discover).not.toHaveBeenCalled();
    expect(hydrate).not.toHaveBeenCalled();
  });

  it("saves preset defaults without ever opening Advanced", async () => {
    show();
    fireEvent.change(screen.getByLabelText(/^API Key/), { target: { value: "synthetic-provider-key" } });
    await addModel("manual-model");
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(advancedToggle()).toHaveAttribute("aria-expanded", "false");
    expect(save.mock.calls[0][0].config).toEqual({
      base_url: "https://api.openai.com/v1", api_format: "openai", api_path_prefix: "/v1",
      api_key: "synthetic-provider-key", models: ["manual-model"],
    });
  });

  it.each([
    ["Base URL", "not-a-url"],
    ["Timeout", "soon"],
  ])("reveals and focuses an invalid hidden %s without saving", async (label, value) => {
    show();
    fireEvent.change(screen.getByLabelText(/^API Key/), { target: { value: "synthetic-provider-key" } });
    await addModel("manual-model");
    await openAdvanced();
    const field = screen.getByLabelText(label);
    fireEvent.change(field, { target: { value } });
    await userEvent.click(advancedToggle());
    expect(field).not.toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(advancedToggle()).toHaveAttribute("aria-expanded", "true");
    expect(field).toBeVisible();
    expect(field).toHaveFocus();
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(save).not.toHaveBeenCalled();
    expect(discover).not.toHaveBeenCalled();
  });

  it.each(["Custom / Self-hosted", "Azure OpenAI", "Custom System One endpoint"])(
    "keeps the required endpoint visible for %s", async (label) => {
      show();
      await pickPreset(label);
      expect(advancedToggle()).toHaveAttribute("aria-expanded", "false");
      expect(screen.getByLabelText(/^Base URL/)).toBeVisible();
      expect(screen.getByLabelText(/^Base URL/)).toBeRequired();
      expect(screen.getByLabelText(/^API Key/)).toBeVisible();
      expect(screen.getByLabelText("Model ID")).toBeVisible();
      if (label === "Custom / Self-hosted") expect(screen.getByLabelText(/^Name/)).toBeVisible();
      await openAdvanced();
      await userEvent.click(advancedToggle());
      expect(screen.getByLabelText(/^Base URL/)).toBeVisible();
    },
  );

  it.each([
    ["AWS Bedrock", [/^AWS Region/, /^AWS Access Key ID/, /^AWS Secret Access Key/, /^AWS Session Token/]],
    ["Google Vertex AI", [/^Google Cloud project ID/, /^Vertex AI location/, /^Service-account JSON/]],
  ])("keeps %s credentials and region controls visible", async (label, fields) => {
    show();
    await pickPreset(label);
    expect(advancedToggle()).toHaveAttribute("aria-expanded", "false");
    fields.forEach((field) => expect(screen.getByLabelText(field)).toBeVisible());
    await openAdvanced();
    await userEvent.click(advancedToggle());
    fields.forEach((field) => expect(screen.getByLabelText(field)).toBeVisible());
    expect(screen.getByLabelText("Model ID")).toBeVisible();
    expect(screen.queryByLabelText(/^API Key/)).toBeNull();
    expect(discover).not.toHaveBeenCalled();
  });

  it("resets expansion and drafts on preset changes and reopening the dialog", async () => {
    const props = { onClose: vi.fn(), gatewayId: "synthetic" };
    const view = render(withClient(<AddProviderDialog {...props} open />));
    await openAdvanced();
    fireEvent.change(screen.getByLabelText("Timeout"), { target: { value: "91" } });
    await pickPreset("DeepInfra");
    expect(advancedToggle()).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByLabelText("Timeout")).toHaveValue("");
    expect(screen.getByText(/Default settings.*Path prefix: \/v1\/openai/)).toBeVisible();
    await openAdvanced();
    view.rerender(withClient(<AddProviderDialog {...props} open={false} />));
    view.rerender(withClient(<AddProviderDialog {...props} open />));
    expect(advancedToggle()).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByLabelText(/^Provider/)).toHaveTextContent("OpenAI");
    expect(save).not.toHaveBeenCalled();
    expect(discover).not.toHaveBeenCalled();
  });

  it("summarizes and preserves saved overrides while resetting expansion for a different edit target", async () => {
    const props = { onClose: vi.fn(), gatewayId: "synthetic" };
    const first = { name: "openai", config: { base_url: "https://saved.example.test", api_format: "openai", api_path_prefix: "/team", default_timeout: 42, max_concurrent: 7 } };
    const second = { name: "groq", config: { base_url: "https://api.groq.com/openai", api_format: "openai", default_timeout: 60 } };
    const view = render(withClient(<AddProviderDialog {...props} open provider={first} />));
    await screen.findByText("gpt-4o");
    expect(advancedToggle()).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText(/Custom settings.*Base URL: custom.*Path prefix: \/team.*Timeout: 42s.*Concurrency: 7/)).toBeVisible();
    await openAdvanced();
    await userEvent.click(advancedToggle());
    expect(hydrate).toHaveBeenCalledTimes(1);
    expect(save).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(save.mock.calls[0][0].config).toEqual({ ...first.config, models: ["gpt-4o"] });
    await openAdvanced();
    view.rerender(withClient(<AddProviderDialog {...props} open provider={second} />));
    expect(advancedToggle()).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText(/Timeout: 60s/)).toBeVisible();
    expect(discover).not.toHaveBeenCalled();
  });
});

describe("launch discovery OFF", () => {
  it("uses honest manual-entry copy and saves without a discovery request", async () => {
    expect(PROVIDER_MODEL_DISCOVERY_ENABLED).toBe(false);
    show();
    expect(screen.getByText(/Provider model discovery is disabled/)).toBeTruthy();
    expect(screen.queryByPlaceholderText(/Enter API key to load models/)).toBeNull();
    expect(screen.queryByText(/coming soon/i)).toBeNull();
    await userEvent.type(screen.getByLabelText(/API Key/i), "bravotest-4826-maple");
    await userEvent.type(screen.getByPlaceholderText(/model IDs/i), "gpt-4o{enter}");
    await pastDebounce();
    expect(discover).not.toHaveBeenCalled();
    expect(hydrate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(save).toHaveBeenCalledOnce();
    expect(save.mock.calls[0][0].config).toMatchObject({
      api_key: "bravotest-4826-maple", models: ["gpt-4o"],
    });
  });

  it("hydrates saved IDs and saves a replacement key without probing its URL", async () => {
    show({ name: "openai", config: { models: ["untrusted-legacy-id"], default_timeout: 30 } });
    await screen.findByText("gpt-4o");
    expect(screen.queryByText("untrusted-legacy-id")).toBeNull();
    await userEvent.type(screen.getByLabelText(/API Key/i), "bravotest-4826-maple");
    await pastDebounce();
    expect(discover).not.toHaveBeenCalled();
    expect(hydrate).toHaveBeenCalledWith({ providerName: "openai", savedModelsOnly: true }, expect.any(Object));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save Changes" })).not.toBeDisabled());
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(save.mock.calls[0][0].config).toMatchObject({
      api_key: "bravotest-4826-maple", models: ["gpt-4o"],
    });
  });

  it("adds a single pasted ID with an explicit action, counts it and never claims live discovery", async () => {
    show();
    expect(screen.getByRole("status")).toHaveTextContent("0 selected");
    expect(screen.getByRole("button", { name: "Add provider" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add model" })).toBeDisabled();
    expect(screen.queryByText(/fetched live from the provider/)).toBeNull();
    expect(screen.getByRole("link", { name: "Model ID documentation" })).toHaveAttribute(
      "href", "https://platform.openai.com/docs/api-reference",
    );
    await userEvent.type(screen.getByLabelText(/API Key/i), "bravotest-4826-maple");
    const input = screen.getByLabelText("Model ID");
    await userEvent.click(input);
    await userEvent.paste("my-account-model");
    expect(screen.getByRole("status")).toHaveTextContent("0 selected");
    await userEvent.click(screen.getByRole("button", { name: "Add model" }));
    expect(screen.getByRole("status")).toHaveTextContent("1 selected");
    expect(input).toHaveValue("");
    expect(screen.getByRole("button", { name: "Add provider" })).toBeEnabled();
    await pastDebounce();
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(save.mock.calls[0][0].config.models).toEqual(["my-account-model"]);
    expect(discover).not.toHaveBeenCalled();
    expect(hydrate).not.toHaveBeenCalled();
  });

  it("commits comma/newline bulk paste once per ID and updates the count after removal", async () => {
    show();
    await userEvent.type(screen.getByLabelText(/API Key/i), "bravotest-4826-maple");
    await addModel("model-a");
    await userEvent.click(screen.getByLabelText("Model ID"));
    await userEvent.paste("model-a, model-b\nmodel-c\r\n model-b, ");
    expect(screen.getByRole("status")).toHaveTextContent("3 selected");
    expect(screen.getByLabelText("Model ID")).toHaveValue("");
    await userEvent.keyboard("{Backspace}");
    expect(screen.getByRole("status")).toHaveTextContent("2 selected");
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(save.mock.calls[0][0].config.models).toEqual(["model-a", "model-b"]);
    expect(discover).not.toHaveBeenCalled();
  });

  it.each([
    ["AWS Bedrock", /AWS account.*selected region/],
    ["Google Vertex AI", /Google Cloud project and location/],
    ["Azure OpenAI", /deployment names.*Azure resource/],
  ])("gives account-specific model guidance for %s", async (label, guidance) => {
    show();
    await pickPreset(label);
    expect(screen.getByText(guidance)).toBeInTheDocument();
    expect(screen.getByText(/Adding an ID does not verify access/)).toBeInTheDocument();
    expect(discover).not.toHaveBeenCalled();
  });
});

describe("connected presets", () => {
  it.each(["config", "orgConfig"])("opens the saved %s connection for explicit editing from the Providers page", async (source) => {
    inventory[source] = { providers: { groq: {
      base_url: "https://saved.example.test/v1", api_format: "openai",
      api_path_prefix: "/saved", default_timeout: 42, max_concurrent: 7,
      models: ["untrusted-legacy-id"],
    } } };
    render(withClient(<ProviderManagementSection />));
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    await pickPreset(/Groq.*Already connected/);
    expect(screen.getByText("Edit existing provider")).toBeInTheDocument();
    expect(screen.getByText(/Nothing changes until you save/)).toBeInTheDocument();
    expect(advancedToggle()).toHaveAttribute("aria-expanded", "false");
    await openAdvanced();
    expect(screen.getByLabelText(/^Base URL/)).toHaveValue("https://saved.example.test/v1");
    expect(screen.getByLabelText("API Path Prefix")).toHaveValue("/saved");
    expect(screen.getByLabelText("Timeout")).toHaveValue("42");
    expect(screen.getByLabelText("Max Concurrent")).toHaveValue(7);
    expect(screen.getByLabelText(/^API Key/)).toHaveValue("");
    await screen.findByText("gpt-4o");
    expect(screen.queryByText("untrusted-legacy-id")).toBeNull();
    expect(save).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(save).toHaveBeenCalledOnce();
    expect(save.mock.calls[0][0]).toMatchObject({ name: "groq", config: {
      base_url: "https://saved.example.test/v1", default_timeout: 42,
      max_concurrent: 7, models: ["gpt-4o"],
    } });
    expect(save.mock.calls[0][0].config).not.toHaveProperty("api_key");
    expect(discover).not.toHaveBeenCalled();
  });

  it("treats the initially selected OpenAI preset as an edit, never a blank replacement", async () => {
    show(undefined, { existingProviders: { openai: { base_url: "https://saved.example.test", default_timeout: 45 } } });
    expect(screen.getByText("Edit existing provider")).toBeInTheDocument();
    expect(screen.getByText(/Timeout: 45s/)).toBeVisible();
    await openAdvanced();
    expect(screen.getByLabelText("Timeout")).toHaveValue("45");
    await screen.findByText("gpt-4o");
    expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
    expect(save).not.toHaveBeenCalled();
  });

  it.each(["Groq", "Custom / Self-hosted"])("switches from an existing connection to a new %s without carrying its settings", async (label) => {
    show(undefined, { existingProviders: { openai: {
      base_url: "https://saved.example.test", default_timeout: 45, max_concurrent: 9,
    } } });
    await screen.findByText("gpt-4o");
    await pickPreset(label);
    expect(advancedToggle()).toHaveAttribute("aria-expanded", "false");
    await openAdvanced();
    expect(screen.getByLabelText("Timeout")).toHaveValue("");
    expect(screen.getByLabelText("Max Concurrent")).toHaveValue(null);
    expect(screen.getByRole("status")).toHaveTextContent("0 selected");
    if (label === "Custom / Self-hosted") {
      fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: "team-endpoint" } });
      fireEvent.change(screen.getByLabelText(/^Base URL/), { target: { value: "https://team.example.test" } });
    }
    await userEvent.type(screen.getByLabelText(/API Key/i), "bravotest-4826-maple");
    await addModel("team-model");
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(save).toHaveBeenCalledOnce();
    expect(save.mock.calls[0][0].name).toBe(label === "Groq" ? "groq" : "team-endpoint");
    expect(save.mock.calls[0][0].config.models).toEqual(["team-model"]);
    expect(save.mock.calls[0][0].config).not.toHaveProperty("default_timeout");
    expect(discover).not.toHaveBeenCalled();
  });

  it("cannot overwrite a connected preset when only its name is available", async () => {
    show(undefined, { existingNames: ["openai"] });
    await userEvent.type(screen.getByLabelText(/API Key/i), "bravotest-4826-maple");
    await userEvent.type(screen.getByPlaceholderText(/model IDs/i), "gpt-4o{enter}");
    expect(screen.getByRole("button", { name: "Add provider" })).toBeDisabled();
    expect(screen.getAllByText(/Already connected/).length).toBeGreaterThan(0);
    expect(save).not.toHaveBeenCalled();
  });

  it("ignores a saved-model response after switching to an unconnected preset", async () => {
    let pending;
    hydrate.mockImplementation((_vars, callbacks) => { pending = callbacks; });
    show(undefined, { existingProviders: { openai: { base_url: "https://saved.example.test" } } });
    await pickPreset("Groq");
    await addModel("team-model");
    act(() => pending.onSuccess({ models: ["old-provider-model"] }));
    expect(screen.queryByText("old-provider-model")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("1 selected");
  });
});
