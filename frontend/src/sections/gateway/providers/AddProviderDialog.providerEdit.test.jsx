import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, userEvent } from "src/utils/test-utils";
import AddProviderDialog from "./AddProviderDialog";
import { PROVIDER_PRESETS } from "./byok-presets";

vi.mock("./providerDiscovery", () => ({ PROVIDER_MODEL_DISCOVERY_ENABLED: false }));
const { updateMutate, savedModels } = vi.hoisted(() => ({
  updateMutate: vi.fn(),
  savedModels: { value: ["saved-model"], fail: false, calls: [] },
}));
vi.mock("./hooks/useGatewayConfig", () => ({
  gatewayConfigRefreshFailed: () => false,
  useUpdateProvider: () => ({ mutate: updateMutate, isPending: false }),
  useFetchProviderModels: () => ({
    isPending: false,
    mutate: (vars, opts) => {
      savedModels.calls.push(vars);
      if (vars.savedModelsOnly) {
        if (savedModels.fail) opts?.onError?.(new Error("Saved read failed"));
        else opts?.onSuccess?.({ models: savedModels.value });
      }
    },
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const renderDialog = (props = {}) => {
  const client = new QueryClient();
  const ui = (next) => (
    <QueryClientProvider client={client}>
      <AddProviderDialog open onClose={vi.fn()} gatewayId="gw-1" {...next} />
    </QueryClientProvider>
  );
  const view = render(ui(props));
  return { ...view, reopen: (next) => view.rerender(ui(next)) };
};
const select = async (label, option) => {
  await userEvent.click(screen.getByLabelText(label));
  await userEvent.click(screen.getByRole("option", { name: option }));
};
const change = (label, value) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const addModel = () => {
  const input = screen.getByPlaceholderText(/model IDs/i);
  fireEvent.change(input, { target: { value: "manual-model" } });
  fireEvent.click(screen.getByRole("button", { name: "Add model" }));
};
const save = (name = "Add provider") => {
  fireEvent.click(screen.getByRole("button", { name }));
  expect(updateMutate).toHaveBeenCalledOnce();
  return updateMutate.mock.calls[0][0].config;
};

beforeEach(() => {
  updateMutate.mockReset();
  savedModels.value = ["saved-model"];
  savedModels.fail = false;
  savedModels.calls = [];
});

it("R3-KEYS-ONBOARDING-04 blocks partial replacement after failed hydration and retries only the saved projection", () => {
  savedModels.fail = true;
  savedModels.value = ["saved-one", "saved-two"];
  renderDialog({ provider: { name: "openai", config: {
    base_url: "https://api.openai.com", models: ["saved-one", "saved-two"],
  } } });
  addModel();
  expect(screen.getByRole("button", { name: "Save Changes" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
  expect(updateMutate).not.toHaveBeenCalled();
  expect(screen.getByText(/Saving is blocked to avoid replacing/)).toBeInTheDocument();
  savedModels.fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry saved models" }));
  expect(savedModels.calls).toEqual([
    { providerName: "openai", savedModelsOnly: true },
    { providerName: "openai", savedModelsOnly: true },
  ]);
  expect(screen.queryByRole("button", { name: "Retry saved models" })).toBeNull();
  expect(save("Save Changes").models).toEqual(["manual-model", "saved-one", "saved-two"]);
});

it("R3-KEYS-ONBOARDING-05 preserves saved label and pool size without echoing opaque config", () => {
  renderDialog({ provider: { name: "openai", config: {
    base_url: "https://api.openai.com",
    display_name: "Production OpenAI",
    conn_pool_size: 20,
    extra_config: { unrelated: "do-not-echo" },
    api_key: "synthetic-redacted-key",
  } } });
  addModel();
  const config = save("Save Changes");
  expect(config).toMatchObject({ display_name: "Production OpenAI", conn_pool_size: 20 });
  expect(config).not.toHaveProperty("extra_config");
  expect(config).not.toHaveProperty("api_key");
});

describe("WALK3 provider protocol selection", () => {
  it("selects native Google rather than carrying OpenAI onto the native host", async () => {
    renderDialog();
    await select(/^Provider/, "Google (Gemini)");
    expect(screen.getByLabelText("API Format")).toHaveTextContent("google");
    change(/^API Key/, "fixture-google-key");
    addModel();
    const config = save();
    expect(config).toMatchObject({
      api_format: "google",
      base_url: "https://generativelanguage.googleapis.com",
      models: ["manual-model"],
    });
    expect(config).not.toHaveProperty("api_path_prefix");
  });

  it.each(["openai", "google"])("uses a complete Google endpoint tuple when switching to %s", async (format) => {
    renderDialog();
    await select(/^Provider/, "Google (Gemini)");
    await select("API Format", "openai");
    expect(screen.getByLabelText("API Path Prefix")).toHaveValue("/v1beta/openai");
    if (format === "google") await select("API Format", "google");
    change(/^API Key/, "fixture-google-key");
    addModel();
    const config = save();
    expect(config.base_url).toBe("https://generativelanguage.googleapis.com");
    expect(config.api_format).toBe(format);
    if (format === "openai") expect(config.api_path_prefix).toBe("/v1beta/openai");
    else expect(config).not.toHaveProperty("api_path_prefix");
  });

  it("does not carry Anthropic's protocol into the SigV4 Bedrock adapter", async () => {
    renderDialog();
    await select(/^Provider/, "Anthropic");
    expect(screen.getByLabelText("API Format")).toHaveTextContent("anthropic");
    await select(/^Provider/, "AWS Bedrock");
    expect(PROVIDER_PRESETS.bedrock.supportedFormats).toEqual(["bedrock"]);
    expect(screen.getByLabelText("API Format")).toHaveTextContent("bedrock");
    change(/^AWS Access Key ID/, "fixture-access");
    change(/^AWS Secret Access Key/, "fixture-secret");
    addModel();
    expect(save()).toMatchObject({
      api_format: "bedrock",
      base_url: "https://bedrock-runtime.us-east-1.amazonaws.com",
      aws_region: "us-east-1",
    });
  });

  it("hydrates an already-connected Google tuple without replacing saved customization", async () => {
    renderDialog({
      existingProviders: {
        google: {
          api_format: "openai",
          base_url: "https://google-proxy.example.test",
          api_path_prefix: "/custom/openai",
          default_timeout: 30,
        },
      },
    });
    await select(/^Provider/, /Google \(Gemini\).*Already connected/);
    expect(updateMutate).not.toHaveBeenCalled();
    change("Timeout", "45");
    expect(save("Save Changes")).toMatchObject({
      api_format: "openai",
      base_url: "https://google-proxy.example.test",
      api_path_prefix: "/custom/openai",
      default_timeout: 45,
    });
  });
});

describe("WALK3 Bedrock edit preservation", () => {
  it("repairs a legacy Bedrock connection saved with the Anthropic adapter", () => {
    renderDialog({ provider: {
      name: "bedrock",
      config: { api_format: "anthropic", base_url: "https://bedrock-runtime.eu-west-1.amazonaws.com" },
    } });
    expect(screen.getByLabelText("API Format")).toHaveTextContent("bedrock");
    expect(save("Save Changes")).toMatchObject({ api_format: "bedrock", aws_region: "eu-west-1" });
  });

  it.each([
    ["eu-west-1", "https://bedrock-runtime.eu-west-1.amazonaws.com"],
    ["cn-north-1", "https://bedrock-runtime.cn-north-1.amazonaws.com.cn"],
  ])("preserves %s after closing, reopening and changing only timeout", (region, baseUrl) => {
    const provider = { name: "bedrock", config: { api_format: "bedrock", base_url: baseUrl } };
    const view = renderDialog({ provider });
    view.reopen({ open: false, provider });
    view.reopen({ open: true, provider });
    expect(screen.getByLabelText(/^AWS Region/)).toHaveTextContent(region);
    change("Timeout", "45");
    expect(save("Save Changes")).toMatchObject({
      api_format: "bedrock", base_url: baseUrl, aws_region: region, default_timeout: 45,
    });
  });

  it("explains complete AWS key-pair replacement and omits a blank optional token", () => {
    renderDialog({ provider: {
      name: "bedrock",
      config: { api_format: "bedrock", base_url: "https://bedrock-runtime.eu-west-1.amazonaws.com" },
    } });
    expect(screen.getByText(/leave all credential fields blank to keep/i)).toBeInTheDocument();
    change(/^AWS Access Key ID/, "fixture-replacement-access");
    change(/^AWS Secret Access Key/, "fixture-replacement-secret");
    expect(screen.getByText(/blank session token removes the stored token/i)).toBeInTheDocument();
    const config = save("Save Changes");
    expect(config).toMatchObject({
      aws_access_key_id: "fixture-replacement-access",
      aws_secret_access_key: "fixture-replacement-secret",
      aws_region: "eu-west-1",
    });
    expect(config).not.toHaveProperty("aws_session_token");
  });
});
