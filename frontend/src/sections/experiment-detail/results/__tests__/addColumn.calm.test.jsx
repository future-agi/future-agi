// "Add column…" in a results column's ⋮ menu, where this build has no route
// for custom columns: it stays in the menu on the Data tab and on the
// Compare pages, turned off with the shared line; a click opens no dialog;
// nothing asks for the columns or their values; and a save, from any door,
// is refused with the shared line before any request. Where the routes are
// offered, it works as before.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import {
  closeCustomColumnDoor,
  openCustomColumnDoor,
} from "src/sections/common/CustomColumns/customColumnDoor";
import CustomColumnHost from "src/sections/common/CustomColumns/CustomColumnHost";
import useCustomColumns, {
  fetchCustomColumnValues,
} from "src/sections/common/CustomColumns/useCustomColumns";
import ResultGroupHeader from "../headers/ResultGroupHeader";
import { buildGroupMenu } from "../groupMenu";

const COLUMNS = "/model-hub/datasets/{dataset_id}/result-columns/";
const VALUES = "/model-hub/datasets/{dataset_id}/result-columns/values/";

const h = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), absent: null }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: h.get, put: h.put },
  };
});
// A case names the routes this build lacks; otherwise the real list.
vi.mock("src/launch/absentPathGuard", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    absentTemplateOf: (url, ...rest) =>
      h.absent
        ? h.absent.includes(url)
          ? url
          : null
        : actual.absentTemplateOf(url, ...rest),
  };
});
vi.mock("src/api/develop/run-results", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunResults: () => ({ data: null }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const GROUP = { key: "output", kind: "output", name: "Output" };
const menuOf = (mode, openColumn) =>
  buildGroupMenu(GROUP, { mode, hide: vi.fn(), openColumn });
const addOf = (items) => items.find((i) => i.label === "Add column…");
const resultColumnsCalls = () =>
  [...h.get.mock.calls, ...h.put.mock.calls].filter(([url]) =>
    String(url).includes("/result-columns/"),
  );

let client;
const withClient = (ui) => (
  <QueryClientProvider client={client}>{ui}</QueryClientProvider>
);
const notOffered = () => {
  setLaunchOverrideForTest({ launch: true });
  h.absent = [COLUMNS, VALUES];
};
const offered = () => {
  setLaunchOverrideForTest({ launch: true });
  h.absent = [];
};

// The save every column dialog calls, whichever door opened it. The probe
// shows how it ended: "saved", or the message it was refused with.
const DEFS = [{ name: "reply_len", kind: "expression", expr: "len(output)" }];
function SaveProbe() {
  const { save } = useCustomColumns("d1");
  const [outcome, setOutcome] = React.useState("");
  return (
    <>
      <button
        type="button"
        onClick={() =>
          save(DEFS).then(
            () => setOutcome("saved"),
            (e) => setOutcome(e?.message ?? "failed"),
          )
        }
      >
        Save from the probe
      </button>
      {outcome ? <span data-testid="save-outcome">{outcome}</span> : null}
    </>
  );
}
const saveOutcome = async () => {
  render(withClient(<SaveProbe />));
  fireEvent.click(screen.getByRole("button", { name: "Save from the probe" }));
  return (await screen.findByTestId("save-outcome")).textContent;
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  h.get.mockReset().mockResolvedValue({ data: { result: { columns: [] } } });
  h.put.mockReset().mockResolvedValue({ data: { result: { columns: [] } } });
  h.absent = null;
});
afterEach(() => {
  act(() => closeCustomColumnDoor());
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("Add column… where custom columns have no route", () => {
  ["experiment", "compare"].forEach((mode) =>
    it(`stays in the ${mode === "experiment" ? "Data tab" : "Compare"} menu, turned off with the shared line`, () => {
      notOffered();
      const openColumn = vi.fn();
      const item = addOf(menuOf(mode, openColumn));
      expect(item).toMatchObject({ disabled: true, secondary: NOT_IN_RELEASE });
      item.onClick();
      expect(openColumn).not.toHaveBeenCalled();
    }),
  );

  it("shows in the header's ⋮ menu turned off, and a click does nothing", () => {
    notOffered();
    const openColumn = vi.fn();
    render(
      <ResultGroupHeader
        group={{ ...GROUP, menuItems: menuOf("experiment", openColumn) }}
      />,
    );
    fireEvent.click(screen.getByLabelText("Output menu"));
    const item = screen.getByRole("menuitem", { name: /Add column…/ });
    expect(item).toHaveAttribute("aria-disabled", "true");
    expect(item).toHaveTextContent(NOT_IN_RELEASE);
    fireEvent.click(item);
    expect(openColumn).not.toHaveBeenCalled();
  });

  it("opens no dialog", async () => {
    notOffered();
    render(
      withClient(<CustomColumnHost datasetId="d1" view={{ runIds: ["r1"] }} />),
    );
    act(() => openCustomColumnDoor());
    await act(async () => {});
    expect(screen.queryByTestId("custom-column-dialog")).toBeNull();
  });

  it("asks for no columns when the door is opened", async () => {
    notOffered();
    render(
      withClient(<CustomColumnHost datasetId="d1" view={{ runIds: ["r1"] }} />),
    );
    act(() => openCustomColumnDoor());
    await act(async () => {});
    expect(resultColumnsCalls()).toEqual([]);
  });

  it("asks for no column values", async () => {
    notOffered();
    await expect(
      fetchCustomColumnValues("d1", { runIds: ["r1"] }, { columnIds: ["c1"] }),
    ).resolves.toEqual({});
    expect(resultColumnsCalls()).toEqual([]);
  });

  it("refuses a save with the shared line", async () => {
    notOffered();
    expect(await saveOutcome()).toBe(NOT_IN_RELEASE);
  });

  it("sends no request for a save", async () => {
    notOffered();
    await saveOutcome();
    expect(resultColumnsCalls()).toEqual([]);
  });
});

describe("Add column… where custom columns are offered", () => {
  it("works as before in the menu", () => {
    // Green at the base.
    offered();
    const openColumn = vi.fn();
    const item = addOf(menuOf("compare", openColumn));
    expect(item.disabled).toBe(false);
    expect(item.secondary).toBeUndefined();
    item.onClick();
    expect(openColumn).toHaveBeenCalledWith(null);
  });

  it("opens the dialog and asks for the columns", async () => {
    // Green at the base.
    offered();
    render(
      withClient(<CustomColumnHost datasetId="d1" view={{ runIds: ["r1"] }} />),
    );
    act(() => openCustomColumnDoor());
    expect(await screen.findByTestId("custom-column-dialog")).toBeTruthy();
    await waitFor(() => expect(resultColumnsCalls().length).toBeGreaterThan(0));
  });

  it("saves through the columns route", async () => {
    offered();
    expect(await saveOutcome()).toBe("saved");
    expect(h.put).toHaveBeenCalledTimes(1);
    expect(String(h.put.mock.calls[0][0])).toContain("/result-columns/");
    expect(h.put.mock.calls[0][1]).toEqual({ columns: DEFS });
  });
});
