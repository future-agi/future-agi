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

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { SandboxesApiProvider } from "../api/SandboxesApiContext";

// Shapes below follow EE's omega-workspace-sandboxes/v1 responses
// (packages/server/src/workspace/sandboxes-types.ts at 39d14407).
export const CAPS = {
  defaultImage: "alpine:latest",
  images: ["alpine:latest", "python:3.12-slim"],
  limits: {
    default: { cpus: 1, memoryMb: 512, pidsLimit: 256 },
    min: { cpus: 0.1, memoryMb: 64, pidsLimit: 16 },
    max: { cpus: 1, memoryMb: 512, pidsLimit: 256 },
  },
  networkModes: ["none", "full"],
  defaultNetwork: "none",
  egressAllowList: {
    supported: false,
    reason: "Provider reason: no per-host allow-list on this runtime.",
  },
  idleTtlSeconds: { default: 1800, min: 60, max: 604800 },
  exec: {
    defaultTimeoutSeconds: 60,
    maxTimeoutSeconds: 900,
    maxOutputBytes: 1000000,
  },
  files: { maxReadBytes: 8388608, maxWriteBytes: 8388608 },
  maxActiveSandboxes: 10,
};

const ID = (n) => `sha256:${String(n).padStart(64, "0")}`;

/** Caps from an EE with sizes and curated templates (sandbox-sizes lane). */
export const CAPS_V2 = {
  ...CAPS,
  defaultImage: "omega-sandbox/base:1.0.0",
  images: [
    "omega-sandbox/base:1.0.0",
    "omega-sandbox/python-data:1.0.0",
    "omega-sandbox/browser:1.0.0",
    "alpine:3.20",
  ],
  limits: {
    default: { cpus: 0.5, memoryMb: 1024, pidsLimit: 256 },
    min: { cpus: 0.1, memoryMb: 64, pidsLimit: 16 },
    max: { cpus: 2, memoryMb: 4096, pidsLimit: 1024 },
  },
  sizes: [
    {
      id: "small",
      label: "Small",
      cpus: 0.5,
      memoryMb: 1024,
      pidsLimit: 256,
      goodFor: "Scripts and light work.",
      available: true,
    },
    {
      id: "medium",
      label: "Medium",
      cpus: 1,
      memoryMb: 2048,
      pidsLimit: 512,
      goodFor: "Builds and tests.",
      available: true,
    },
    {
      id: "large",
      label: "Large",
      cpus: 2,
      memoryMb: 4096,
      pidsLimit: 1024,
      goodFor: "Headless browsers.",
      available: true,
    },
    {
      id: "xl",
      label: "XL",
      cpus: 4,
      memoryMb: 8192,
      pidsLimit: 2048,
      goodFor: "Heavy builds.",
      available: false,
      refusedBy: "host",
      reason: "The container host has 2 CPUs; this size needs 4.",
    },
  ],
  defaultSize: "small",
  customLimits: true,
  templates: [
    {
      id: "base",
      name: "Base",
      description: "Python and Node.",
      contents: ["Python 3.12 with uv and pip", "Node.js 22 with npm"],
      sizeHint: "small",
      minSize: "small",
      image: "omega-sandbox/base:1.0.0",
      imageId: ID(1),
      available: true,
      scan: {
        scanner: "trivy 0.72.0",
        critical: 2,
        high: 70,
        fixableHighOrCritical: 0,
      },
    },
    {
      id: "python-data",
      name: "Python data",
      description: "numpy, pandas, matplotlib.",
      contents: ["Everything in Base", "numpy, pandas, matplotlib"],
      sizeHint: "medium",
      minSize: "small",
      image: "omega-sandbox/python-data:1.0.0",
      imageId: ID(2),
      available: true,
    },
    {
      id: "browser",
      name: "Browser",
      description: "Playwright and headless Chromium.",
      contents: ["Everything in Base", "Playwright"],
      sizeHint: "large",
      minSize: "medium",
      image: "omega-sandbox/browser:1.0.0",
      imageId: ID(3),
      available: true,
    },
    {
      id: "omega-agent",
      name: "Omega agent",
      description: "The Omega CLI.",
      contents: ["Everything in Base", "omega"],
      sizeHint: "small",
      minSize: "small",
      image: "omega-sandbox/omega-agent:1.0.0",
      imageId: ID(4),
      available: false,
      refusedBy: "host",
      reason:
        "This template's image is not on the container host yet. An operator builds it with deploy/sandbox-images/build.mjs.",
    },
  ],
  defaultTemplate: "base",
  settings: { canEdit: true },
};

/** `GET /workspace/sandboxes/settings` for an owner (canEdit) with nothing narrowed yet. */
export function settingsView(overrides = {}) {
  return {
    settings: {},
    operator: {
      templates: ["base", "python-data", "browser", "omega-agent"],
      defaultTemplate: "base",
      images: ["alpine:3.20"],
      sizes: ["small", "medium", "large", "xl"],
      defaultSize: "small",
      allowNetwork: true,
      maxActivePerWorkspace: 10,
      idleTtlSeconds: 1800,
      customLimits: true,
    },
    canEdit: true,
    caps: CAPS_V2,
    ...overrides,
  };
}

export const PROVIDER = {
  kind: "container",
  bindingId: "colima-default",
  isolationStatement: "Statement from provider metadata (test value 7f3c).",
  snapshotKind: "files",
  snapshotStatement: "Files only; memory and running services are not saved.",
  isolation: null,
};

export function sandboxView(overrides = {}) {
  const now = Date.now();
  return {
    id: "sbx_0123456789abcdef01234567",
    name: "data-cleanup",
    origin: "created",
    state: "active",
    createdAt: new Date(now - 3600_000).toISOString(),
    updatedAt: new Date(now - 60_000).toISOString(),
    lastActivityAt: new Date(now - 120_000).toISOString(),
    hibernatesAt: new Date(now + 28 * 60_000).toISOString(),
    image: "alpine:latest",
    limits: { cpus: 1, memoryMb: 512, pidsLimit: 256 },
    network: "none",
    idleTtlSeconds: 1800,
    generation: 1,
    meter: { execCount: 3, lastExecAt: new Date(now - 120_000).toISOString() },
    readOnly: false,
    usedBy: [],
    provider: PROVIDER,
    ...overrides,
  };
}

/** A fake client with the real client's method names; every call is a vi.fn. */
export function fakeApi(overrides = {}) {
  const sandbox = sandboxView();
  return {
    list: vi.fn(async () => ({
      sandboxes: [sandbox],
      caps: CAPS,
      provider: PROVIDER,
    })),
    get: vi.fn(async () => sandbox),
    create: vi.fn(async (input) => ({
      ...sandbox,
      id: "sbx_new0000000000000000000000",
      name: input.name || "sandbox",
    })),
    update: vi.fn(async () => sandbox),
    destroy: vi.fn(async () => ({ ...sandbox, state: "destroyed" })),
    hibernate: vi.fn(async () => ({ ...sandbox, state: "hibernated" })),
    resume: vi.fn(async () => sandbox),
    listFiles: vi.fn(async () => ({ path: "", entries: [] })),
    readFile: vi.fn(async () => ({
      path: "a.txt",
      encoding: "utf8",
      content: "",
      bytes: 0,
      truncated: false,
      modifiedAt: new Date().toISOString(),
    })),
    writeFile: vi.fn(async (_id, path, content) => ({
      path,
      bytes: content.length,
      modifiedAt: new Date().toISOString(),
    })),
    downloadFile: vi.fn(
      async () =>
        new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" }),
    ),
    uploadFile: vi.fn(async (_id, path, file) => ({
      path,
      bytes: file.size,
      modifiedAt: new Date().toISOString(),
    })),
    deletePath: vi.fn(async (_id, path, { recursive = false } = {}) => ({
      path,
      recursive,
    })),
    movePath: vi.fn(async (_id, from, to) => ({ from, to })),
    makeFolder: vi.fn(async (_id, path) => ({ path })),
    snapshots: vi.fn(async () => []),
    snapshot: vi.fn(async () => ({})),
    restore: vi.fn(async () => ({
      ...sandbox,
      id: "sbx_restored00000000000000000",
    })),
    activity: vi.fn(async () => []),
    environments: vi.fn(async () => []),
    settings: vi.fn(async () => settingsView()),
    saveSettings: vi.fn(async (input) => settingsView({ settings: input })),
    cancelExec: vi.fn(async () => ({ cancelled: true })),
    exec: vi.fn(() => ({ close: vi.fn(), done: new Promise(() => {}) })),
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

const theme = createTheme({ palette: palette("light"), typography });

/** Render a page under the real route shapes with a fake Omega client. */
export function renderSandboxes(
  element,
  { api = fakeApi(), route = "/dashboard/sandboxes" } = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchInterval: false },
      mutations: { retry: false },
    },
  });
  function Wrapper({ children }) {
    return (
      <HelmetProvider>
        <QueryClientProvider client={queryClient}>
          <ThemeProvider theme={theme}>
            <SnackbarProvider>
              <SandboxesApiProvider api={api}>
                <MemoryRouter initialEntries={[route]}>
                  {children}
                  <LocationProbe />
                </MemoryRouter>
              </SandboxesApiProvider>
            </SnackbarProvider>
          </ThemeProvider>
        </QueryClientProvider>
      </HelmetProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  const result = render(
    <Routes>
      <Route path="/dashboard/sandboxes" element={element} />
      <Route path="/dashboard/sandboxes/:id/:tab?" element={element} />
    </Routes>,
    { wrapper: Wrapper },
  );
  return { ...result, api, queryClient };
}
