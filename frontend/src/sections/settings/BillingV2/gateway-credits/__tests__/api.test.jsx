import React from "react";
import PropTypes from "prop-types";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { handleError } from "src/utils/queryErrorHandler";

const { get, snackbar } = vi.hoisted(() => ({
  get: vi.fn(),
  snackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: { gateway: {
    managedCredits: "/agentcc/managed-credits/",
    managedCreditStatement: "/agentcc/managed-credits/statement/",
  } },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: snackbar }));

import { creditKeys, useCreditStatement, useGatewayCreditSummary } from "../api";

function Probe() {
  const { isError } = useGatewayCreditSummary();
  return <div>{isError ? "summary failed" : "loading"}</div>;
}

function Providers({ children }) {
  // The app's own QueryCache error handler (App.jsx), which toasts a failed
  // read unless the query says its caller reports the failure.
  const client = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Providers.propTypes = { children: PropTypes.node };

describe("useGatewayCreditSummary", () => {
  // Every caller reports a failed summary itself: Billing shows its own error,
  // the nav hides central billing, and the models catalog keeps its strip. A
  // build without the ledger answers 501 billing_unavailable, which must not
  // also raise the app-wide "Something went wrong" toast.
  it("leaves a failed read to its caller, with no app-wide toast", async () => {
    get.mockRejectedValueOnce({
      statusCode: 501,
      result: "billing_unavailable",
    });
    render(<Probe />, { wrapper: Providers });
    await waitFor(() =>
      expect(screen.getByText("summary failed")).toBeInTheDocument(),
    );
    expect(snackbar).not.toHaveBeenCalled();
  });
});


describe("monthly statement query isolation", () => {
  it("never fetches an empty month", async () => {
    get.mockReset();
    const { result } = renderHook(() => useCreditStatement(""), { wrapper: Providers });
    expect(result.current.fetchStatus).toBe("idle");
    expect(result.current.data).toBeUndefined();
    expect(get).not.toHaveBeenCalled();
  });

  it("keeps UTC month keys distinct and does not show previous numbers under a new month", async () => {
    get.mockReset();
    let finish;
    const next = new Promise((resolve) => { finish = resolve; });
    get.mockImplementation(async (_url, { params }) => params.month === "2026-09"
      ? { data: { result: { month: "2026-09", closing_balance: "9.00" } } } : next);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    const { result, rerender } = renderHook(({ month }) => useCreditStatement(month), {
      initialProps: { month: "2026-09" }, wrapper,
    });
    await waitFor(() => expect(result.current.data?.closing_balance).toBe("9.00"));
    rerender({ month: "2026-10" });
    expect(result.current.data).toBeUndefined();
    expect(result.current.isLoading).toBe(true);
    expect(get).toHaveBeenCalledWith("/agentcc/managed-credits/statement/", { params: { month: "2026-10" } });
    await act(async () => finish({ data: { result: { month: "2026-10", closing_balance: "10.00" } } }));
    await waitFor(() => expect(result.current.data?.closing_balance).toBe("10.00"));
    expect(client.getQueryData(creditKeys.statement("2026-09")).data.result.closing_balance).toBe("9.00");
    expect(client.getQueryData(creditKeys.statement("2026-10")).data.result.closing_balance).toBe("10.00");
    client.clear();
  });
});
