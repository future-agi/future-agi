import React, { StrictMode } from "react";
import PropTypes from "prop-types";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import PlaygroundHandoff from "../PlaygroundHandoff";
import { workbenchModelFor } from "../handoff";

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
    api.get.mockResolvedValue({ data: { results: [AZURE_GPT_4O, GPT_4O] } });
    api.post.mockResolvedValue({
      data: { result: { root_template: "tpl-1" } },
    });
    const where = renderHandoff(
      "/dashboard/gateway/playground?model=openai%2Fgpt-4o&prompt=Hello&system=Be%20brief",
    );

    await waitFor(() =>
      expect(where.pathname).toBe("/dashboard/workbench/create/tpl-1"),
    );
    expect(api.get).toHaveBeenCalledWith("/model-hub/api/models_list/", {
      params: { page: 1, search: "gpt-4o" },
    });
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

  it("still opens Prompts, and says why, when the model is not in its list", async () => {
    api.get.mockResolvedValue({ data: { results: [] } });
    api.post.mockResolvedValue({
      data: { result: { root_template: "tpl-2" } },
    });
    const where = renderHandoff(
      "/dashboard/gateway/playground?model=mistralai%2Fministral-8b-2512",
    );

    await waitFor(() =>
      expect(where.pathname).toBe("/dashboard/workbench/create/tpl-2"),
    );
    const [, body] = api.post.mock.calls[0];
    expect(body.prompt_config[0].configuration).toBeUndefined();
    expect(snackbars).toHaveLength(1);
    expect(snackbars[0].message).toMatch(/mistralai\/ministral-8b-2512/);
    expect(snackbars[0].message).toMatch(/not in the Prompts model list/);
  });

  it("offers the Prompts list when the prompt cannot be created", async () => {
    api.get.mockResolvedValue({ data: { results: [GPT_4O] } });
    api.post.mockRejectedValue(new Error("boom"));
    renderHandoff("/dashboard/gateway/playground?model=gpt-4o");

    expect(
      await screen.findByText("Could not open the Prompts playground"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Prompts" })).toHaveAttribute(
      "href",
      "/dashboard/workbench/all",
    );
  });
});
