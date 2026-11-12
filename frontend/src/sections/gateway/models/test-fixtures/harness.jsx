import React from "react";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { DEMO_CATALOG, DEMO_PROVIDERS, FA_PROVIDERS } from "./demoCatalog";
import { fakeRequestLogs } from "./requestLogs";

// Test harness for the Models pages: the FA theme, React Query, and the
// gateway routes the pages live under, at a given URL. `location` reports
// where the app navigated to.
export function renderModelsAt(url, element, { mode = "light" } = {}) {
  const location = { pathname: null, search: null };
  const Probe = () => {
    ({ pathname: location.pathname, search: location.search } = useLocation());
    return null;
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const utils = render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <ThemeProvider
          theme={createTheme({ palette: palette(mode), typography })}
        >
          <MemoryRouter initialEntries={[url]}>
            <Probe />
            <Routes>
              <Route path="/dashboard/gateway/models" element={element} />
              <Route path="/dashboard/gateway/models/:tab" element={element} />
              <Route
                path="/dashboard/gateway/models/:provider/:model"
                element={element}
              />
              <Route path="*" element={<div data-testid="elsewhere" />} />
            </Routes>
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
  return { ...utils, location, client };
}

export const MANAGED_ON = {
  available: true,
  enabled: true,
  balance_usd: 34.999336,
  markup_percent: 5,
};

// GET responses by URL, as the Django API answers them on the demo stack.
export function demoResponses(endpoints, overrides = {}) {
  const gw = endpoints.gateway;
  return {
    [gw.managedModels]: { status: true, result: MANAGED_ON },
    [gw.managedModelsCatalog]: { status: true, result: DEMO_CATALOG },
    [gw.providers("default")]: { status: true, result: DEMO_PROVIDERS },
    [gw.analyticsModels]: { status: true, result: { models: [] } },
    // The org's request logs: none unless a test gives some.
    [gw.requestLogs]: fakeRequestLogs([]),
    // The Prompts model list (the platform catalog): empty unless a test
    // gives rows.
    [endpoints.develop.modelList]: { results: [] },
    // Future AGI's provider list, with each provider's logo.
    [endpoints.develop.apiKey.status]: { status: true, result: FA_PROVIDERS },
    ...overrides,
  };
}

// An axios.get stand-in answering from `responses`; a value that is an Error
// is thrown, a function is called with the request config (and may answer
// with a promise).
export function answerFrom(responses) {
  return async (url, config) => {
    if (!(url in responses)) throw new Error(`unmocked GET ${url}`);
    const value = responses[url];
    if (value instanceof Error) throw value;
    const data = typeof value === "function" ? await value(config) : value;
    return { data };
  };
}

// Every request a test made, checked against the app's API contract (the
// axios interceptor refuses a request that fails it on the real stack, so a
// mocked axios must not hide one). Returns the failures, [] when all pass.
export async function contractFailures(api) {
  const { validateContractedRequestConfig } = await import(
    "src/api/contracts/openapi-contract"
  );
  const calls = [
    ...api.get.mock.calls.map(([url, config]) => ({
      method: "get",
      url,
      ...config,
    })),
    ...api.post.mock.calls.map(([url, data, config]) => ({
      method: "post",
      url,
      data,
      ...config,
    })),
  ];
  return calls
    .map((config) => ({
      config,
      result: validateContractedRequestConfig(config),
    }))
    .filter(({ result }) => !result.ok)
    .map(
      ({ config, result }) =>
        `${config.method} ${config.url}: ${result.error?.message}`,
    );
}
