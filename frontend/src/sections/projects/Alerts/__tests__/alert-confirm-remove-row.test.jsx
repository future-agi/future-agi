import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";

import axios, { endpoints } from "src/utils/axios";
import ConfirmAlertsDialog from "../components/ConfirmAlertsDialog";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: vi.fn(),
}));

const ALERTS = [
  { id: "alert-a", name: "Alpha alert", isMute: false },
  { id: "alert-b", name: "Beta alert", isMute: false },
  { id: "alert-c", name: "Gamma alert", isMute: false },
];

const selectedIds = () =>
  useAlertStore.getState().selectedRows.map((row) => row.id);

const prepareDialog = (type, rows = ALERTS) => {
  useAlertStore.setState({ selectedRows: rows });
  useAlertStore.getState().handleOpenActionModal(type);
};

const openDialog = (type, rows = ALERTS) => {
  prepareDialog(type, rows);
  render(<ConfirmAlertsDialog />);
};

// The row's X has no accessible name of its own; it is the only button in
// the row that shows the alert's name.
const removeButtonFor = (name) =>
  screen.getByText(name).parentElement.querySelector("button");

// Closing the dialog clears its type on a 200 ms timer; let it land so it
// cannot rewrite the next test's dialog.
const waitForDialogClosed = () =>
  waitFor(() => {
    expect(useAlertStore.getState().actionModal).toMatchObject({
      state: false,
      type: null,
    });
  });

beforeEach(() => {
  resetAlertStoreState();
  vi.spyOn(axios, "delete").mockResolvedValue({
    data: { result: "Alerts deleted successfully" },
  });
  vi.spyOn(axios, "post").mockResolvedValue({
    data: { result: "Alerts muted successfully" },
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  resetAlertStoreState();
});

describe("removing an alert from the bulk action batch", () => {
  it("drops only that alert from the selection and keeps the dialog open", () => {
    prepareDialog("delete");

    act(() => useAlertStore.getState().handleRemoveRow("alert-b"));

    expect(selectedIds()).toEqual(["alert-a", "alert-c"]);
    expect(useAlertStore.getState().actionModal).toEqual({
      state: true,
      type: "delete",
      rows: [ALERTS[0], ALERTS[2]],
    });
  });

  it("leaves the batch alone for an id that is not in it", () => {
    prepareDialog("delete");

    act(() => useAlertStore.getState().handleRemoveRow("alert-z"));

    expect(selectedIds()).toEqual(["alert-a", "alert-b", "alert-c"]);
    expect(useAlertStore.getState().actionModal.state).toBe(true);
    expect(useAlertStore.getState().actionModal.rows).toEqual(ALERTS);
  });

  it("deletes only the alerts still listed after one is removed", async () => {
    openDialog("delete");
    expect(
      screen.getByText("Are you sure you want to delete the following Alerts?"),
    ).toBeInTheDocument();

    fireEvent.click(removeButtonFor("Beta alert"));

    expect(screen.queryByText("Beta alert")).not.toBeInTheDocument();
    expect(screen.getByText("Alpha alert")).toBeInTheDocument();
    expect(screen.getByText("Gamma alert")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete Alerts" }));

    expect(axios.delete).toHaveBeenCalledTimes(1);
    expect(axios.delete).toHaveBeenCalledWith(endpoints.project.createMonitor, {
      data: { ids: ["alert-a", "alert-c"] },
    });
    await waitForDialogClosed();
  });

  it("mutes only the alerts still listed after one is removed", async () => {
    openDialog("mute");

    fireEvent.click(removeButtonFor("Alpha alert"));

    expect(screen.queryByText("Alpha alert")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Mute Alerts" }));

    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledWith(endpoints.project.muteAlerts, {
      ids: ["alert-b", "alert-c"],
      is_mute: true,
    });
    await waitForDialogClosed();
  });

  it("edits the frozen batch without replacing it with a new live selection", async () => {
    openDialog("delete", [ALERTS[0], ALERTS[1]]);
    act(() => useAlertStore.setState({ selectedRows: [ALERTS[2]] }));

    fireEvent.click(removeButtonFor("Alpha alert"));

    expect(selectedIds()).toEqual(["alert-c"]);
    expect(useAlertStore.getState().actionModal.state).toBe(true);
    expect(useAlertStore.getState().actionModal.rows).toEqual([ALERTS[1]]);
    expect(screen.queryByText("Alpha alert")).not.toBeInTheDocument();
    expect(screen.getByText("Beta alert")).toBeInTheDocument();
    expect(screen.queryByText("Gamma alert")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete Alerts" }));

    expect(axios.delete).toHaveBeenCalledTimes(1);
    expect(axios.delete).toHaveBeenCalledWith(endpoints.project.createMonitor, {
      data: { ids: ["alert-b"] },
    });
    await waitForDialogClosed();
  });

  it("closes the dialog without acting when the last alert is removed", async () => {
    openDialog("delete", [ALERTS[0]]);

    fireEvent.click(removeButtonFor("Alpha alert"));

    expect(selectedIds()).toEqual([]);
    await waitForDialogClosed();
    expect(axios.delete).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });
});
