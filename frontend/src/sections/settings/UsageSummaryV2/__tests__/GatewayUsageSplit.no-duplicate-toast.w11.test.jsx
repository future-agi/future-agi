import React from "react";
import { MemoryRouter } from "react-router-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { enqueueSnackbar } from "notistack";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { handleError } from "src/utils/queryErrorHandler";

import GatewayUsageSplit from "../GatewayUsageSplit";

// W11-USAGE-TOAST: a failed credit statement already
// shows "Managed spend unavailable" with Retry on the card, so the app-wide
// "Something went wrong" toast must not pop up for that same request. Other
// failing requests keep their toast.

const CONTROL_URL = "/w11/control-request/";

const state = vi.hoisted(() => ({
  statement: undefined,
  statementError: false,
  calls: [],
}));

// The shape src/utils/axios.js's response interceptor rejects with for the
// walk's simulated 503: the response body spread plus statusCode. A bare Error
// has no `result`, so the global handler would never toast for it.
function failed503() {
  return {
    status: false,
    result: "The managed credit operation could not be completed.",
    code: "billing_unavailable",
    statusCode: 503,
  };
}

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const gw = actual.endpoints.gateway;
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        state.calls.push(url);
        if (url === gw.managedCreditStatement) {
          if (state.statementError) throw failed503();
          return { data: { result: state.statement } };
        }
        if (url === gw.analyticsCost) {
          return {
            data: {
              result: {
                group_by: "billing_mode",
                breakdown: [{ name: "Own key", total_cost: "12" }],
              },
            },
          };
        }
        if (url === CONTROL_URL) throw failed503();
        throw new Error(`unmocked GET ${url}`);
      }),
    },
  };
});

// A different request elsewhere on the page, with no opt-out of its own.
function OtherFailingRequest() {
  const other = useQuery({
    queryKey: ["w11-usage-toast", "control-request"],
    queryFn: () => axios.get(CONTROL_URL),
    retry: false,
  });
  return other.isError ? <p>Other request failed</p> : null;
}

function renderUi(ui) {
  // Built like src/app.jsx: every failed read goes through the app's handler.
  const client = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <MemoryRouter initialEntries={["/dashboard/settings/usage-summary"]}>
          {ui}
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const NOW = new Date("2026-10-20T12:00:00Z");
const statementCalls = () =>
  state.calls.filter((url) => url === "/agentcc/managed-credits/statement/");

describe("W11-USAGE-TOAST Usage Summary shows one error for a failed statement", () => {
  beforeEach(() => {
    vi.mocked(enqueueSnackbar).mockClear();
    state.statement = undefined;
    state.statementError = false;
    state.calls = [];
  });

  it("W11-USAGE-TOAST control: a different failing request still gets the generic toast", async () => {
    renderUi(<OtherFailingRequest />);
    expect(await screen.findByText("Other request failed")).toBeInTheDocument();
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith("Something went wrong", {
      variant: "error",
    });
  });

  it("W11-USAGE-TOAST a failed statement shows unavailable + Retry and no toast, while the other failure still toasts", async () => {
    state.statementError = true;
    renderUi(
      <>
        <GatewayUsageSplit now={NOW} />
        <OtherFailingRequest />
      </>,
    );
    expect(
      await screen.findByText("Managed spend unavailable"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Retry managed spend" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Other request failed")).toBeInTheDocument();
    expect(statementCalls()).toHaveLength(1);
    // Exactly one toast, and it is the other request's: the handler is live
    // in this client, and the statement failure is reported only on the card.
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith("Something went wrong", {
      variant: "error",
    });
  });

  it("W11-USAGE-TOAST Retry after a failed statement shows the total, with no toast at any point", async () => {
    state.statementError = true;
    renderUi(<GatewayUsageSplit now={NOW} />);
    expect(
      await screen.findByText("Managed spend unavailable"),
    ).toBeInTheDocument();
    expect(statementCalls()).toHaveLength(1);
    expect(enqueueSnackbar).not.toHaveBeenCalled();

    state.statementError = false;
    state.statement = { spend_by_model: [{ spend_usd: 25, count: 4 }] };
    fireEvent.click(
      screen.getByRole("button", { name: "Retry managed spend" }),
    );
    expect(await screen.findByTitle("25 USD")).toHaveTextContent("$25");
    expect(screen.getByText(/4 requests/)).toBeInTheDocument();
    expect(
      screen.queryByText("Managed spend unavailable"),
    ).not.toBeInTheDocument();
    expect(statementCalls()).toHaveLength(2);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("W11-USAGE-TOAST a successful empty statement still shows a real $0 without a toast", async () => {
    state.statement = { spend_by_model: [] };
    renderUi(<GatewayUsageSplit now={NOW} />);
    expect(await screen.findByTitle("0 USD")).toHaveTextContent("$0");
    expect(screen.getByText(/0 requests/)).toBeInTheDocument();
    expect(
      screen.queryByText("Managed spend unavailable"),
    ).not.toBeInTheDocument();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });
});
