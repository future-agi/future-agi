import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider, useInfiniteQuery } from "@tanstack/react-query";
import { creditKeys, fetchCreditEntries, useCreditStatement, useGatewayCreditSummary } from "../api";
import { useCheckoutReturn } from "../useCheckoutReturn";

const { get, announce } = vi.hoisted(() => ({ get: vi.fn(), announce: vi.fn() }));
vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return { ...actual, default: { ...actual.default, get } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: announce }));
const response = (result) => ({ data: { result } });
const confirmed = "Credits added to your AI Gateway balance.";
function deferred() {
  let resolve;
  const promise = new Promise((yes) => { resolve = yes; });
  return { promise, resolve };
}
afterEach(() => { cleanup(); get.mockReset(); announce.mockReset(); });

// Port of the three uncached in-flight replacement cases from the original
// wallet hook. The confirmation is now the v2 success notification and URL
// clearing, not the removed hook's status.data. All reads are real queries.
describe("post-fulfillment credit reads", () => {
  it.each(["summary", "entries", "statement"])(
    "replaces an uncached in-flight %s read before confirming payment",
    async (pending) => {
      const first = deferred();
      const fresh = deferred();
      const checkout = deferred();
      const calls = { summary: 0, entries: 0, statement: 0, checkout: 0 };
      const old = {
        summary: { available_usd: 27.5 },
        entries: { entries: [{ id: "old-entry" }], next_cursor: null },
        statement: { closing_balance: "27.5" },
      };
      const next = {
        summary: { available_usd: 67.5 },
        entries: { entries: [{ id: "new-topup" }], next_cursor: null },
        statement: { closing_balance: "67.5" },
      };
      get.mockImplementation((url) => {
        const kind = url.includes("/checkout/") ? "checkout"
          : url.endsWith("/entries/") ? "entries"
            : url.endsWith("/statement/") ? "statement" : "summary";
        calls[kind] += 1;
        if (kind === "checkout") return checkout.promise;
        if (kind === pending) return calls[kind] === 1 ? first.promise : fresh.promise;
        return Promise.resolve(response(calls[kind] === 1 ? old[kind] : next[kind]));
      });
      const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
      const { result } = renderHook(() => {
        const summary = useGatewayCreditSummary();
        const entries = useInfiniteQuery({
          queryKey: creditKeys.entries(""),
          queryFn: ({ pageParam }) => fetchCreditEntries({ kind: "", cursor: pageParam }),
          initialPageParam: null,
          getNextPageParam: (page) => page.next_cursor || undefined,
          retry: false,
        });
        const statement = useCreditStatement("2026-10");
        useCheckoutReturn();
        return { summary, entries, statement, location: useLocation() };
      }, { wrapper: ({ children }) => (
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={["/dashboard/settings/billing?topup=success&session_id=cs_fixture_pending&x=1#ai-gateway-credits"]}>
            {children}
          </MemoryRouter>
        </QueryClientProvider>
      ) });
      await waitFor(() => expect(calls[pending]).toBe(1));
      await act(async () => checkout.resolve(response({ credited: true })));
      await waitFor(() => expect(calls[pending]).toBe(2));
      expect(announce).not.toHaveBeenCalledWith(confirmed, expect.anything());
      expect(result.current.location.search).toContain("session_id=cs_fixture_pending");
      await act(async () => fresh.resolve(response(next[pending])));
      await waitFor(() => expect(announce).toHaveBeenCalledWith(confirmed, { variant: "success" }));
      // A transport ignoring cancellation cannot overwrite the post-payment read.
      await act(async () => first.resolve(response(old[pending])));
      expect(result.current.summary.data.available_usd).toBe(67.5);
      expect(result.current.entries.data.pages[0].entries).toEqual([{ id: "new-topup" }]);
      expect(result.current.statement.data.closing_balance).toBe("67.5");
      expect(result.current.location.search).toBe("?x=1");
      expect(result.current.location.hash).toBe("#ai-gateway-credits");
      expect(calls.checkout).toBe(1);
      client.clear();
    },
  );
});
