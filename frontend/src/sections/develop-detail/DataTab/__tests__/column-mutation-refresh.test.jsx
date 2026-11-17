import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import {
  clearTokens,
  getIsRefreshing,
  processQueue,
  setIsRefreshing,
  setRememberMe,
  setRefreshToken,
} from "src/auth/context/jwt/utils";
import {
  useAddColumnDrawerStore,
  useDeleteColumnStore,
  useDevelopFilterStore,
  useEditColumnNameStore,
} from "../../states";
import AddColumnDrawer from "../../AddColumn/AddColumnDrawer";
import EditColumnName from "../EditColumnName";
import DeleteColumn from "../DeleteColumn";

const mocks = vi.hoisted(() => ({ effect: vi.fn(), resetUser: vi.fn() }));
// Keep the real Axios interceptors, refreshTokenRequest and refresh queue.
// Only the adapter is replaced below: no HTTP or recaptcha traffic is allowed.
vi.mock("src/config-global", async (importOriginal) => ({
  ...(await importOriginal()),
  HOST_API: "http://127.0.0.1:1",
}));
vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: vi.fn().mockResolvedValue("test-recaptcha"),
}));
vi.mock("src/api/contracts/openapi-contract", () => ({
  assertContractedRequestConfig: (config) => config,
  assertContractedResponse: (response) => response,
  findOpenApiEndpoint: vi.fn(),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.effect }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: mocks.effect }));
vi.mock("src/sections/common/ux/toast", () => ({ notify: mocks.effect }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({
  resetUser: mocks.resetUser,
  trackEvent: mocks.effect,
  Events: {
    editColumnNameSuccessful: "renamed",
    deleteColumnSuccessful: "deleted",
  },
  PropertyName: { columnName: "columnName", columnType: "columnType" },
}));
vi.mock("src/pages/dashboard/Develop/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ setActionSource: vi.fn() }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: mocks.effect }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds-1", name: "Dataset" }],
  }),
}));
vi.mock("../Versions/changeSetStore", () => ({
  useCreatedColumnsStore: {
    getState: () => ({ add: mocks.effect, remove: mocks.effect }),
  },
}));
vi.mock("../../AddColumn/Expression/ExpressionColumnDialog", () => ({
  default: () => null,
}));
vi.mock("../../AddColumn/common", () => ({
  StaticColumns: [
    { label: "Text", value: "text", icon: "text", helpingText: "Text values" },
  ],
  DynamicColumns: [],
}));
vi.mock("src/components/FormTextField/FormTextFieldV2", async () => {
  const { Controller } = await import("react-hook-form");
  const { TextField } = await import("@mui/material");
  const { default: PropTypes } = await import("prop-types");
  const Field = ({ control, fieldName, label, disabled }) => (
    <Controller
      control={control}
      name={fieldName}
      render={({ field }) => (
        <TextField {...field} label={label} disabled={disabled} />
      )}
    />
  );
  Field.propTypes = {
    control: PropTypes.object,
    fieldName: PropTypes.string,
    label: PropTypes.string,
    disabled: PropTypes.bool,
  };
  return { default: Field };
});

const COLUMN = { id: "col-1", name: "Original" };
const FILTERS = [{ id: "filter-1", columnId: "col-1" }];
const actions = [
  {
    name: "add",
    method: "post",
    url: endpoints.develop.addColumn("ds-1"),
    button: "Add Column",
    field: "Column name",
    fallback: "Couldn't save the column",
  },
  {
    name: "rename",
    method: "put",
    url: endpoints.develop.updateColumnName("ds-1", "col-1"),
    button: "Save",
    field: "Column Name",
    fallback: "Couldn't save the column",
  },
  {
    name: "delete",
    method: "delete",
    url: endpoints.develop.deleteColumn("ds-1", "col-1"),
    button: "Delete",
    fallback: "Couldn't delete the column",
  },
];
const originalAdapter = axios.defaults.adapter;
let client;
let finishRefresh;
let adapter;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  setRememberMe(true);
  setRefreshToken("test-refresh");
  setIsRefreshing(false);
  finishRefresh = undefined;
  adapter = vi.fn(() => {
    throw new Error("Unexpected request: network is disabled in this test");
  });
  axios.defaults.adapter = adapter;
  useAddColumnDrawerStore.setState({ openAddColumnDrawer: false });
  useEditColumnNameStore.setState({ editColumnName: null });
  useDeleteColumnStore.setState({ deleteColumn: null });
  useDevelopFilterStore.setState({ filters: FILTERS });
});

afterEach(async () => {
  cleanup();
  // Drain the real queue even if a RED assertion failed before releasing it.
  finishRefresh?.();
  processQueue(null, "test-access");
  if (vi.isFakeTimers())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  setIsRefreshing(false);
  clearTokens();
  delete axios.defaults.headers.common.Authorization;
  client?.clear();
  axios.defaults.adapter = originalAdapter;
  vi.useRealTimers();
});

async function openAction(action) {
  const user = userEvent.setup();
  client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  vi.spyOn(client, "invalidateQueries");
  if (action.name === "add")
    useAddColumnDrawerStore.setState({ openAddColumnDrawer: true });
  if (action.name === "rename")
    useEditColumnNameStore.setState({ editColumnName: COLUMN });
  if (action.name === "delete")
    useDeleteColumnStore.setState({ deleteColumn: COLUMN });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/develop/ds-1"]}>
        <Routes>
          <Route
            path="/develop/:dataset"
            element={
              action.name === "add" ? (
                <AddColumnDrawer />
              ) : action.name === "rename" ? (
                <EditColumnName onSuccess={mocks.effect} />
              ) : (
                <DeleteColumn />
              )
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  if (action.name === "add")
    await user.click(screen.getByRole("button", { name: "Text", exact: true }));
  if (action.field) {
    const field = screen.getByRole("textbox", { name: action.field });
    await user.clear(field);
    await user.type(field, "Draft");
  }
}

function expectUnchanged(action) {
  expect(screen.getByRole("dialog")).toBeVisible();
  if (action.field)
    expect(screen.getByRole("textbox", { name: action.field })).toHaveValue(
      "Draft",
    );
  expect(useDevelopFilterStore.getState().filters).toEqual(FILTERS);
  expect(client.invalidateQueries).not.toHaveBeenCalled();
  expect(mocks.effect).not.toHaveBeenCalled();
  expect(mocks.resetUser).not.toHaveBeenCalled();
}

const response = (config, status, data) => ({
  config,
  status,
  statusText: String(status),
  headers: {},
  data,
});

// RED at the tests-only commit: the 401 has completed the original transport,
// so its Axios timeout cannot settle the subsequent refresh/queue promise.
// RED/GREEN execution is owed in the authorized, egress-blocked cloud runner.
describe.each(actions)("$name column auth deadline", (action) => {
  it.each(["refresh request", "existing refresh queue"])(
    "recovers after 15 seconds waiting for %s and never replays the expired mutation",
    async (waitingFor) => {
      const queued = waitingFor === "existing refresh queue";
      const columnRequests = [];
      const refreshRequests = [];
      let allowSuccess = false;
      adapter.mockImplementation(async (config) => {
        if (config.url === endpoints.auth.refreshToken) {
          refreshRequests.push(config);
          return new Promise((resolve) => {
            finishRefresh = () =>
              resolve(response(config, 200, { access: "test-access" }));
          });
        }
        if (config.url !== action.url || config.method !== action.method)
          throw new Error(
            "Unexpected request: network is disabled in this test",
          );
        columnRequests.push(config);
        if (allowSuccess)
          return response(config, 200, { result: { column_id: "new-1" } });
        throw Object.assign(new Error("Session expired"), {
          config,
          response: response(config, 401, { detail: "Session expired" }),
        });
      });
      await openAction(action);
      setIsRefreshing(queued);
      vi.useFakeTimers();
      act(() => {
        fireEvent.click(
          screen.getByRole("button", { name: action.button, exact: true }),
        );
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(columnRequests).toHaveLength(1);
      expect(columnRequests[0]._retry).toBe(true);
      expect(refreshRequests).toHaveLength(queued ? 0 : 1);
      if (!queued) expect(refreshRequests[0].timeout).toBe(0);
      expect(getIsRefreshing()).toBe(true);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(14_999);
      });
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
      expectUnchanged(action);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      // TanStack dispatches its state notification on the next timer tick.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(screen.getByRole("alert")).toHaveTextContent(action.fallback);
      expect(screen.getAllByRole("alert")).toHaveLength(1);
      expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
      expect(screen.getByRole("button", { name: /^Close / })).toBeEnabled();
      expect(
        screen.getByRole("button", { name: action.button, exact: true }),
      ).toBeEnabled();
      expect(columnRequests[0].signal.aborted).toBe(true);
      expectUnchanged(action);

      // A refresh completing late must not send the abandoned mutation, close
      // its dialog, discard its draft or publish any success side effects.
      await act(async () => {
        if (queued) {
          processQueue(null, "test-access");
          setIsRefreshing(false);
        } else {
          finishRefresh();
        }
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(columnRequests).toHaveLength(1);
      expectUnchanged(action);
      expect(screen.getByRole("alert")).toHaveTextContent(action.fallback);
      expect(getIsRefreshing()).toBe(false);

      allowSuccess = true;
      vi.useRealTimers();
      await userEvent
        .setup()
        .click(
          screen.getByRole("button", { name: action.button, exact: true }),
        );
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(columnRequests).toHaveLength(2);
      expect(columnRequests[1].signal).not.toBe(columnRequests[0].signal);
      expect(columnRequests[1].signal.aborted).toBe(false);
      expect(mocks.effect).toHaveBeenCalled();
    },
  );
});
