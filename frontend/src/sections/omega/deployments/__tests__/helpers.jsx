/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { SnackbarProvider } from "notistack";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";

import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { DeploymentsApiProvider } from "../api/DeploymentsApiContext";

// Shapes follow EE's omega-workspace-deployments/v1 replies
// (packages/server/src/workspace/project-deployments/views.ts at 2128123a).
export const ID = "dep-0123456789abcdef";
const at = (minutesAgo) =>
  new Date(Date.now() - minutesAgo * 60_000).toISOString();

export function revisionView(revision, overrides = {}) {
  return {
    revision,
    status: "superseded",
    message: null,
    createdAt: at(60 - revision),
    builtAt: at(59 - revision),
    liveAt: at(58 - revision),
    bundle: {
      sha256: `${revision}`.repeat(64).slice(0, 64),
      contentSha256: "c".repeat(64),
      size: 2535,
      files: 4,
    },
    env: { SERVE_FIXTURE_GREETING: `hello-v${revision}` },
    secretRefs: [],
    studio: true,
    image: `deptab-ws-${ID}:r${revision}`,
    ...overrides,
  };
}

export function detailView(overrides = {}) {
  return {
    schemaVersion: "omega-workspace-deployments/v1",
    id: ID,
    name: "greeter",
    revision: 2,
    status: "live",
    url: "http://127.0.0.1:55408",
    studioUrl: "http://127.0.0.1:55408/",
    message: "Revision 2 is live.",
    // EE DEP-02 (views.ts): every deployment today is published on the
    // deployment host's loopback address only.
    urlReach: "deployment-host",
    urlNote: "Reachable from the deployment host only.",
    kind: "project",
    liveRevision: 2,
    revisionCount: 2,
    createdAt: at(60),
    updatedAt: at(2),
    operation: {
      seq: 2,
      action: "deploy",
      revision: 2,
      by: "acc_1",
      startedAt: at(3),
      status: "live",
      message: "Revision 2 is live.",
      endedAt: at(2),
    },
    revisions: [revisionView(2, { status: "live" }), revisionView(1)],
    history: [
      {
        seq: 1,
        at: at(50),
        action: "deploy",
        revision: 1,
        previousRevision: null,
        outcome: "live",
        by: "acc_1",
      },
      {
        seq: 2,
        at: at(2),
        action: "deploy",
        revision: 2,
        previousRevision: 1,
        outcome: "live",
        by: "acc_1",
      },
    ],
    health: { status: "ok", httpStatus: 200, revision: 2, checkedAt: at(0) },
    ...overrides,
  };
}

export const summaryOf = (detail) => {
  const {
    id,
    name,
    revision,
    status,
    url,
    studioUrl,
    message,
    urlReach,
    urlNote,
    kind,
    liveRevision,
    revisionCount,
    createdAt,
    updatedAt,
  } = detail;
  return {
    id,
    name,
    revision,
    status,
    url,
    studioUrl,
    message,
    urlReach,
    urlNote,
    kind,
    liveRevision,
    revisionCount,
    createdAt,
    updatedAt,
  };
};

/** A fake client with the real client's method names; every call is a vi.fn. */
export function fakeApi(overrides = {}) {
  const detail = detailView();
  return {
    list: vi.fn(async () => [summaryOf(detail)]),
    get: vi.fn(async () => detail),
    create: vi.fn(async () => detail),
    rollback: vi.fn(async () => detail),
    rotateToken: vi.fn(async () => detail),
    remove: vi.fn(async () => ({
      id: ID,
      name: "greeter",
      containers: 1,
      images: 2,
      volume: true,
    })),
    health: vi.fn(async () => ({ health: detail.health, liveRevision: 2 })),
    credentials: vi.fn(async () => []),
    logs: vi.fn(() => ({ close: vi.fn(), done: new Promise(() => {}) })),
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location">{`${location.pathname}${location.search}`}</div>
  );
}

const theme = createTheme({ palette: palette("light"), typography });

/**
 * Render a page under the shell's route shapes with a fake Omega client.
 * `parts` lists the product parts the server says work today
 * (omega_rollout/readiness.py): none by default, as on every deployment until
 * a part ships; every product is admitted.
 */
export function renderDeployments(
  element,
  { api = fakeApi(), route = "/dashboard/deployments", parts = [] } = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchInterval: false },
      mutations: { retry: false },
    },
  });
  queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
    data: capabilitiesAnswer({ parts }),
  });
  function Wrapper({ children }) {
    return (
      <HelmetProvider>
        <QueryClientProvider client={queryClient}>
          <ThemeProvider theme={theme}>
            <SnackbarProvider>
              <DeploymentsApiProvider api={api}>
                <MemoryRouter initialEntries={[route]}>
                  {children}
                  <LocationProbe />
                </MemoryRouter>
              </DeploymentsApiProvider>
            </SnackbarProvider>
          </ThemeProvider>
        </QueryClientProvider>
      </HelmetProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  const result = render(
    <Routes>
      <Route path="/dashboard/deployments" element={element} />
      <Route path="/dashboard/deployments/new" element={element} />
      <Route path="/dashboard/deployments/:id/:tab?" element={element} />
    </Routes>,
    { wrapper: Wrapper },
  );
  return { ...result, api, queryClient };
}
