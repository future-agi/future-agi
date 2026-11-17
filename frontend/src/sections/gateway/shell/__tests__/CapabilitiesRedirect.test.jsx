import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes, useLocation } from "react-router-dom";
import { act, cleanup, render, screen } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { paths } from "src/routes/paths";

import CapabilitiesRedirect from "../CapabilitiesRedirect";

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: vi.fn() } };
});

const suffix = "?tab=limits&return=credits#saved-view";
const clients = [];

// eslint-disable-next-line react/prop-types
function Destination({ name }) {
  const location = useLocation();
  return (
    <>
      <h1>{name}</h1>
      <div>{`${location.pathname}${location.search}${location.hash}`}</div>
    </>
  );
}

function renderRedirect({ cached, retry = false } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry, retryDelay: 60000 } },
  });
  clients.push(client);
  if (cached) client.setQueryData(["agentcc-managed-models"], cached);
  return render(
    <QueryClientProvider client={client}>
      <Routes>
        <Route
          path={paths.dashboard.gateway.capabilities}
          element={<CapabilitiesRedirect />}
        />
        <Route
          path={paths.dashboard.gateway.models}
          element={<Destination name="Model catalog" />}
        />
        <Route
          path={paths.dashboard.gateway.overview}
          element={<Destination name="Gateway overview" />}
        />
      </Routes>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.history.replaceState(
    {},
    "",
    `${paths.dashboard.gateway.capabilities}${suffix}`,
  );
  axios.get.mockReset();
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  window.history.replaceState({}, "", "/");
});

describe("CapabilitiesRedirect", () => {
  it("announces pending availability and preserves query/hash on the confirmed catalog redirect", async () => {
    let finish;
    axios.get.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    renderRedirect();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Checking model availability...",
    );
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(window.location.pathname).toBe(paths.dashboard.gateway.capabilities);
    expect(axios.get).toHaveBeenCalledWith(endpoints.gateway.managedModels, {
      timeout: 30000,
    });
    await act(async () => {
      finish({ data: { result: { available: true, enabled: false } } });
    });
    expect(
      await screen.findByRole("heading", { name: "Model catalog" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(`${paths.dashboard.gateway.models}${suffix}`),
    ).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it.each([
    ["unavailable", { available: false }],
    ["missing availability", {}],
    ["non-boolean availability", { available: "true" }],
  ])(
    "uses Overview for %s without dropping query/hash",
    async (_label, result) => {
      axios.get.mockResolvedValue({ data: { result } });
      renderRedirect();
      expect(
        await screen.findByRole("heading", { name: "Gateway overview" }),
      ).toBeInTheDocument();
      expect(
        screen.getByText(`${paths.dashboard.gateway.overview}${suffix}`),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "Model catalog" }),
      ).not.toBeInTheDocument();
    },
  );

  it.each([false, 3])(
    "preserves query/hash on a failed status read with retry=%s",
    async (retry) => {
      axios.get.mockRejectedValue(new Error("status read failed"));
      renderRedirect({ retry });
      // Also covers failureCount > 0 while the optional query awaits its retry.
      expect(
        await screen.findByRole("heading", { name: "Gateway overview" }),
      ).toBeInTheDocument();
      expect(
        screen.getByText(`${paths.dashboard.gateway.overview}${suffix}`),
      ).toBeInTheDocument();
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      expect(screen.queryByText("status read failed")).not.toBeInTheDocument();
    },
  );

  it("uses authoritative cached availability without waiting for another optional read", () => {
    renderRedirect({ cached: { available: true, enabled: false } });
    expect(
      screen.getByRole("heading", { name: "Model catalog" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(`${paths.dashboard.gateway.models}${suffix}`),
    ).toBeInTheDocument();
    expect(axios.get).not.toHaveBeenCalled();
  });
});
