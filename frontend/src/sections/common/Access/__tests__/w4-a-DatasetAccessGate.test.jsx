// The dataset page for someone the server refuses to show it to (W4-5).
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

const state = vi.hoisted(() => ({
  query: { data: undefined, error: null, isLoading: false },
  post: vi.fn(),
  ask: { isSuccess: false, isError: false, isPending: false, error: null },
}));
vi.mock("src/api/develop/dataset-access", () => ({
  useDatasetAccessQuery: () => state.query,
  useRequestDatasetAccess: () => ({ ...state.ask, mutate: state.post }),
}));
vi.mock("src/routes/hooks", () => ({ useParams: () => ({ dataset: "ds" }) }));

const { default: DatasetAccessGate } = await import("../DatasetAccessGate");
const { viewDenialOf, deniedTitle } = await import("../accessModel");

const denial = {
  status: false,
  code: "permission_denied",
  error: "You need view access to this dataset.",
  result:
    "You need view access to this dataset. Ask Asha Rao, Ravi Menon or Meera Iyer.",
  missing_permission: "view",
  dataset_id: "ds",
  dataset_name: "Support agent - golden set",
  ask: [
    { id: "a", name: "Asha Rao" },
    { id: "r", name: "Ravi Menon" },
    { id: "m", name: "Meera Iyer" },
  ],
  statusCode: 403,
};

const renderGate = () =>
  render(
    <MemoryRouter>
      <DatasetAccessGate>
        <div>the dataset page</div>
      </DatasetAccessGate>
    </MemoryRouter>,
  );

afterEach(() => {
  cleanup();
  state.post.mockReset();
  state.ask = { isSuccess: false, isError: false, isPending: false };
});

describe("viewDenialOf", () => {
  it("only a 403 for view (or a key that can't read datasets)", () => {
    expect(viewDenialOf(denial)).toBe(denial);
    expect(
      viewDenialOf({ ...denial, missing_permission: "read_datasets" }),
    ).toBeTruthy();
    expect(viewDenialOf({ ...denial, missing_permission: "edit" })).toBeNull();
    expect(viewDenialOf({ ...denial, statusCode: 404 })).toBeNull();
    expect(viewDenialOf(null)).toBeNull();
    expect(deniedTitle(denial)).toBe(
      "You don't have access to “Support agent - golden set”",
    );
    expect(deniedTitle({})).toBe("You don't have access to this dataset");
  });
});

describe("DatasetAccessGate", () => {
  // Review fix (W4 A must-fix 4): the page no longer waits for the access
  // read, so a server without it never shows a blank page.
  it("renders the page while the access read runs", () => {
    state.query = { data: undefined, error: null, isLoading: true };
    renderGate();
    expect(screen.getByText("the dataset page")).toBeTruthy();
    expect(screen.queryByTestId("dataset-access-denied")).toBeNull();
  });

  it("only a view 403 replaces the page: other refusals and errors keep it", () => {
    for (const error of [
      { ...denial, missing_permission: "edit" },
      { statusCode: 500, error: "Server error" },
      { message: "Network Error" },
    ]) {
      state.query = { data: undefined, error, isLoading: false };
      renderGate();
      expect(screen.getByText("the dataset page")).toBeTruthy();
      cleanup();
    }
    state.query = { data: undefined, error: denial, isLoading: false };
    renderGate();
    expect(screen.queryByText("the dataset page")).toBeNull();
  });

  it("renders the page when the read is allowed or the endpoint is absent", () => {
    state.query = { data: { me: {} }, error: null, isLoading: false };
    renderGate();
    expect(screen.getByText("the dataset page")).toBeTruthy();
    cleanup();
    state.query = {
      data: undefined,
      error: { statusCode: 404 },
      isLoading: false,
    };
    renderGate();
    expect(screen.getByText("the dataset page")).toBeTruthy();
  });

  it("a refused reader sees who can grant access and can ask for view", () => {
    state.query = { data: undefined, error: denial, isLoading: false };
    renderGate();
    expect(screen.queryByText("the dataset page")).toBeNull();
    expect(
      screen.getByText("You don't have access to “Support agent - golden set”"),
    ).toBeTruthy();
    expect(screen.getByTestId("access-denied-note").textContent).toContain(
      "You need view access to this dataset. Ask Asha Rao, Ravi Menon or Meera Iyer.",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Request view access" }),
    );
    expect(state.post).toHaveBeenCalledWith("view");
    expect(screen.getByRole("link", { name: /Back to datasets/ })).toBeTruthy();
  });

  it("a refused request says why", () => {
    state.query = { data: undefined, error: denial, isLoading: false };
    state.ask = {
      isSuccess: false,
      isError: true,
      isPending: false,
      error: { error: "You already have view access to this dataset." },
    };
    renderGate();
    expect(screen.getByRole("alert").textContent).toBe(
      "You already have view access to this dataset.",
    );
  });

  it("an earlier request reads Requested, and a refetch keeps the page", () => {
    state.query = {
      data: undefined,
      error: { ...denial, requested: true },
      isLoading: false,
    };
    const { rerender } = renderGate();
    expect(
      screen.getByText("Requested · an admin sees it under Access"),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Request view access" }),
    ).toBeNull();
    // The refetch after a request: no error while it runs.
    state.query = { data: undefined, error: null, isLoading: true };
    rerender(
      <MemoryRouter>
        <DatasetAccessGate>
          <div>the dataset page</div>
        </DatasetAccessGate>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("dataset-access-denied")).toBeTruthy();
    expect(screen.queryByText("the dataset page")).toBeNull();
    // Granted meanwhile: the page opens.
    state.query = { data: { me: {} }, error: null, isLoading: false };
    rerender(
      <MemoryRouter>
        <DatasetAccessGate>
          <div>the dataset page</div>
        </DatasetAccessGate>
      </MemoryRouter>,
    );
    expect(screen.getByText("the dataset page")).toBeTruthy();
  });
});
