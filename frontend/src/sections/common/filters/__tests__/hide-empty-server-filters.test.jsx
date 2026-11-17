// The results endpoint (dataset_results.py PILL_FIELDS) refuses the switch's
// expression pill with a 400, and its raw sentence ("filters: Unsupported
// filter type in this checkpoint.") reached the app toast right after a run
// started: the switch's `Hides N rows` count asked for it. A real server is
// never asked for that count now, and where the count is read (the
// prototype's mock) a refused read is never a toast. The switch stays.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import HideEmptySwitch from "../HideEmptySwitch";
import {
  HIDE_EMPTY_EXPR,
  SERVER_FILTER_TYPES,
  canCountHideEmpty,
  serverAcceptsPills,
  withHideEmpty,
} from "../hideEmpty";
import { handleError } from "src/utils/queryErrorHandler";

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
}));
let prototype = false;
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => prototype,
}));
const fetchResultIds = vi.fn();
vi.mock("src/api/develop/run-results", () => ({
  runResultsKey: (id) => ["run-results", id],
  fetchResultIds: (...a) => fetchResultIds(...a),
}));
const patch = vi.fn();
vi.mock("src/sections/experiment-detail/results/useResultsUrlState", () => ({
  default: () => ({ pills: [], patch: (...a) => patch(...a) }),
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children, title }) => (
    <span data-tip={typeof title === "string" ? title : ""}>{children}</span>
  ),
}));

const REFUSED = {
  statusCode: 400,
  result: "filters: Unsupported filter type in this checkpoint.",
};
const PLAIN = "Leaves out the rows where no run in view has an output";

const renderSwitch = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false } },
          queryCache: new QueryCache({ onError: handleError }),
        })
      }
    >
      <HideEmptySwitch datasetId="d1" view={{ runIds: ["a"], pills: [] }} />
    </QueryClientProvider>,
  );
const settle = () => new Promise((r) => setTimeout(r, 30));
const sentTypes = () =>
  fetchResultIds.mock.calls.flatMap(([, view]) =>
    (view?.pills ?? []).map((p) => p.type),
  );

describe("Hide rows with no output: the count's read", () => {
  beforeEach(() => {
    enqueueSnackbar.mockReset();
    fetchResultIds.mockReset();
    patch.mockReset();
    prototype = false;
  });

  it("knows which filter types the results endpoint accepts", () => {
    expect(SERVER_FILTER_TYPES).not.toContain("expression");
    expect(serverAcceptsPills([])).toBe(true);
    expect(
      serverAcceptsPills([{ type: "fails", metric: "m", run: "any_run" }]),
    ).toBe(true);
    expect(serverAcceptsPills(withHideEmpty([], true))).toBe(false);
    expect(serverAcceptsPills([{ type: "movement", metric: "m" }])).toBe(false);
    expect(serverAcceptsPills([{ type: "error", cause: "timeout" }])).toBe(
      false,
    );
    expect(canCountHideEmpty([], { prototype: false })).toBe(false);
    expect(canCountHideEmpty([], { prototype: true })).toBe(true);
  });

  it("sends no unsupported filter type to a real server, and the switch still works", async () => {
    renderSwitch();
    await settle();
    expect(fetchResultIds).not.toHaveBeenCalled();
    const tip = screen.getByTestId("results-hide-empty").parentElement;
    expect(tip.getAttribute("data-tip")).toBe(PLAIN);
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Hide rows with no output" }),
    );
    expect(patch).toHaveBeenCalledWith({
      filters: JSON.stringify([{ type: "expression", expr: HIDE_EMPTY_EXPR }]),
    });
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("never toasts a refused count read", async () => {
    prototype = true;
    fetchResultIds.mockRejectedValue(REFUSED);
    renderSwitch();
    await waitFor(() => expect(fetchResultIds).toHaveBeenCalledTimes(2));
    await settle();
    expect(sentTypes()).toEqual(["expression"]);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(
      screen
        .getByTestId("results-hide-empty")
        .parentElement.getAttribute("data-tip"),
    ).toBe(PLAIN);
    expect(
      screen.getByRole("checkbox", { name: "Hide rows with no output" }),
    ).toBeTruthy();
  });

  it("the app's handler still toasts a read that does not handle its error", () => {
    handleError(REFUSED, { options: { meta: {} } });
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    enqueueSnackbar.mockReset();
    handleError(REFUSED, { options: { meta: { errorHandled: true } } });
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });
});
