import React, { StrictMode } from "react";
import PropTypes from "prop-types";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import PlaygroundHandoff from "../PlaygroundHandoff";
import { workbenchModelFor } from "../handoff";
import { laterPageCatalogs, modelListResponse } from "./promptsModelFixtures";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});
const snackbars = vi.hoisted(() => []);
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

const GPT_4O = {
  model_name: "gpt-4o",
  providers: "openai",
  is_available: false,
  logo_url: "",
  type: "chat",
};
const AZURE_GPT_4O = {
  ...GPT_4O,
  model_name: "azure/gpt-4o",
  providers: "azure",
};

function Where({ into }) {
  const location = useLocation();
  into.pathname = location.pathname;
  into.state = location.state;
  return null;
}
Where.propTypes = { into: PropTypes.object.isRequired };

function renderHandoff(url) {
  const where = { pathname: null };
  render(
    <StrictMode>
      <QueryClientProvider client={new QueryClient()}>
        <ThemeProvider
          theme={createTheme({ palette: palette("light"), typography })}
        >
          <MemoryRouter initialEntries={[url]}>
            <Routes>
              <Route
                path="/dashboard/gateway/playground"
                element={<PlaygroundHandoff />}
              />
              <Route path="*" element={<div data-testid="elsewhere" />} />
            </Routes>
            <Where into={where} />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </StrictMode>,
  );
  return where;
}

describe("matching a gateway model to the Prompts model list", () => {
  it("prefers the exact id, then the bare name from the same provider", () => {
    expect(workbenchModelFor("gpt-4o", [AZURE_GPT_4O, GPT_4O])).toBe(GPT_4O);
    expect(workbenchModelFor("openai/gpt-4o", [AZURE_GPT_4O, GPT_4O])).toBe(
      GPT_4O,
    );
    expect(workbenchModelFor("azure/gpt-4o", [GPT_4O, AZURE_GPT_4O])).toBe(
      AZURE_GPT_4O,
    );
    expect(
      workbenchModelFor("mistralai/ministral-8b-2512", [GPT_4O]),
    ).toBeNull();
  });
});

describe("the gateway playground URL opens the Prompts playground", () => {
  beforeEach(() => {
    api.get.mockReset();
    api.post.mockReset();
    snackbars.length = 0;
  });

  it("opens the Prompts list when no model is named", async () => {
    const where = renderHandoff("/dashboard/gateway/playground");
    await waitFor(() =>
      expect(where.pathname).toBe("/dashboard/workbench/all"),
    );
    expect(api.post).not.toHaveBeenCalled();
  });

  it("creates one prompt with the model, system and user message preset", async () => {
    api.get.mockImplementation(async (_url, options) =>
      modelListResponse([AZURE_GPT_4O, GPT_4O], options),
    );
    api.post.mockResolvedValue({
      data: { result: { root_template: "tpl-1" } },
    });
    const where = renderHandoff(
      "/dashboard/gateway/playground?model=openai%2Fgpt-4o&prompt=Hello&system=Be%20brief",
    );

    await waitFor(() =>
      expect(where.pathname).toBe("/dashboard/workbench/create/tpl-1"),
    );
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(api.get).toHaveBeenNthCalledWith(1, "/model-hub/api/models_list/", expect.objectContaining({
      params: { name: "openai/gpt-4o" }, timeout: 15000, signal: expect.any(AbortSignal),
    }));
    expect(api.get).toHaveBeenNthCalledWith(2, "/model-hub/api/models_list/", expect.objectContaining({
      params: { name: "gpt-4o" }, timeout: 15000, signal: expect.any(AbortSignal),
    }));
    expect(api.post.mock.calls[0][2]).toMatchObject({ timeout: 15000, signal: expect.any(AbortSignal) });
    expect(api.post).toHaveBeenCalledTimes(1);
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe("/model-hub/prompt-templates/create-draft/");
    const config = body.prompt_config[0];
    expect(config.configuration).toMatchObject({
      model: "gpt-4o",
      model_detail: { model_name: "gpt-4o", providers: "openai", type: "chat" },
    });
    expect(config.messages).toEqual([
      { role: "system", content: [{ type: "text", text: "Be brief" }] },
      { role: "user", content: [{ type: "text", text: "Hello" }] },
    ]);
  });

  it("requires explicit consent before creating an unselected draft for a genuine no-match", async () => {
    api.get.mockResolvedValue({ data: { results: [] } });
    api.post.mockResolvedValue({
      data: { result: { root_template: "tpl-2" } },
    });
    const where = renderHandoff(
      "/dashboard/gateway/playground?model=mistralai%2Fministral-8b-2512",
    );

    await screen.findByText(/not in the Prompts model list/);
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.getByText(/Settings > AI Providers/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Continue with model unselected" }));
    await waitFor(() =>
      expect(where.pathname).toBe("/dashboard/workbench/create/tpl-2"),
    );
    expect(api.post).toHaveBeenCalledTimes(1);
    const [, body] = api.post.mock.calls[0];
    expect(body.prompt_config[0].configuration).toBeUndefined();
    expect(snackbars).toHaveLength(1);
    expect(snackbars[0].message).toMatch(/mistralai\/ministral-8b-2512/);
    expect(snackbars[0].message).toMatch(/not in the Prompts model list/);
  });

  it.each(laterPageCatalogs)("selects later-page $id without an absence notice", async ({ id, exact, rows }) => {
    api.get.mockImplementation(async (_url, options) => modelListResponse(rows, options));
    api.post.mockResolvedValue({ data: { result: { root_template: "tpl-later" } } });
    const where = renderHandoff(
      `/dashboard/gateway/playground?model=${encodeURIComponent(id)}&prompt=Hello`,
    );

    await waitFor(() => expect(where.pathname).toBe("/dashboard/workbench/create/tpl-later"));
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][1].prompt_config[0].configuration).toMatchObject({
      model: exact.model_name,
      model_detail: { providers: exact.providers, is_available: false },
    });
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(snackbars).toHaveLength(0);
  });

  it("rejects substring-only rows instead of selecting a different model", async () => {
    api.get.mockResolvedValue({ data: { results: [AZURE_GPT_4O] } });
    api.post.mockResolvedValue({ data: { result: { root_template: "tpl-substring" } } });
    const where = renderHandoff("/dashboard/gateway/playground?model=azure%2Fgpt-4");
    await screen.findByText(/not in the Prompts model list/);
    expect(api.post).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Continue with model unselected" }));
    await waitFor(() => expect(where.pathname).toBe("/dashboard/workbench/create/tpl-substring"));
    expect(api.post.mock.calls[0][1].prompt_config[0].configuration).toBeUndefined();
    expect(snackbars[0].message).toMatch(/not in the Prompts model list/);
  });

  it.each(["request failure", "fallback failure", "malformed response"])(
    "does not claim absence after %s",
    async (failure) => {
      if (failure === "malformed response") {
        api.get.mockResolvedValue({ data: {} });
      } else {
        api.get.mockRejectedValue(new Error("unavailable"));
        if (failure === "fallback failure") {
          api.get.mockResolvedValueOnce({ data: { results: [] } });
        }
      }
      renderHandoff(
        "/dashboard/gateway/playground?model=azure%2Fgpt-4&prompt=Hello&system=Be%20brief",
      );
      expect(await screen.findByText(/could not be checked/)).toBeInTheDocument();
      expect(screen.queryByText(/not in the Prompts model list/)).not.toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: "Original system message" })).toHaveValue("Be brief");
      expect(screen.getByRole("textbox", { name: "Original user message" })).toHaveValue("Hello");
      expect(screen.queryByRole("button", { name: "Continue with model unselected" })).not.toBeInTheDocument();
      expect(api.post).not.toHaveBeenCalled();
      expect(snackbars).toHaveLength(0);
    },
  );

  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  const original = { prompt: '  Keep "quotes".\nAnd whitespace.  ', system: "  Be precise.\n  " };
  const recoveryUrl = `/dashboard/gateway/playground?${new URLSearchParams({ model: "gpt-4o", ...original })}`;
  const expectOriginalMessages = (messages) => expect(messages).toEqual([
    { role: "system", content: [{ type: "text", text: original.system }] },
    { role: "user", content: [{ type: "text", text: original.prompt }] },
  ]);

  it("keeps original messages when lookup fails, and Retry only refetches the lookup", async () => {
    const user = userEvent.setup();
    const clipboard = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    api.get.mockRejectedValueOnce(new Error("Lookup unavailable"))
      .mockResolvedValue({ data: { results: [GPT_4O] } });
    api.post.mockResolvedValue({ data: { result: { root_template: "recovered" } } });
    const where = renderHandoff(recoveryUrl);
    await screen.findByText("Could not look up the Prompts model");
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Original system message" })).toHaveValue(original.system);
    expect(screen.getByRole("textbox", { name: "Original user message" })).toHaveValue(original.prompt);
    await user.click(screen.getByRole("button", { name: "Copy messages" }));
    expectOriginalMessages(JSON.parse(clipboard.mock.calls[0][0]));
    await user.click(screen.getByRole("button", { name: "Retry lookup" }));
    const create = await screen.findByRole("button", { name: "Create draft in Prompts" });
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(api.post).not.toHaveBeenCalled();
    await user.click(create);
    await waitFor(() => expect(where.pathname).toBe("/dashboard/workbench/create/recovered"));
    expect(api.post).toHaveBeenCalledTimes(1);
    expectOriginalMessages(api.post.mock.calls[0][1].prompt_config[0].messages);
  });

  it("bounds a stalled lookup and ignores its late success rather than creating a draft", async () => {
    vi.useFakeTimers();
    let finish;
    api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    renderHandoff(recoveryUrl);
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(screen.getByText("Could not look up the Prompts model")).toBeInTheDocument();
    expect(api.get.mock.calls[0][1].signal.aborted).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
    await act(async () => { finish({ data: { results: [GPT_4O] } }); });
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Original user message" })).toHaveValue(original.prompt);
  });

  it.each(["network", "server error", "missing id"])("offers copy and inspect, never blind retry or a not-created claim, after %s draft uncertainty", async (mode) => {
    const user = userEvent.setup();
    const clipboard = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    api.get.mockResolvedValue({ data: { results: [GPT_4O] } });
    if (mode === "missing id") api.post.mockResolvedValue({ data: { result: {} } });
    else api.post.mockRejectedValue(mode === "network" ? new Error("Connection lost") : { statusCode: 503, message: "Unavailable" });
    const where = renderHandoff(recoveryUrl);
    expect(await screen.findByText("Draft creation outcome unknown")).toBeInTheDocument();
    expect(screen.getByText(/A draft may already exist/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/was not created|no draft exists/i);
    expect(screen.queryByRole("button", { name: /retry|create draft/i })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Original system message" })).toHaveValue(original.system);
    await user.click(screen.getByRole("button", { name: "Copy messages" }));
    expectOriginalMessages(JSON.parse(clipboard.mock.calls[0][0]));
    expect(api.post).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("link", { name: "Inspect existing Prompts" }));
    await waitFor(() => expect(where.pathname).toBe("/dashboard/workbench/all"));
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("bounds a stalled draft POST and never navigates or posts again on its late response", async () => {
    vi.useFakeTimers();
    let finish;
    api.get.mockResolvedValue({ data: { results: [GPT_4O] } });
    api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const where = renderHandoff(recoveryUrl);
    await act(async () => {});
    expect(api.post).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(screen.getByText("Draft creation outcome unknown")).toBeInTheDocument();
    expect(api.post.mock.calls[0][2].signal.aborted).toBe(true);
    await act(async () => { finish({ data: { result: { root_template: "late" } } }); });
    expect(where.pathname).toBe("/dashboard/gateway/playground");
    fireEvent.click(screen.getByRole("button", { name: "Copy messages" }));
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it("does not interpret a malformed lookup response as consent to an unselected draft", async () => {
    api.get.mockResolvedValue({ data: { status: false, result: "Unavailable" } });
    renderHandoff(recoveryUrl);
    await screen.findByText("Could not look up the Prompts model");
    expect(screen.queryByRole("button", { name: "Continue with model unselected" })).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
  });
});
