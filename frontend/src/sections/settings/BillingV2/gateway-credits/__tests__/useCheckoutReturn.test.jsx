/* eslint-disable react/prop-types */
import React from "react";
import { act, render, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { creditKeys } from "../api";
import { useCheckoutReturn } from "../useCheckoutReturn";

const state = vi.hoisted(() => ({ status: vi.fn(), announce: vi.fn() }));
vi.mock("../api", async (original) => ({
  ...(await original()),
  fetchCheckoutStatus: (...args) => state.status(...args),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: (...args) => state.announce(...args) }));

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
const caches = [];
function mount(query = "topup=success&session_id=cs_port") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  caches.push(client);
  const where = {};
  function Page() {
    useCheckoutReturn();
    const location = useLocation();
    Object.assign(where, { search: location.search, hash: location.hash });
    return null;
  }
  return {
    client, where,
    render: () => render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[
      `/dashboard/settings/billing?${query}&keep=1#ai-gateway-credits`,
    ]}><Page /></MemoryRouter></QueryClientProvider>),
  };
}
beforeEach(() => { state.status.mockReset(); state.announce.mockReset(); });
afterEach(() => { caches.splice(0).forEach((client) => client.clear()); vi.useRealTimers(); });

describe("checkout return on the central ledger", () => {
  it.each(["topup=success&session_id=cs_port", "checkout_session=cs_port"])(
    "refreshes summary, entries and both inactive statement months before confirming (%s)", async (query) => {
      const h = mount(query);
      const keys = [creditKeys.summary, creditKeys.entries(), creditKeys.statement("2026-09"), creditKeys.statement("2026-10")];
      const pending = keys.map(deferred);
      const reads = keys.map((key, i) => vi.fn().mockResolvedValueOnce({ old: true }).mockImplementation(() => pending[i].promise));
      await Promise.all(keys.map((queryKey, i) => h.client.fetchQuery({ queryKey, queryFn: reads[i] })));
      const cancel = vi.spyOn(h.client, "cancelQueries");
      state.status.mockResolvedValue({ credited: true });
      h.render();
      await waitFor(() => reads.forEach((fn) => expect(fn).toHaveBeenCalledTimes(2)));
      expect(cancel).toHaveBeenCalledWith({ queryKey: creditKeys.all });
      expect(state.announce).not.toHaveBeenCalled();
      await act(async () => { pending.slice(0, -1).forEach((item) => item.resolve({ refreshed: true })); });
      expect(state.announce).not.toHaveBeenCalled();
      await act(async () => { pending.at(-1).resolve({ refreshed: true }); });
      await waitFor(() => expect(state.announce).toHaveBeenCalledTimes(1));
      expect(state.announce).toHaveBeenCalledWith("Credits added to your AI Gateway balance.", { variant: "success" });
      expect(state.status).toHaveBeenCalledWith("cs_port");
      expect(h.where).toEqual({ search: "?keep=1", hash: "#ai-gateway-credits" });
      keys.forEach((key) => expect(h.client.getQueryData(key)).toEqual({ refreshed: true }));
    },
  );

  it("keeps polling the URL session when a pending response has no session id", async () => {
    vi.useFakeTimers();
    const h = mount();
    const refresh = vi.spyOn(h.client, "invalidateQueries").mockResolvedValue();
    state.status.mockResolvedValueOnce({ credited: false, session_id: null })
      .mockResolvedValue({ credited: true, session_id: "cs_port" });
    h.render();
    await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
    expect(state.status.mock.calls).toEqual([["cs_port"], ["cs_port"]]);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(state.announce).toHaveBeenCalledWith("Credits added to your AI Gateway balance.", { variant: "success" });
    expect(h.where.search).toBe("?keep=1");
  });

  it("keeps the legacy return session through two pending-null replies and stops after completion", async () => {
    vi.useFakeTimers();
    const h = mount("checkout_session=cs_pending");
    const refresh = vi.spyOn(h.client, "invalidateQueries").mockResolvedValue();
    state.status
      .mockResolvedValueOnce({ session_id: null, status: "pending", credited: false })
      .mockResolvedValueOnce({ session_id: null, status: "pending", credited: false })
      .mockResolvedValue({ session_id: "cs_pending", status: "complete", credited: true });
    h.render();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(state.status).toHaveBeenCalledTimes(1);
    expect(state.announce).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
    expect(h.where.search).toContain("checkout_session=cs_pending");
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(state.status).toHaveBeenCalledTimes(2);
    expect(state.announce).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
    expect(h.where.search).toContain("checkout_session=cs_pending");
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(state.status).toHaveBeenCalledTimes(3);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(state.announce).toHaveBeenCalledWith("Credits added to your AI Gateway balance.", { variant: "success" });
    expect(h.where).toEqual({ search: "?keep=1", hash: "#ai-gateway-credits" });
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(state.status).toHaveBeenCalledTimes(3);
    expect(state.status.mock.calls).toEqual([["cs_pending"], ["cs_pending"], ["cs_pending"]]);
    expect(state.announce).toHaveBeenCalledTimes(1);
  });

  it("cancels without confirming or making a ledger request", () => {
    const h = mount("topup=cancel");
    h.render();
    expect(state.status).not.toHaveBeenCalled();
    expect(state.announce).toHaveBeenCalledWith("Top-up cancelled. No payment was taken.", { variant: "info" });
    expect(h.where).toEqual({ search: "?keep=1", hash: "#ai-gateway-credits" });
  });

  it("does not refresh or confirm a pending payment", async () => {
    const h = mount();
    const refresh = vi.spyOn(h.client, "invalidateQueries");
    state.status.mockResolvedValue({ credited: false });
    h.render();
    await waitFor(() => expect(state.status).toHaveBeenCalledTimes(1));
    expect(refresh).not.toHaveBeenCalled();
    expect(state.announce).not.toHaveBeenCalled();
    expect(h.where.search).toContain("session_id=cs_port");
  });

  it("never confirms a credited payment whose ledger refresh fails, and bounds retries", async () => {
    vi.useFakeTimers();
    const h = mount();
    const refresh = vi.spyOn(h.client, "invalidateQueries").mockRejectedValue(new Error("ledger unavailable"));
    state.status.mockResolvedValue({ credited: true });
    h.render();
    await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
    expect(state.status).toHaveBeenCalledTimes(20);
    expect(refresh).toHaveBeenCalledTimes(20);
    expect(state.announce).toHaveBeenCalledTimes(1);
    expect(state.announce).toHaveBeenCalledWith("Your payment is still being processed. The balance updates when it settles.", { variant: "info" });
    expect(h.where.search).toBe("?keep=1");
  });

  it("does not announce or clear a return after unmount during refresh", async () => {
    const h = mount();
    const pending = deferred();
    vi.spyOn(h.client, "invalidateQueries").mockReturnValue(pending.promise);
    state.status.mockResolvedValue({ credited: true });
    const view = h.render();
    await waitFor(() => expect(h.client.invalidateQueries).toHaveBeenCalled());
    view.unmount();
    await act(async () => pending.resolve());
    expect(state.announce).not.toHaveBeenCalled();
    expect(h.where.search).toContain("session_id=cs_port");
  });

  it("leaves card returns for PaymentMethods and makes no credit request", async () => {
    const h = mount("card=added&session_id=cs_card");
    h.render();
    expect(state.status).not.toHaveBeenCalled();
    expect(state.announce).not.toHaveBeenCalled();
    expect(h.where.search).toBe("?card=added&session_id=cs_card&keep=1");
  });
});
