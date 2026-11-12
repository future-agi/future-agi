// No provider key: every model-list row comes back unusable (credential_mode
// "unavailable"). The model menus then say what works, in one sentence. The
// credits part shows only when the organization's Future AGI credits are on
// (GET /agentcc/managed-models/: available and enabled), and names what the
// gateway serves: Try it, unless the gateway list says
// delegated_playground_available is false (then a gateway API key). Rows stay
// as they were: nothing becomes unselectable. The strings are byte for byte
// CREDITS-PICKER-COPY (the Prompts workbench shows the same ones).
import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";

const state = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => state.get(...args) },
  endpoints: {
    develop: { modelList: "/model-hub/api/models_list/" },
    gateway: {
      managedModels: "/agentcc/managed-models/",
      list: "/agentcc/gateways/",
    },
  },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
// Provider-logo loading is not part of this. jsdom does not supply the full
// IntersectionObserver browser API.
vi.mock("react-lazy-load-image-component", () => ({
  // eslint-disable-next-line react/prop-types
  LazyLoadImage: ({ alt, src, className, style }) => (
    <img alt={alt} src={src} className={className} style={style} />
  ),
}));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));
vi.mock("src/pages/dashboard/settings/AddCustomModal", () => ({
  default: () => null,
}));
vi.mock("src/components/custom-model-dropdown/EachCustomVoiceModel", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/RunPrompt/common", () => ({
  MODEL_TYPES: { LLM: "llm", STT: "stt", TTS: "tts", IMAGE: "image" },
}));
// The Agents form's own picker is not under test here, only the line under it.
vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({ default: () => null }),
);

import ShowModelDropdown from "../ShowModelDropdown";
import CustomModelDropdown from "../CustomModelDropdown";
import PickedModelNotice from "../PickedModelNotice";
import { normalizeModelOption } from "../common";
import {
  CREDITS_ROUTE,
  creditsRouteFrom,
  noUsableModelSentence,
} from "../noUsableModel";
import AgentChatForm from "src/sections/agents/CreateNewAgent/AgentConfigurationStep/AgentChatForm";
import {
  byokRow,
  configureHint,
  legacyRow,
  managedRow,
  unavailableRow,
} from "./modelListContractFixtures";

const CREDITS_OFF =
  "Run this with your own provider key (Settings → AI Providers).";
const TRY_IT =
  "Run this with your own provider key (Settings → AI Providers), or try models with your Future AGI credits: open a model in AI Gateway → Models and use Try it.";
const API_KEY =
  "Run this with your own provider key (Settings → AI Providers), or call the gateway with an API key on your Future AGI credits (AI Gateway → API keys).";
const MODEL_LIST_URL = "/model-hub/api/models_list/";
const STATUS_URL = "/agentcc/managed-models/";
const GATEWAYS_URL = "/agentcc/gateways/";
const CREDITS_ON = { available: true, enabled: true };
const gatewayList = (fields = {}) => ({
  status: true,
  result: [{ id: "gw-1", name: "default", ...fields }],
});

const notice = () => screen.queryByTestId("no-usable-model-notice");

function renderMenu({ rows, ...props }) {
  const anchor = document.createElement("div");
  document.body.appendChild(anchor);
  const onClose = vi.fn();
  render(
    <ShowModelDropdown
      ref={{ current: anchor }}
      open
      searchDropdown
      hideCreateLabel
      options={rows.map(normalizeModelOption)}
      value=""
      isLoadingModelList={false}
      isFetchingNextPage={false}
      fetchNextPage={vi.fn()}
      setIsFocus={vi.fn()}
      setSearchQuery={vi.fn()}
      inputRef={{ current: null }}
      onChange={vi.fn()}
      onConfigOpen={vi.fn()}
      onClose={onClose}
      {...props}
    />,
  );
  return { onClose };
}

// What the server answers: the model list, one model by name, and the
// managed-models status.
function serve({ rows = [], entry = null, status = null, gateways = null }) {
  state.get.mockImplementation((url, config) => {
    if (url === MODEL_LIST_URL) {
      const results = config?.params?.name ? (entry ? [entry] : []) : rows;
      return Promise.resolve({
        data: { results, next: null, current_page: 1 },
      });
    }
    if (url === STATUS_URL) {
      return Promise.resolve({ data: { result: status } });
    }
    if (url === GATEWAYS_URL) {
      return gateways
        ? Promise.resolve({ data: gateways })
        : Promise.reject(new Error("no gateway"));
    }
    return Promise.reject(new Error(`unmocked GET ${url}`));
  });
}

const withQueries = (ui) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {ui}
  </QueryClientProvider>
);

beforeEach(() => {
  state.get.mockReset();
});

describe("the sentence", () => {
  it("is exact in its three forms and never says Playground", () => {
    expect(noUsableModelSentence()).toBe(CREDITS_OFF);
    expect(noUsableModelSentence(CREDITS_ROUTE.NONE)).toBe(CREDITS_OFF);
    expect(noUsableModelSentence(CREDITS_ROUTE.TRY_IT)).toBe(TRY_IT);
    expect(noUsableModelSentence(CREDITS_ROUTE.API_KEY)).toBe(API_KEY);
    expect(`${TRY_IT} ${API_KEY}`).not.toMatch(/Playground/);
  });

  it("names a credits path only when credits are offered and turned on", () => {
    const route = (status, gateways) => creditsRouteFrom(status, gateways);
    expect(route({ available: true, enabled: false }, gatewayList())).toBe(
      CREDITS_ROUTE.NONE,
    );
    expect(route({ available: false, enabled: true }, gatewayList())).toBe(
      CREDITS_ROUTE.NONE,
    );
    expect(route(null, gatewayList())).toBe(CREDITS_ROUTE.NONE);
  });

  it("reads a missing delegated_playground_available as available; only false means an API key", () => {
    const route = (gateways) => creditsRouteFrom(CREDITS_ON, gateways);
    expect(route(gatewayList())).toBe(CREDITS_ROUTE.TRY_IT);
    expect(route(gatewayList({ delegated_playground_available: true }))).toBe(
      CREDITS_ROUTE.TRY_IT,
    );
    expect(route(null)).toBe(CREDITS_ROUTE.TRY_IT);
    expect(
      route(gatewayList({ delegated_playground_available: false })),
    ).toBe(CREDITS_ROUTE.API_KEY);
  });
});

describe("shared model menu: one notice when no model can run", () => {
  it("shows the provider-key sentence above the list when every row is unavailable, and keeps the row captions", () => {
    renderMenu({
      rows: [
        unavailableRow(),
        unavailableRow({ model_name: "gpt-5.5", providers: "openai" }),
      ],
    });

    expect(notice()).not.toBeNull();
    expect(notice().textContent).toBe(CREDITS_OFF);
    expect(
      screen.getByRole("link", { name: "Settings → AI Providers" }),
    ).toHaveAttribute("href", "/dashboard/settings/ai-providers");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByText(configureHint("Mistral"))).toBeInTheDocument();
    expect(screen.getByText(configureHint("OpenAI"))).toBeInTheDocument();
  });

  it("Try it path: links AI Gateway → Models to the gateway models list", () => {
    renderMenu({
      rows: [unavailableRow()],
      creditsRoute: CREDITS_ROUTE.TRY_IT,
    });

    expect(notice().textContent).toBe(TRY_IT);
    expect(
      screen.getByRole("link", { name: "AI Gateway → Models" }),
    ).toHaveAttribute("href", "/dashboard/gateway/models");
  });

  it("API-key path: links AI Gateway → API keys to the gateway's API keys", () => {
    renderMenu({
      rows: [unavailableRow()],
      creditsRoute: CREDITS_ROUTE.API_KEY,
    });

    expect(notice().textContent).toBe(API_KEY);
    expect(
      screen.getByRole("link", { name: "AI Gateway → API keys" }),
    ).toHaveAttribute("href", "/dashboard/gateway/keys");
  });

  it("is absent when one row can run on Future AGI credits", () => {
    renderMenu({ rows: [unavailableRow(), managedRow()] });
    expect(notice()).toBeNull();
  });

  it("is absent when one row has the user's own key", () => {
    renderMenu({ rows: [unavailableRow(), byokRow()] });
    expect(notice()).toBeNull();
  });

  it("is absent while the list is loading", () => {
    renderMenu({ rows: [unavailableRow()], isLoadingModelList: true });
    expect(notice()).toBeNull();
  });

  it("is absent for the empty-list placeholder", () => {
    renderMenu({
      rows: [{ label: "No model provided", value: "no", disabled: true }],
    });
    expect(screen.getByText("No model provided")).toBeInTheDocument();
    expect(notice()).toBeNull();
  });

  it("shows for the Agents menus too (requireUserApiKey={false}), where rows stay selectable", () => {
    renderMenu({
      rows: [legacyRow({ is_available: false }), unavailableRow()],
      requireUserApiKey: false,
    });
    expect(notice().textContent).toBe(CREDITS_OFF);
  });

  it("closes the menu when a link is followed", () => {
    const { onClose } = renderMenu({ rows: [unavailableRow()] });
    fireEvent.click(
      screen.getByRole("link", { name: "Settings → AI Providers" }),
    );
    expect(onClose).toHaveBeenCalled();
  });
});

describe("shared picker reads whether credits are on", () => {
  function mountPicker(props = {}) {
    const onChange = vi.fn();
    render(
      withQueries(
        <CustomModelDropdown
          openSelectModel
          setOpenSelectModel={vi.fn()}
          hideCreateLabel
          customTrigger={<span>Pick a model</span>}
          onChange={onChange}
          value=""
          modelDetail={{}}
          extraParams={{ model_type: "llm" }}
          {...props}
        />,
      ),
    );
    return { onChange };
  }

  it("credits on, gateway field not sent yet: the Try it path", async () => {
    serve({
      rows: [unavailableRow()],
      status: { ...CREDITS_ON, balance_usd: 5 },
      gateways: gatewayList(),
    });
    mountPicker();

    await waitFor(() => expect(notice()?.textContent).toBe(TRY_IT));
  });

  it("credits on, delegated calls off: the gateway API-key path", async () => {
    serve({
      rows: [unavailableRow()],
      status: CREDITS_ON,
      gateways: gatewayList({ delegated_playground_available: false }),
    });
    mountPicker();

    await waitFor(() => expect(notice()?.textContent).toBe(API_KEY));
  });

  it("keeps the provider-key sentence when credits are off", async () => {
    serve({
      rows: [unavailableRow()],
      status: { available: true, enabled: false },
    });
    mountPicker();

    await waitFor(() =>
      expect(state.get).toHaveBeenCalledWith(STATUS_URL, { timeout: 30000 }),
    );
    expect(notice().textContent).toBe(CREDITS_OFF);
    expect(state.get).not.toHaveBeenCalledWith(GATEWAYS_URL, expect.anything());
  });

  it("does not read the credits status when a model can run", async () => {
    serve({ rows: [managedRow()] });
    mountPicker();

    await screen.findByText("gpt-5.5-mini");
    expect(notice()).toBeNull();
    expect(state.get).not.toHaveBeenCalledWith(STATUS_URL, expect.anything());
  });

  it("keeps credential_mode and payer in the picked value", async () => {
    serve({ rows: [managedRow()] });
    const { onChange } = mountPicker();

    fireEvent.click(await screen.findByText("gpt-5.5-mini"));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0].target.value).toMatchObject({
      model_name: "gpt-5.5-mini",
      providers: "openai",
      credential_mode: "managed",
      payer: "future_agi_credits",
    });
  });

  it("adds nothing to the picked value of a row without the contract fields", async () => {
    serve({ rows: [legacyRow({ is_available: true })] });
    const { onChange } = mountPicker();

    fireEvent.click(await screen.findByText("gpt-4o"));

    const picked = onChange.mock.calls[0][0].target.value;
    expect(Object.keys(picked).sort()).toEqual([
      "id",
      "isAvailable",
      "logoUrl",
      "model_name",
      "providers",
      "type",
    ]);
  });
});

describe("Agents: the picked model cannot run", () => {
  function AgentHarness({ model }) {
    const { control } = useForm({ defaultValues: { model } });
    return <AgentChatForm control={control} />;
  }
  AgentHarness.propTypes = { model: PropTypes.string };

  it("shows the sentence under the model field for a picked unusable model", async () => {
    serve({ entry: unavailableRow(), status: { available: false } });
    render(withQueries(<AgentHarness model="mistral-large-3" />));

    const line = await screen.findByTestId("picked-model-notice");
    expect(line.textContent).toBe(CREDITS_OFF);
    expect(state.get).toHaveBeenCalledWith(MODEL_LIST_URL, {
      params: { name: "mistral-large-3" },
    });
  });

  it("credits on: links AI Gateway → Models to this model's Try it tab", async () => {
    serve({
      entry: unavailableRow(),
      status: CREDITS_ON,
      gateways: gatewayList(),
    });
    render(withQueries(<PickedModelNotice modelName="mistral-large-3" />));

    await waitFor(() =>
      expect(screen.getByTestId("picked-model-notice").textContent).toBe(
        TRY_IT,
      ),
    );
    expect(
      screen.getByRole("link", { name: "AI Gateway → Models" }),
    ).toHaveAttribute(
      "href",
      "/dashboard/gateway/models/mistral/mistral-large-3?tab=try",
    );
  });

  it("says nothing for a picked model that runs on Future AGI credits", async () => {
    serve({ entry: managedRow() });
    render(withQueries(<PickedModelNotice modelName="gpt-5.5-mini" />));

    await waitFor(() =>
      expect(state.get).toHaveBeenCalledWith(MODEL_LIST_URL, {
        params: { name: "gpt-5.5-mini" },
      }),
    );
    expect(screen.queryByTestId("picked-model-notice")).toBeNull();
  });

  it("says nothing before a model is picked", () => {
    render(withQueries(<PickedModelNotice modelName="" />));
    expect(screen.queryByTestId("picked-model-notice")).toBeNull();
    expect(state.get).not.toHaveBeenCalled();
  });
});
