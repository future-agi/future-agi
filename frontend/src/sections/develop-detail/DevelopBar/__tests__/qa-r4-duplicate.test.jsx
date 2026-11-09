/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const mocks = vi.hoisted(() => ({
  clone: vi.fn(),
  notify: vi.fn(),
  invalidate: vi.fn(),
  commands: [],
  role: "owner",
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({}),
  useQueryClient: () => ({ invalidateQueries: mocks.invalidate }),
}));
vi.mock("react-router", () => ({
  useParams: () => ({ dataset: "source-id" }),
  useLocation: () => ({}),
  useNavigate: () => vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: mocks.role }),
}));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { CREATE: "create", UPDATE: "update", DELETE: "delete" },
  RolePermission: {
    DATASETS: {
      create: { owner: true, viewer: false },
      update: { owner: true },
      delete: { owner: true },
    },
  },
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [] }),
  getDatasetQueryOptions: () => ({}),
}));
vi.mock("src/sections/develop/hooks/useDatasetsList", () => ({
  useDatasetsList: () => ({
    data: {
      items: [{ id: "source-id", name: "Source", numberOfDatapoints: 1 }],
    },
  }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({}),
}));
vi.mock("src/api/develop/dataset-access", () => ({
  useRequestDatasetAccess: () => ({}),
}));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: (_scope, commands) => {
    mocks.commands = commands;
  },
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: mocks.notify }));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("../../states", () => ({
  useRunOptimizationStore: () => ({}),
  useDatasetOriginStore: () => ({}),
  useRunExperimentStore: {},
}));
vi.mock("../../DataTab/Versions/PendingChangesGate", () => ({
  usePendingChangesGate: () => vi.fn(),
}));
vi.mock("../../DataTab/Versions/classicWrites", async (importOriginal) => ({
  ...(await importOriginal()),
  cloneDatasetClassic: mocks.clone,
}));
vi.mock("src/sections/common/ux/oneview/MenuItems", () => ({
  ItemsMenu: ({ open, items }) =>
    open ? (
      <div role="menu">
        {items
          .filter((i) => i.id)
          .map((i) => (
            <button key={i.id} disabled={i.disabled} onClick={i.run}>
              {i.label}
            </button>
          ))}
      </div>
    ) : null,
}));
vi.mock("src/sections/develop/DuplicateDataset/DuplicateDataset", () => ({
  default: () => <div role="dialog">Prototype history and transfer</div>,
}));
vi.mock("../../DatasetSelectDropDown", () => ({ default: () => null }));
vi.mock("../../CompareDatasetDrawer", () => ({ default: () => null }));
vi.mock("../../BaseColumnsDrawer", () => ({ default: () => null }));
vi.mock("../../Common/ConfigureDatasetModal", () => ({ default: () => null }));
vi.mock("../../DataTab/Versions/DatasetVersionChip", () => ({
  default: () => null,
}));
vi.mock("../../Experiment/NewExperimentButton", () => ({
  default: () => null,
}));
vi.mock("../ShareDatasetDialog", () => ({ default: () => null }));
vi.mock("../ScenariosLine", () => ({ default: () => null }));
vi.mock("src/sections/common/AddData/SavedSourcesChip", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/AddData/KbChangesBanner", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Audit/AuditDrawer", () => ({
  default: () => null,
}));

import DatasetHeaderBar from "../DatasetHeaderBar";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.role = "owner";
  mocks.clone.mockResolvedValue({
    data: { result: { dataset_name: "Copy of Source" } },
  });
  setRealBackendForTests(true);
  setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
});
afterEach(() => {
  cleanup();
  setRealBackendForTests(null);
  setLaunchOverrideForTest(null);
});

const openMenu = () => {
  render(<DatasetHeaderBar currentTab="data" />);
  fireEvent.click(screen.getByRole("button", { name: "More dataset actions" }));
};

describe("LC-03 real header duplicates without prototype history or transfer", () => {
  // Without the workspace copy-and-move capability the real header copies at once.
  beforeEach(() =>
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: new Set(),
    }),
  );
  it("copies once through the classic endpoint and refreshes the list", async () => {
    openMenu();
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() =>
      expect(mocks.clone).toHaveBeenCalledExactlyOnceWith("source-id"),
    );
    expect(mocks.invalidate).toHaveBeenCalledWith({
      queryKey: ["datasets", "list"],
    });
    expect(mocks.notify).toHaveBeenCalledWith(
      expect.stringContaining("Copy of Source"),
      expect.objectContaining({ variant: "success" }),
    );
  });
  it("uses the same real action from the command palette", async () => {
    openMenu();
    await mocks.commands.find((c) => c.id === "dataset.duplicate").run();
    expect(mocks.clone).toHaveBeenCalledExactlyOnceWith("source-id");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("reports backend refusals without opening hidden surfaces", async () => {
    mocks.clone.mockRejectedValue({
      response: { data: { result: "Dataset limit reached" } },
    });
    openMenu();
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() =>
      expect(mocks.notify).toHaveBeenCalledWith(
        "Dataset limit reached",
        expect.objectContaining({ variant: "error" }),
      ),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("keeps viewers out of both duplicate entry points", () => {
    mocks.role = "viewer";
    openMenu();
    expect(screen.getByRole("button", { name: "Duplicate" })).toBeDisabled();
    expect(
      mocks.commands.find((c) => c.id === "dataset.duplicate").when(),
    ).toBe(false);
  });
  it("retains the rich dialog only for the prototype", () => {
    setRealBackendForTests(false);
    setLaunchOverrideForTest({ launch: false });
    openMenu();
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Prototype history and transfer",
    );
    expect(mocks.clone).not.toHaveBeenCalled();
  });
});
