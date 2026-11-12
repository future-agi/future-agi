import React from "react";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

// WALK-STG-FA-PICKERS-NO-CREDITS-MODELS-WORKBENCH: a run refused for a
// missing provider key says what works, word for word from the shared copy.
const PROVIDER_KEY_SENTENCE =
  "Run this with your own provider key (Settings → AI Providers).";
const CREDITS_SENTENCE =
  "Run this with your own provider key (Settings → AI Providers), or try models with your Future AGI credits: open a model in AI Gateway → Models and use Try it.";
// WORKBENCH-DELEGATED-PLAYGROUND-COPY: credits are on, but the gateway says
// it does not serve Try it here (delegated_playground_available false).
const API_KEY_SENTENCE =
  "Run this with your own provider key (Settings → AI Providers), or call the gateway with an API key on your Future AGI credits (AI Gateway → API keys).";
const REFUSAL =
  "API key not configured for openai. Please add your API key in settings.";

let status;
vi.mock("src/sections/gateway/models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => status,
}));
// The gateway list read (the same one the model menus make), answered here
// as the AI Gateway pages would get it.
const gateway = vi.hoisted(() => {
  const state = { query: { data: undefined } };
  state.useGatewayList = vi.fn(() => state.query);
  return state;
});
vi.mock(
  "src/components/custom-model-dropdown/noUsableModel",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useGatewayList: (...args) => gateway.useGatewayList(...args),
  }),
);
vi.mock("../../../WorkbenchContext", () => ({
  usePromptWorkbenchContext: () => ({ saveAndRun: vi.fn() }),
}));
vi.mock("../ResultSection", () => ({
  default: () => <div data-testid="result-section" />,
}));
vi.mock("src/hooks/use-wavesurfer-cache", () => ({
  default: () => ({
    getWaveSurferInstance: vi.fn(),
    storeWaveSurferInstance: vi.fn(),
    updateWaveSurferInstance: vi.fn(),
    clearWaveSurferCache: vi.fn(),
  }),
}));

import OutputSection from "../OutputSection";
import {
  CREDITS_ROUTE,
  noUsableModelSentence,
} from "src/components/custom-model-dropdown/noUsableModel";

const renderOutput = (results) =>
  render(
    <MemoryRouter>
      <OutputSection results={results} promptGeneratingStatus={false} />
    </MemoryRouter>,
  );

const streamed = { output: [{ text: REFUSAL, status: "error" }] };
const CREDITS_ON = { data: { available: true, enabled: true } };
const gatewayList = (fields = {}) => ({
  data: { status: true, result: [{ id: "default", ...fields }] },
});

beforeEach(() => {
  gateway.query = { data: undefined };
  gateway.useGatewayList.mockClear();
});

describe("Workbench run refusal for a missing provider key", () => {
  beforeEach(() => {
    status = { isSuccess: true, isError: false, data: null };
  });

  it("says what works with a provider key when credits are not on", () => {
    status = { data: { available: true, enabled: false } };
    renderOutput(streamed);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(PROVIDER_KEY_SENTENCE);
    expect(alert.textContent).not.toContain("Future AGI credits");
    expect(alert.textContent).not.toMatch(/Playground/);
    const link = within(alert).getByRole("link", {
      name: "Settings → AI Providers",
    });
    expect(link.getAttribute("href")).toBe("/dashboard/settings/ai-providers");
    expect(
      within(alert).queryByRole("link", { name: "AI Gateway → Models" }),
    ).toBeNull();
    expect(alert.textContent).not.toMatch(/Please add your API key/);
  });

  it("adds the credits route when the page reads credits are on", () => {
    status = { data: { available: true, enabled: true } };
    renderOutput(streamed);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(CREDITS_SENTENCE);
    expect(alert.textContent).not.toMatch(/Playground/);
    expect(
      within(alert)
        .getByRole("link", { name: "AI Gateway → Models" })
        .getAttribute("href"),
    ).toBe("/dashboard/gateway/models");
    expect(
      within(alert)
        .getByRole("link", { name: "Settings → AI Providers" })
        .getAttribute("href"),
    ).toBe("/dashboard/settings/ai-providers");
  });

  it("uses the provider key sentence when the credits read fails", () => {
    status = { isError: true, data: { available: true, enabled: true } };
    renderOutput(streamed);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(PROVIDER_KEY_SENTENCE);
    expect(alert.textContent).not.toContain("Future AGI credits");
  });

  it("explains a refusal that came back from the HTTP run", () => {
    status = { data: { available: false, enabled: false } };
    renderOutput({ output: [], error: REFUSAL });
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(
      "Couldn't run: API key not configured for openai.",
    );
    expect(alert.textContent).toContain(PROVIDER_KEY_SENTENCE);
    expect(
      within(alert).getByRole("button", { name: "Retry" }),
    ).toBeInTheDocument();
  });

  it("keeps the streamed output on the page", () => {
    renderOutput(streamed);
    expect(screen.getByTestId("result-section")).toBeInTheDocument();
  });

  it("leaves other run errors as they were", () => {
    renderOutput({ output: [], error: "the run did not start" });
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("Couldn't run: the run did not startRetry");
    expect(within(alert).queryByRole("link")).toBeNull();
  });
});

describe("Workbench run refusal when the gateway does not serve Try it", () => {
  beforeEach(() => {
    status = CREDITS_ON;
  });

  it("sends the user to an API key, not to Try it", () => {
    gateway.query = gatewayList({ delegated_playground_available: false });
    renderOutput(streamed);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(API_KEY_SENTENCE);
    expect(alert.textContent).not.toMatch(/Try it/);
    expect(
      within(alert)
        .getByRole("link", { name: "AI Gateway → API keys" })
        .getAttribute("href"),
    ).toBe("/dashboard/gateway/keys");
    expect(
      within(alert)
        .getByRole("link", { name: "Settings → AI Providers" })
        .getAttribute("href"),
    ).toBe("/dashboard/settings/ai-providers");
    expect(
      within(alert).queryByRole("link", { name: "AI Gateway → Models" }),
    ).toBeNull();
    expect(gateway.useGatewayList).toHaveBeenCalledWith({ enabled: true });
  });

  it("says the same for a refusal that came back from the HTTP run", () => {
    gateway.query = gatewayList({ delegated_playground_available: false });
    renderOutput({ output: [], error: REFUSAL });
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(
      "Couldn't run: API key not configured for openai.",
    );
    expect(alert.textContent).toContain(API_KEY_SENTENCE);
    expect(alert.textContent).not.toMatch(/Try it/);
    expect(
      within(alert).getByRole("button", { name: "Retry" }),
    ).toBeInTheDocument();
  });

  it.each([
    ["serves Try it", gatewayList({ delegated_playground_available: true })],
    ["does not send the field", gatewayList()],
    ["has not answered", { data: undefined }],
    ["could not be read", { isError: true, data: undefined }],
  ])("keeps Try it when the gateway %s", (_why, query) => {
    gateway.query = query;
    renderOutput(streamed);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(CREDITS_SENTENCE);
    expect(
      within(alert).getByRole("link", { name: "AI Gateway → Models" }),
    ).toBeInTheDocument();
    expect(
      within(alert).queryByRole("link", { name: "AI Gateway → API keys" }),
    ).toBeNull();
  });

  it("names no gateway path when credits are off, whatever the gateway says", () => {
    status = { data: { available: true, enabled: false } };
    gateway.query = gatewayList({ delegated_playground_available: false });
    renderOutput(streamed);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(PROVIDER_KEY_SENTENCE);
    expect(alert.textContent).not.toContain("Future AGI credits");
    expect(
      within(alert).queryByRole("link", { name: "AI Gateway → API keys" }),
    ).toBeNull();
    expect(gateway.useGatewayList).not.toHaveBeenCalledWith({ enabled: true });
  });

  it("uses the model menus' sentences word for word", () => {
    expect(noUsableModelSentence(CREDITS_ROUTE.NONE)).toBe(
      PROVIDER_KEY_SENTENCE,
    );
    expect(noUsableModelSentence(CREDITS_ROUTE.TRY_IT)).toBe(CREDITS_SENTENCE);
    expect(noUsableModelSentence(CREDITS_ROUTE.API_KEY)).toBe(API_KEY_SENTENCE);
  });
});
