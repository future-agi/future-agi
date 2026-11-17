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
import {
  MutationCache,
  onlineManager,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { handleError } from "src/utils/queryErrorHandler";
import { useWorkspaceUnavailableRecovery } from "src/contexts/useWorkspaceUnavailableRecovery";
import { WORKSPACE_UNAVAILABLE_EVENT } from "src/contexts/workspaceUnavailableEvent";
import {
  WORKSPACE_RECOVERY_HANDLED,
  claimWorkspaceRefusal,
  isHeldRefusalToast,
} from "src/contexts/workspaceRefusalHold";
import {
  useAddColumnDrawerStore,
  useDeleteColumnStore,
  useDevelopFilterStore,
  useEditColumnNameStore,
} from "../../states";
import AddColumnDrawer from "../../AddColumn/AddColumnDrawer";
import EditColumnName from "../EditColumnName";
import DeleteColumn from "../DeleteColumn";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  get: vi.fn(),
  common: {},
  applyUserWorkspace: vi.fn(),
  refreshGrid: vi.fn(),
  snack: vi.fn(),
  notify: vi.fn(),
  trackEvent: vi.fn(),
  onSuccess: vi.fn(),
  add: vi.fn(),
  remove: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: {
    post: mocks.post,
    put: mocks.put,
    delete: mocks.delete,
    // Read by the workspace recovery hook (landing read, scope headers).
    get: mocks.get,
    defaults: { headers: { common: mocks.common } },
  },
  endpoints: {
    auth: { me: "/accounts/user-info/" },
    develop: {
      addColumn: (id) => `/datasets/${id}/add_column/`,
      updateColumnName: (id, col) => `/datasets/${id}/rename/${col}/`,
      deleteColumn: (id, col) => `/datasets/${id}/delete/${col}/`,
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.snack }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: mocks.snack }));
vi.mock("src/sections/common/ux/toast", () => ({ notify: mocks.notify }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: mocks.trackEvent,
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
  useDevelopDetailContext: () => ({ refreshGrid: mocks.refreshGrid }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds-1", name: "Dataset" }],
  }),
}));
vi.mock("../Versions/changeSetStore", () => ({
  useCreatedColumnsStore: {
    getState: () => ({ add: mocks.add, remove: mocks.remove }),
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
// Keep the real RHF controller and MUI field without unrelated filter controls.
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
    button: "Add Column",
    field: "Column name",
    fallback: "Couldn't save the column",
  },
  {
    name: "rename",
    method: "put",
    button: "Save",
    field: "Column Name",
    fallback: "Couldn't save the column",
  },
  {
    name: "delete",
    method: "delete",
    button: "Delete",
    fallback: "Couldn't delete the column",
  },
];
const failures = [
  ["offline", { message: "Network Error", transportCode: "ERR_NETWORK" }],
  ["timeout", { message: "timeout", transportCode: "ECONNABORTED" }],
  ["400", { statusCode: 400, result: "Choose another column name" }],
  [
    "403",
    {
      statusCode: 403,
      code: "permission_denied",
      result: "You cannot change this dataset",
    },
  ],
  [
    "500",
    { statusCode: 500, result: "Traceback: private implementation details" },
  ],
];
let client;

beforeEach(() => {
  onlineManager.setOnline(true);
  vi.clearAllMocks();
  mocks.post.mockReset();
  mocks.put.mockReset();
  mocks.delete.mockReset();
  mocks.get.mockReset();
  for (const header of Object.keys(mocks.common)) delete mocks.common[header];
  useAddColumnDrawerStore.setState({ openAddColumnDrawer: false });
  useEditColumnNameStore.setState({ editColumnName: null });
  useDeleteColumnStore.setState({ deleteColumn: null });
  useDevelopFilterStore.setState({ filters: FILTERS });
});
afterEach(() => {
  cleanup();
  client?.clear();
  vi.useRealTimers();
  onlineManager.setOnline(true);
});

// The app's workspace recovery (WorkspaceContext) with the real refusal hold.
const clearWorkspace = () => {
  delete mocks.common["X-Workspace-Id"];
};
function Recovery() {
  useWorkspaceUnavailableRecovery({
    authenticated: true,
    userId: "user-1",
    organizationId: "org",
    clearWorkspace,
    applyUserWorkspace: mocks.applyUserWorkspace,
  });
  return null;
}

// What src/utils/axios.js does with a workspace_unavailable response: it
// starts the recovery, then marks the error handled only if a hold claims it.
function interceptRefusal(refusal) {
  window.dispatchEvent(
    new CustomEvent(WORKSPACE_UNAVAILABLE_EVENT, {
      detail: { workspaceId: "stale", organizationId: "org" },
    }),
  );
  const data = {
    code: "workspace_unavailable",
    message: refusal,
    result: refusal,
  };
  const error = { ...data, statusCode: 403 };
  if (claimWorkspaceRefusal(data)) {
    error.handledBy = WORKSPACE_RECOVERY_HANDLED;
  }
  return error;
}

async function openAction(action, { recovery = false } = {}) {
  const user = userEvent.setup();
  client = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  vi.spyOn(client, "invalidateQueries");
  if (action.name === "add")
    useAddColumnDrawerStore.setState({ openAddColumnDrawer: true });
  if (action.name === "rename")
    useEditColumnNameStore.setState({ editColumnName: COLUMN });
  if (action.name === "delete")
    useDeleteColumnStore.setState({ deleteColumn: COLUMN });
  const component =
    action.name === "add" ? (
      <AddColumnDrawer />
    ) : action.name === "rename" ? (
      <EditColumnName onSuccess={mocks.onSuccess} />
    ) : (
      <DeleteColumn />
    );
  render(
    <QueryClientProvider client={client}>
      {recovery && <Recovery />}
      <MemoryRouter initialEntries={["/develop/ds-1"]}>
        <Routes>
          <Route path="/develop/:dataset" element={component} />
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
  return user;
}

function expectUnchanged(action) {
  expect(screen.getByRole("dialog")).toBeVisible();
  if (action.field)
    expect(screen.getByRole("textbox", { name: action.field })).toHaveValue(
      "Draft",
    );
  if (action.name === "add")
    expect(useAddColumnDrawerStore.getState().openAddColumnDrawer).toBe(true);
  if (action.name === "rename")
    expect(useEditColumnNameStore.getState().editColumnName).toEqual(COLUMN);
  if (action.name === "delete")
    expect(useDeleteColumnStore.getState().deleteColumn).toEqual(COLUMN);
  expect(useDevelopFilterStore.getState().filters).toEqual(FILTERS);
  expect(mocks.refreshGrid).not.toHaveBeenCalled();
  expect(client.invalidateQueries).not.toHaveBeenCalled();
  expect(mocks.onSuccess).not.toHaveBeenCalled();
  expect(mocks.add).not.toHaveBeenCalled();
  expect(mocks.remove).not.toHaveBeenCalled();
  expect(mocks.trackEvent).not.toHaveBeenCalled();
  expect(mocks.snack).not.toHaveBeenCalled();
  expect(mocks.notify).not.toHaveBeenCalled();
}

function expectRequest(action, times) {
  const request = mocks[action.method];
  expect(request).toHaveBeenCalledTimes(times);
  if (action.name === "add")
    expect(request).toHaveBeenLastCalledWith(
      "/datasets/ds-1/add_column/",
      {
        new_column_name: "Draft",
        column_type: "text",
        role: expect.any(String),
      },
      expect.objectContaining({ timeout: 15_000 }),
    );
  if (action.name === "rename")
    expect(request).toHaveBeenLastCalledWith(
      "/datasets/ds-1/rename/col-1/",
      { new_column_name: "Draft" },
      expect.objectContaining({ timeout: 15_000 }),
    );
  if (action.name === "delete")
    expect(request).toHaveBeenLastCalledWith(
      "/datasets/ds-1/delete/col-1/",
      expect.objectContaining({ timeout: 15_000 }),
    );
}

async function expectSuccess(action) {
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(mocks.refreshGrid).toHaveBeenCalledTimes(1);
  if (action.name === "add") {
    expect(mocks.add).toHaveBeenCalledWith("ds-1", {
      id: "new-1",
      name: "Draft",
      from: "add_column",
    });
    expect(mocks.snack).toHaveBeenCalledWith(expect.stringContaining("Draft"), {
      variant: "success",
    });
  } else if (action.name === "rename") {
    expect(mocks.onSuccess).toHaveBeenCalledWith(null, true);
    expect(mocks.snack).toHaveBeenCalledWith(
      "Column name updated successfully",
      { variant: "success" },
    );
    expect(client.invalidateQueries).toHaveBeenCalledTimes(3);
    expect(mocks.trackEvent).toHaveBeenCalledTimes(1);
  } else {
    expect(mocks.remove).toHaveBeenCalledWith("ds-1", "col-1");
    expect(mocks.notify).toHaveBeenCalledWith("Column “Original” deleted", {
      variant: "success",
      key: "column-deleted",
    });
    expect(client.invalidateQueries).toHaveBeenCalledTimes(1);
    expect(mocks.trackEvent).toHaveBeenCalledTimes(1);
  }
}

// RED contract: the baseline dialogs render no inline alert for these failures.
// Cloud execution is owed; do not treat this comment as a RED run receipt.
describe.each(actions)("$name column mutation", (action) => {
  it.each(failures)(
    "keeps state and shows one persistent error on %s, then retries",
    async (_kind, error) => {
      mocks[action.method]
        .mockRejectedValueOnce(error)
        .mockResolvedValueOnce({ data: { result: { column_id: "new-1" } } });
      const user = await openAction(action);
      await user.click(
        screen.getByRole("button", { name: action.button, exact: true }),
      );
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(
        error.statusCode === 400 || error.statusCode === 403
          ? error.result
          : action.fallback,
      );
      expect(screen.getAllByRole("alert")).toHaveLength(1);
      expectUnchanged(action);
      expectRequest(action, 1);
      const retry = screen.getByRole("button", {
        name: action.button,
        exact: true,
      });
      expect(retry).toBeEnabled();
      await user.click(retry);
      await expectSuccess(action);
      expectRequest(action, 2);
    },
  );

  it("shows an error and permits retry and Cancel while the browser is offline", async () => {
    mocks[action.method].mockRejectedValue(failures[0][1]);
    const user = await openAction(action);
    // Exercise TanStack's paused-mutation path, not just an Axios rejection.
    act(() => onlineManager.setOnline(false));
    await user.click(
      screen.getByRole("button", { name: action.button, exact: true }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(action.fallback);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expectUnchanged(action);
    expectRequest(action, 1);
    const retry = screen.getByRole("button", {
      name: action.button,
      exact: true,
    });
    expect(retry).toBeEnabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^Close / })).toBeEnabled();
    expect(onlineManager.isOnline()).toBe(false);
    await user.click(retry);
    await waitFor(() => expectRequest(action, 2));
    expect(await screen.findByRole("alert")).toHaveTextContent(action.fallback);
    expectUnchanged(action);
    expect(retry).toBeEnabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expectRequest(action, 2);
    expect(onlineManager.isOnline()).toBe(false);
  });

  it("preserves the existing successful path", async () => {
    mocks[action.method].mockResolvedValueOnce({
      data: { result: { column_id: "new-1" } },
    });
    const user = await openAction(action);
    await user.click(
      screen.getByRole("button", { name: action.button, exact: true }),
    );
    await expectSuccess(action);
    expectRequest(action, 1);
  });

  it("recovers from a stalled request after the 15-second deadline", async () => {
    await openAction(action);
    vi.useFakeTimers();
    // Model Axios's request timeout contract without sending any traffic.
    // With no configured timeout (the RED baseline), this never settles.
    mocks[action.method].mockImplementationOnce(
      (...args) =>
        new Promise((_resolve, reject) => {
          const config = args[action.method === "delete" ? 1 : 2];
          if (config?.timeout > 0)
            setTimeout(() => reject(failures[1][1]), config.timeout);
        }),
    );
    // user-event's RTL async wrapper waits on a zero-delay timer that Vitest
    // does not advance automatically. Submit synchronously, then flush at t=0.
    act(() => {
      fireEvent.click(
        screen.getByRole("button", { name: action.button, exact: true }),
      );
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocks[action.method]).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(14_999);
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expectUnchanged(action);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    // Flush TanStack's next notification tick after the deadline rejection.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(screen.getByRole("alert")).toHaveTextContent(action.fallback);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expectUnchanged(action);
    expectRequest(action, 1);
    expect(
      screen.getByRole("button", { name: action.button, exact: true }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^Close / })).toBeEnabled();
    vi.useRealTimers();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expectRequest(action, 1);
  });

  it("does not dismiss or claim success while the request is pending", async () => {
    let rejectRequest;
    mocks[action.method].mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectRequest = reject;
        }),
    );
    const user = await openAction(action);
    await user.click(
      screen.getByRole("button", { name: action.button, exact: true }),
    );
    await waitFor(() => expect(mocks[action.method]).toHaveBeenCalledTimes(1));
    expect(
      screen.getByRole("button", { name: action.button, exact: true }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^Close / })).toBeDisabled();
    await user.keyboard("{Escape}");
    expectUnchanged(action);
    await act(async () => rejectRequest(failures[0][1]));
    expect(await screen.findByRole("alert")).toHaveTextContent(action.fallback);
  });

  it("shows one calm line, not the refusal, for a refusal the workspace recovery explains, then retries", async () => {
    // The recovery says where the person landed; the dialog adds one calm
    // line instead of the server's refusal text.
    const refusal =
      "The selected workspace is unavailable. Select an accessible workspace and retry.";
    let rejectRequest;
    mocks[action.method]
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            rejectRequest = reject;
          }),
      )
      .mockResolvedValueOnce({ data: { result: { column_id: "new-1" } } });
    const user = await openAction(action);
    await user.click(
      screen.getByRole("button", { name: action.button, exact: true }),
    );
    await waitFor(() => expect(rejectRequest).toBeTypeOf("function"));
    await act(async () =>
      rejectRequest({
        statusCode: 403,
        code: "workspace_unavailable",
        message: refusal,
        result: refusal,
        handledBy: "workspace-recovery",
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled(),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your workspace changed. Try again.",
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByText(refusal)).not.toBeInTheDocument();
    expectUnchanged(action);
    const retry = screen.getByRole("button", {
      name: action.button,
      exact: true,
    });
    expect(retry).toBeEnabled();
    await user.click(retry);
    await expectSuccess(action);
    expectRequest(action, 2);
  });

  // The column mutations report errors only inline (no toast), so when the
  // recovery ends without a line of its own (a workspace is chosen while it
  // reads the landing) the dialog's line is all the person sees.
  it("shows a visible line when the real recovery hold ends unexplained, then retries", async () => {
    const refusal =
      "The selected workspace is unavailable. Select an accessible workspace and retry.";
    mocks.common["X-Organization-Id"] = "org";
    mocks.common["X-Workspace-Id"] = "stale";
    let finishRead;
    mocks.get.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishRead = () =>
            resolve({ data: { default_workspace_id: "landing" } });
        }),
    );
    let refused;
    mocks[action.method]
      .mockImplementationOnce(() => {
        refused = interceptRefusal(refusal);
        return Promise.reject(refused);
      })
      .mockResolvedValueOnce({ data: { result: { column_id: "new-1" } } });
    const user = await openAction(action, { recovery: true });
    await user.click(
      screen.getByRole("button", { name: action.button, exact: true }),
    );
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled(),
    );
    // The real hold claimed the refusal while the landing is read.
    expect(refused.handledBy).toBe(WORKSPACE_RECOVERY_HANDLED);
    expect(isHeldRefusalToast(refusal)).toBe(true);
    mocks.common["X-Workspace-Id"] = "chosen";
    await act(async () => finishRead());
    // Ended unexplained: no landing line, no grace hold.
    await waitFor(() => expect(isHeldRefusalToast(refusal)).toBe(false));
    expect(mocks.applyUserWorkspace).not.toHaveBeenCalled();
    expect(mocks.common["X-Workspace-Id"]).toBe("chosen");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your workspace changed. Try again.",
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByText(refusal)).not.toBeInTheDocument();
    expectUnchanged(action);
    const retry = screen.getByRole("button", {
      name: action.button,
      exact: true,
    });
    expect(retry).toBeEnabled();
    await user.click(retry);
    await expectSuccess(action);
    expectRequest(action, 2);
  });

  it("clears a dismissed error before opening another column", async () => {
    mocks[action.method].mockRejectedValueOnce(failures[0][1]);
    const user = await openAction(action);
    await user.click(
      screen.getByRole("button", { name: action.button, exact: true }),
    );
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    act(() => {
      if (action.name === "add")
        useAddColumnDrawerStore.setState({ openAddColumnDrawer: true });
      if (action.name === "rename")
        useEditColumnNameStore.setState({
          editColumnName: { id: "col-2", name: "Another" },
        });
      if (action.name === "delete")
        useDeleteColumnStore.setState({
          deleteColumn: { id: "col-2", name: "Another" },
        });
    });
    if (action.name === "add")
      await user.click(
        screen.getByRole("button", { name: "Text", exact: true }),
      );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    if (action.field)
      expect(screen.getByRole("textbox", { name: action.field })).toHaveValue(
        action.name === "add" ? "" : "Another",
      );
  });
});
