/* eslint-disable react/prop-types */
import React from "react";
import { act, render, renderHook, waitFor } from "@testing-library/react";
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
vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => state.announce(...args),
}));

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
const caches = [];
function mount(query = "topup=success&session_id=cs_port") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  caches.push(client);
  const where = {};
  function Page() {
    useCheckoutReturn();
    const location = useLocation();
    Object.assign(where, { search: location.search, hash: location.hash });
    return null;
  }
  return {
    client,
    where,
    render: () =>
      render(
        <QueryClientProvider client={client}>
          <MemoryRouter
            initialEntries={[
              `/dashboard/settings/billing?${query}&keep=1#ai-gateway-credits`,
            ]}
          >
            <Page />
          </MemoryRouter>
        </QueryClientProvider>,
      ),
  };
}
beforeEach(() => {
  state.status.mockReset();
  state.announce.mockReset();
});
afterEach(() => {
  caches.splice(0).forEach((client) => client.clear());
  vi.useRealTimers();
});

describe("checkout return on the central ledger", () => {
  it.each(["topup=success&session_id=cs_port", "checkout_session=cs_port"])(
    "refreshes summary, entries and both inactive statement months before confirming (%s)",
    async (query) => {
      const h = mount(query);
      const keys = [
        creditKeys.summary,
        creditKeys.entries(),
        creditKeys.statement("2026-09"),
        creditKeys.statement("2026-10"),
      ];
      const pending = keys.map(deferred);
      const reads = keys.map((key, i) =>
        vi
          .fn()
          .mockResolvedValueOnce({ old: true })
          .mockImplementation(() => pending[i].promise),
      );
      await Promise.all(
        keys.map((queryKey, i) =>
          h.client.fetchQuery({ queryKey, queryFn: reads[i] }),
        ),
      );
      const cancel = vi.spyOn(h.client, "cancelQueries");
      state.status.mockResolvedValue({ credited: true });
      h.render();
      await waitFor(() =>
        reads.forEach((fn) => expect(fn).toHaveBeenCalledTimes(2)),
      );
      expect(cancel).toHaveBeenCalledWith({ queryKey: creditKeys.all });
      expect(state.announce).not.toHaveBeenCalled();
      await act(async () => {
        pending
          .slice(0, -1)
          .forEach((item) => item.resolve({ refreshed: true }));
      });
      expect(state.announce).not.toHaveBeenCalled();
      await act(async () => {
        pending.at(-1).resolve({ refreshed: true });
      });
      await waitFor(() => expect(state.announce).toHaveBeenCalledTimes(1));
      expect(state.announce).toHaveBeenCalledWith(
        "Credits added to your AI Gateway balance.",
        { variant: "success" },
      );
      expect(state.status).toHaveBeenCalledWith("cs_port");
      expect(h.where).toEqual({
        search: "?keep=1",
        hash: "#ai-gateway-credits",
      });
      keys.forEach((key) =>
        expect(h.client.getQueryData(key)).toEqual({ refreshed: true }),
      );
    },
  );

  it("keeps polling the URL session when a pending response has no session id", async () => {
    vi.useFakeTimers();
    const h = mount();
    const refresh = vi.spyOn(h.client, "invalidateQueries").mockResolvedValue();
    state.status
      .mockResolvedValueOnce({ credited: false, session_id: null })
      .mockResolvedValue({ credited: true, session_id: "cs_port" });
    h.render();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(state.status.mock.calls).toEqual([["cs_port"], ["cs_port"]]);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(state.announce).toHaveBeenCalledWith(
      "Credits added to your AI Gateway balance.",
      { variant: "success" },
    );
    expect(h.where.search).toBe("?keep=1");
  });

  it("keeps the legacy return session through two pending-null replies and stops after completion", async () => {
    vi.useFakeTimers();
    const h = mount("checkout_session=cs_pending");
    const refresh = vi.spyOn(h.client, "invalidateQueries").mockResolvedValue();
    state.status
      .mockResolvedValueOnce({
        session_id: null,
        status: "pending",
        credited: false,
      })
      .mockResolvedValueOnce({
        session_id: null,
        status: "pending",
        credited: false,
      })
      .mockResolvedValue({
        session_id: "cs_pending",
        status: "complete",
        credited: true,
      });
    h.render();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(state.status).toHaveBeenCalledTimes(1);
    expect(state.announce).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
    expect(h.where.search).toContain("checkout_session=cs_pending");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(state.status).toHaveBeenCalledTimes(2);
    expect(state.announce).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
    expect(h.where.search).toContain("checkout_session=cs_pending");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(state.status).toHaveBeenCalledTimes(3);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(state.announce).toHaveBeenCalledWith(
      "Credits added to your AI Gateway balance.",
      { variant: "success" },
    );
    expect(h.where).toEqual({ search: "?keep=1", hash: "#ai-gateway-credits" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(state.status).toHaveBeenCalledTimes(3);
    expect(state.status.mock.calls).toEqual([
      ["cs_pending"],
      ["cs_pending"],
      ["cs_pending"],
    ]);
    expect(state.announce).toHaveBeenCalledTimes(1);
  });

  it("cancels without confirming or making a ledger request", () => {
    const h = mount("topup=cancel");
    h.render();
    expect(state.status).not.toHaveBeenCalled();
    expect(state.announce).toHaveBeenCalledWith(
      "Top-up cancelled. No payment was taken.",
      { variant: "info" },
    );
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

  it.each([false, true])(
    "H7-C25-003 refreshes credits at the pending poll limit and once after a bounded delay (status error: %s)",
    async (statusError) => {
      vi.useFakeTimers();
      const h = mount();
      const keys = [
        creditKeys.summary,
        creditKeys.entries(),
        creditKeys.statement("2026-09"),
        creditKeys.statement("2026-10"),
      ];
      const reads = keys.map(() =>
        vi.fn().mockResolvedValue({ available_usd: 10 }),
      );
      await Promise.all(
        keys.map((queryKey, i) =>
          h.client.fetchQuery({ queryKey, queryFn: reads[i] }),
        ),
      );
      const refresh = vi.spyOn(h.client, "invalidateQueries");
      if (statusError)
        state.status.mockRejectedValue(new Error("checkout unavailable"));
      else state.status.mockResolvedValue({ credited: false });
      h.render();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(31_000);
      });
      expect(state.status).toHaveBeenCalledTimes(20);
      expect(refresh).toHaveBeenCalledTimes(1);
      expect(refresh).toHaveBeenCalledWith(
        { queryKey: creditKeys.all, refetchType: "all" },
        { throwOnError: true },
      );
      reads.forEach((read) => expect(read).toHaveBeenCalledTimes(2));
      expect(state.announce).toHaveBeenCalledTimes(1);
      expect(state.announce).toHaveBeenCalledWith(
        expect.stringContaining("Your payment is still being processed."),
        expect.objectContaining({ variant: "info" }),
      );
      expect(state.announce).not.toHaveBeenCalledWith(
        "Credits added to your AI Gateway balance.",
        expect.anything(),
      );

      // Settlement after the fast polling window is picked up without a reload.
      reads.forEach((read) => read.mockResolvedValue({ available_usd: 35 }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(refresh).toHaveBeenCalledTimes(2);
      keys.forEach((key) =>
        expect(h.client.getQueryData(key)).toEqual({ available_usd: 35 }),
      );
      expect(h.where).toEqual({
        search: "?keep=1",
        hash: "#ai-gateway-credits",
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(120_000);
      });
      expect(state.status).toHaveBeenCalledTimes(20);
      expect(refresh).toHaveBeenCalledTimes(2);
      expect(state.announce).toHaveBeenCalledTimes(1);
    },
  );

  it("H7-C25-003 still refreshes and confirms credit on poll 20 without a follow-up", async () => {
    vi.useFakeTimers();
    const h = mount();
    const refresh = vi.spyOn(h.client, "invalidateQueries").mockResolvedValue();
    let polls = 0;
    state.status.mockImplementation(async () => {
      polls += 1;
      return { credited: polls === 20 };
    });
    h.render();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });
    expect(state.status).toHaveBeenCalledTimes(20);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(state.announce).toHaveBeenCalledWith(
      "Credits added to your AI Gateway balance.",
      { variant: "success" },
    );
    expect(h.where).toEqual({ search: "?keep=1", hash: "#ai-gateway-credits" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(state.status).toHaveBeenCalledTimes(20);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(state.announce).toHaveBeenCalledTimes(1);
  });

  it("H7-C25-003 cancels the bounded follow-up when Billing unmounts", async () => {
    vi.useFakeTimers();
    const h = mount();
    const refresh = vi.spyOn(h.client, "invalidateQueries").mockResolvedValue();
    state.status.mockResolvedValue({ credited: false });
    const view = h.render();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });
    expect(refresh).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(state.status).toHaveBeenCalledTimes(20);
    expect(state.announce).toHaveBeenCalledTimes(1);
  });

  it("never confirms a credited payment whose ledger refresh fails, and bounds retries", async () => {
    vi.useFakeTimers();
    const h = mount();
    const refresh = vi
      .spyOn(h.client, "invalidateQueries")
      .mockRejectedValue(new Error("ledger unavailable"));
    state.status.mockResolvedValue({ credited: true });
    h.render();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });
    expect(state.status).toHaveBeenCalledTimes(20);
    expect(refresh).toHaveBeenCalledTimes(20);
    expect(state.announce).toHaveBeenCalledTimes(1);
    expect(state.announce).toHaveBeenCalledWith(
      expect.stringContaining("Your payment is still being processed."),
      expect.objectContaining({ variant: "info" }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(refresh).toHaveBeenCalledTimes(21);
    expect(h.where.search).toBe("?keep=1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(state.status).toHaveBeenCalledTimes(20);
    expect(refresh).toHaveBeenCalledTimes(21);
    expect(state.announce).toHaveBeenCalledTimes(1);
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

  // Billing walk B6: the return is confirming for as long as its parameters
  // stay in the URL (until credited, or until the single slow follow-up after
  // the poll limit), so Billing can say so without another toast.
  function mountHook(query) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    caches.push(client);
    vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    return renderHook(
      () => ({ checkout: useCheckoutReturn(), location: useLocation() }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>
            <MemoryRouter
              initialEntries={[
                `/dashboard/settings/billing?${query}&keep=1#ai-gateway-credits`,
              ]}
            >
              {children}
            </MemoryRouter>
          </QueryClientProvider>
        ),
      },
    );
  }

  it.each(["topup=success&session_id=cs_port", "checkout_session=cs_port"])(
    "B6 reports a pending return as confirming through the poll limit and follow-up, and not once its URL is cleared (%s)",
    async (query) => {
      vi.useFakeTimers();
      state.status.mockResolvedValue({
        session_id: null,
        status: "pending",
        credited: false,
      });
      const { result } = mountHook(query);
      expect(result.current.checkout).toEqual({ confirming: true });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(31_000);
      });
      expect(state.status).toHaveBeenCalledTimes(20);
      expect(state.announce).toHaveBeenCalledTimes(1);
      expect(result.current.checkout).toEqual({ confirming: true });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(result.current.location.search).toBe("?keep=1");
      expect(result.current.checkout).toEqual({ confirming: false });
    },
  );

  it.each(["topup=cancel", "topup=success", "card=added&session_id=cs_card"])(
    "B6 never reports a return without a checkout session as confirming (%s)",
    (query) => {
      const { result } = mountHook(query);
      expect(result.current.checkout).toEqual({ confirming: false });
      expect(state.status).not.toHaveBeenCalled();
    },
  );
});
