import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme, ThemeProvider } from "@mui/material/styles";

const fixtures = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  notify: vi.fn(),
  role: "Owner",
  workspace: "workspace-a",
}));
vi.mock("src/utils/axios", () => ({
  default: { get: fixtures.get, put: fixtures.put },
  endpoints: {
    develop: {
      updateDataset: (id) => `/model-hub/develops/${id}/edit_dataset_behavior/`,
    },
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: fixtures.workspace }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: fixtures.role }),
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: fixtures.notify }));
import DatasetSettingsDialog from "./DatasetSettingsDialog";
import { datasetCaps } from "../realBackendCaps";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

let client;
const details = {
  id: "outside-first-page",
  name: "Saved name",
  description: "Persisted description",
  tags: ["golden"],
};
function openDialog(theme = createTheme()) {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const close = vi.fn();
  const view = render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={client}>
        <DatasetSettingsDialog datasetId={details.id} onClose={close} />
      </QueryClientProvider>
    </ThemeProvider>,
  );
  return { close, ...view };
}
beforeEach(() => {
  vi.clearAllMocks();
  fixtures.role = "Owner";
  fixtures.workspace = "workspace-a";
  fixtures.get.mockResolvedValue({ data: { result: details } });
  fixtures.put.mockResolvedValue({ data: { status: true } });
});
afterEach(() => {
  cleanup();
  client?.clear();
  setLaunchOverrideForTest(null);
});

describe("dataset settings", () => {
  it.each([false, true])(
    "reserves theme spacing above the floating name label (launch=%s)",
    async (launch) => {
      setLaunchOverrideForTest({ launch, madeReal: ["dataset.settings"] });
      // A non-default scale catches hardcoded pixel padding as well as its absence.
      const theme = createTheme({ spacing: 10 });
      openDialog(theme);
      const name = await screen.findByRole("textbox", { name: /Dataset name/ });
      const content = name.closest(".MuiDialogContent-root");
      const stack = content.querySelector(":scope > .MuiStack-root");
      // jsdom pins the CSS contract; browser screenshots must prove no clipping.
      expect(stack).toHaveStyle({ paddingTop: theme.spacing(1) });
      expect(name).toHaveFocus();
      expect(
        screen.queryByRole("textbox", { name: "Description" }) !== null,
      ).toBe(!launch);
      expect(fixtures.put).not.toHaveBeenCalled();
    },
  );

  it("reads the scoped detail endpoint without list pagination or access calls", async () => {
    openDialog();
    await screen.findByDisplayValue(details.description);
    expect(fixtures.get).toHaveBeenCalledWith(
      `/model-hub/develops/${details.id}/edit_dataset_behavior/`,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(fixtures.get).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Access", { exact: true })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(fixtures.put).not.toHaveBeenCalled();
    expect(
      client.getQueryData([
        "datasets",
        "details",
        "org-a",
        "workspace-a",
        details.id,
      ]),
    ).toEqual(details);
  });
  it("saves name, description and a pending tag only on confirmation", async () => {
    const { close } = openDialog();
    await screen.findByDisplayValue(details.description);
    fireEvent.change(screen.getByRole("textbox", { name: /Dataset name/ }), {
      target: { value: "New name" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Description" }), {
      target: { value: "New description" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Tags" }), {
      target: { value: "support" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
    expect(fixtures.put).toHaveBeenCalledTimes(1);
    expect(fixtures.put).toHaveBeenCalledWith(
      `/model-hub/develops/${details.id}/edit_dataset_behavior/`,
      {
        dataset_name: "New name",
        description: "New description",
        tags: ["golden", "support"],
      },
    );
  });
  it("cancels without writing", async () => {
    const { close } = openDialog();
    await screen.findByDisplayValue(details.description);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(close).toHaveBeenCalledOnce();
    expect(fixtures.put).not.toHaveBeenCalled();
  });
  it("keeps edits visible after a server refusal", async () => {
    fixtures.put.mockRejectedValue({ result: "Dataset name already exists" });
    const { close } = openDialog();
    await screen.findByDisplayValue(details.description);
    fireEvent.change(screen.getByRole("textbox", { name: "Description" }), {
      target: { value: "Retain my edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Dataset name already exists");
    expect(screen.getByDisplayValue("Retain my edit")).toBeVisible();
    expect(close).not.toHaveBeenCalled();
  });
  it("never permits saving empty defaults when the detail read fails", async () => {
    fixtures.get.mockRejectedValue(new Error("Not found"));
    openDialog();
    await screen.findByText(/Couldn't load dataset details/);
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
    expect(fixtures.put).not.toHaveBeenCalled();
  });
  it("rejects whitespace names and invalid pending tags", async () => {
    openDialog();
    await screen.findByDisplayValue(details.description);
    fireEvent.change(screen.getByRole("textbox", { name: /Dataset name/ }), {
      target: { value: "  " },
    });
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: /Dataset name/ }), {
      target: { value: "Valid" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Tags" }), {
      target: { value: "Invalid tag" },
    });
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });
  it("has no viewer write control", async () => {
    fixtures.role = "Viewer";
    openDialog();
    await screen.findByDisplayValue(details.description);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(fixtures.put).not.toHaveBeenCalled();
  });
  it("holds settings even when its independent surface is claimed", () => {
    setLaunchOverrideForTest({ launch: true, madeReal: [] });
    expect(datasetCaps(true).settings).toBe(false);
    setLaunchOverrideForTest({ launch: true, madeReal: ["dataset.settings"] });
    expect(datasetCaps(true)).toMatchObject({
      settings: false,
      listFolders: false,
      listStars: false,
      listOrganize: false,
      listFilters: false,
    });
  });

  it("keeps legacy rename while hiding held description and tags", async () => {
    setLaunchOverrideForTest({ launch: true, madeReal: ["dataset.settings"] });
    const { close } = openDialog();
    await screen.findByDisplayValue(details.name);
    expect(screen.queryByRole("textbox", { name: "Description" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Tags" })).toBeNull();
    expect(fixtures.get).toHaveBeenCalledTimes(1);
    expect(fixtures.put).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox", { name: /Dataset name/ }), {
      target: { value: "Renamed legacy dataset" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
    expect(fixtures.put).toHaveBeenCalledTimes(1);
    expect(fixtures.put).toHaveBeenCalledWith(
      `/model-hub/develops/${details.id}/edit_dataset_behavior/`,
      { dataset_name: "Renamed legacy dataset" },
    );
  });
  it("keeps the held rename form read-only for viewers", async () => {
    setLaunchOverrideForTest({ launch: true, madeReal: ["dataset.settings"] });
    fixtures.role = "Viewer";
    openDialog();
    await screen.findByDisplayValue(details.name);
    expect(
      screen.getByRole("textbox", { name: /Dataset name/ }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.queryByRole("textbox", { name: "Description" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Tags" })).toBeNull();
    expect(fixtures.put).not.toHaveBeenCalled();
  });
  it("cancels legacy rename without writing held metadata", async () => {
    setLaunchOverrideForTest({ launch: true, madeReal: ["dataset.settings"] });
    const { close } = openDialog();
    await screen.findByDisplayValue(details.name);
    fireEvent.change(screen.getByRole("textbox", { name: /Dataset name/ }), {
      target: { value: "Not saved" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(close).toHaveBeenCalledOnce();
    expect(fixtures.put).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox", { name: "Description" })).toBeNull();
  });
});
