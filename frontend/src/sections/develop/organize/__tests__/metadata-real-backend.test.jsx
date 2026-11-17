// @vitest-environment jsdom
/* eslint-disable react/prop-types */
// Real-backend gates with a claimed registry: the API answer, not visibility,
// admits writes. Axios is a transport double; E2E owns actual persistence/scope.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CAPABILITIES_QUERY_KEY, useFeatureAllowed } from "src/hooks/useCapabilities";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { setCollabOfferForTest } from "src/sections/common/Collab/collabOffer";
import { setRealBackendForTests, datasetCaps, realListColumnIds } from "../../realBackendCaps";
import { useDatasetListQuery, realDatasetListResult } from "../../hooks/useDatasetListQuery";
import { useCommandStore } from "src/sections/common/CommandPalette/commandRegistry";
import useDatasetOrganize from "../useDatasetOrganize";
import MetadataDialog from "../MetadataDialog";

const { get, put, notify } = vi.hoisted(() => ({
  get: vi.fn(), put: vi.fn(), notify: vi.fn(() => ({ undone: Promise.resolve(false) })),
}));
vi.mock("src/utils/axios", () => ({
  default: { get, put },
  endpoints: {
    develop: { updateDataset: (id) => `/edit/${id}/`, getDatasets: () => "/datasets/" },
    agentPlayground: { listGraphs: "/graphs/" },
  },
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify, default: notify }));

const DS = { id: "d1", name: "Golden", metadata: { keep: "original", remove: "old" } };
const capability = (allowed) => ({ features: { dataset_metadata: { allowed } } });
let client;
const command = (kind) => useCommandStore.getState().sources["datasets.organize"]
  ?.find((item) => item.id === `datasets.organize.${kind}`);
function Host({ selected = [DS], live = false, canEdit = true }) {
  const metadataCapability = useFeatureAllowed("dataset_metadata");
  const list = useDatasetListQuery({}, { enabled: live });
  const organize = useDatasetOrganize({
    selected: live ? list.data?.items ?? [] : selected, canEdit, metadataCapability,
  });
  return <>{organize.menu}{organize.dialogs}</>;
}
const mount = (ui = <Host />) => render(
  <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
);
const setCapability = async (data) => {
  await act(async () => { client.setQueryData(CAPABILITIES_QUERY_KEY, { data }); });
};
const openMenu = () => fireEvent.click(screen.getByRole("button", { name: /Organize/ }));
const openMetadata = async () => {
  await waitFor(() => expect(command("metadata")).toBeDefined());
  act(() => command("metadata").run());
  return screen.findByRole("dialog");
};
const save = async () => {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save metadata" })); });
};
const change = (label, value) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  setRealBackendForTests(true);
  setLaunchOverrideForTest({ launch: true, showUnreleased: true, madeReal: ["list.organize.metadata", "list.organize.folders"] });
  setCollabOfferForTest({ all: false, madeReal: ["list.organize.metadata", "list.organize.folders"] });
  get.mockReset(); put.mockReset(); notify.mockClear();
  get.mockResolvedValue({ data: capability(true) });
  put.mockResolvedValue({ data: { result: "ok" } });
});
afterEach(() => {
  cleanup(); client.clear();
  setRealBackendForTests(null); setLaunchOverrideForTest(null); setCollabOfferForTest(null);
});

describe("claimed Metadata needs explicit real server support", () => {
  it.each([
    ["missing feature", { features: {} }],
    ["denied feature", capability(false)],
    ["nonboolean answer", capability("true")],
  ])("keeps menu and palette unavailable for %s", async (_name, data) => {
    await setCapability(data);
    mount(); openMenu();
    const item = screen.getByRole("menuitem", { name: "Metadata…" });
    expect(item).toHaveAttribute("aria-disabled", "true");
    expect(item).toHaveAccessibleDescription("Metadata is not available on this server.");
    expect(command("metadata").hint).toBe("Metadata is not available on this server.");
    act(() => command("metadata").run());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(screen.getByRole("menuitem", { name: "Agents…" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("menuitem", { name: "Move to folder…" })).not.toHaveAttribute("aria-disabled");
  });

  it("does not open or mutate while the capability request is loading", () => {
    get.mockImplementation(() => new Promise(() => {}));
    mount(); openMenu();
    const item = screen.getByRole("menuitem", { name: "Metadata…" });
    expect(item).toHaveAttribute("aria-disabled", "true");
    expect(item).toHaveAccessibleDescription("Checking metadata availability…");
    act(() => command("metadata").run());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(put).not.toHaveBeenCalled();
  });

  it("closes the save gate after a failed capability refresh even with cached true", async () => {
    await setCapability(capability(true));
    mount(); await openMetadata();
    change("Value 1", "unsaved");
    get.mockRejectedValue(new Error("capabilities offline"));
    await act(async () => { await client.refetchQueries({ queryKey: CAPABILITIES_QUERY_KEY }); });
    await waitFor(() => expect(screen.getByRole("button", { name: "Save metadata" })).toBeDisabled());
    expect(screen.getByText("Could not check metadata availability. Reload to try again.")).toBeInTheDocument();
    expect(screen.getByLabelText("Value 1")).toHaveValue("unsaved");
    await save();
    expect(put).not.toHaveBeenCalled();
  });

  it("follows unknown → false → true → false without enabling agents or query features", async () => {
    await setCapability({ features: {} });
    mount();
    act(() => command("metadata").run());
    expect(screen.queryByRole("dialog")).toBeNull();
    await setCapability(capability(false));
    act(() => command("metadata").run());
    expect(screen.queryByRole("dialog")).toBeNull();
    await setCapability(capability(true));
    await waitFor(() => expect(command("metadata").hint).toBeUndefined());
    await openMetadata();
    change("Value 1", "draft");
    await setCapability(capability(false));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save metadata" })).toBeDisabled());
    expect(screen.getByLabelText("Value 1")).toHaveValue("draft");
    await save();
    expect(put).not.toHaveBeenCalled();
    expect(command("agents").hint).toBeDefined();
    setLaunchOverrideForTest({ launch: true, showUnreleased: false, madeReal: ["list.organize.metadata"] });
    expect(datasetCaps().listFilters).toBe(false);
    expect(datasetCaps().listGroupBy).toBe(false);
    expect(realListColumnIds()).not.toContain("agents");
    expect(realListColumnIds()).not.toContain("metadata");
  });

  it("guards a directly mounted dialog on an unsupported real backend", async () => {
    await setCapability(capability(false));
    mount(<MetadataDialog open datasets={[DS]} onClose={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Save metadata" })).toBeDisabled();
    await save();
    expect(put).not.toHaveBeenCalled();
  });

  it("does not allow a viewer to open metadata even when supported", async () => {
    await setCapability(capability(true));
    mount(<Host canEdit={false} />);
    expect(screen.getByRole("button", { name: /Organize/ })).toBeDisabled();
    expect(command("metadata").when()).toBe(false);
    act(() => command("metadata").run());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(put).not.toHaveBeenCalled();
  });
});

describe("real metadata writes and refetched values", () => {
  it("sends typed values and null deletion, then reads the list and reopens saved values", async () => {
    let stored = { ...DS.metadata };
    get.mockImplementation(async (url) => url === "/datasets/"
      ? { data: { result: { datasets: [{ ...DS, metadata: { ...stored } }], total_count: 1, total_pages: 1 } } }
      : { data: capability(true) });
    put.mockImplementation(async (_url, { metadata }) => {
      for (const [key, value] of Object.entries(metadata)) {
        if (value === null) delete stored[key]; else stored[key] = value;
      }
      return { data: { result: "ok" } };
    });
    await setCapability(capability(true));
    mount(<Host live />);
    await waitFor(() => expect(command("metadata").when()).toBe(true));
    await openMetadata();
    fireEvent.click(screen.getByRole("button", { name: "Edit as YAML" }));
    change("Metadata as YAML", 'keep: original\nscore: 2.5\nready: true\ncode: "2"\n');
    await save();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(put).toHaveBeenCalledWith("/edit/d1/", { metadata: { score: 2.5, ready: true, code: "2", remove: null } });
    await waitFor(() => expect(client.getQueriesData({ queryKey: ["datasets", "list"] })[0][1].items[0].metadata)
      .toEqual({ keep: "original", score: 2.5, ready: true, code: "2" }));
    await openMetadata();
    expect(screen.getByLabelText("Value 1")).toHaveValue('"2"');
    expect(screen.getByLabelText("Value 3")).toHaveValue("true");
    expect(screen.getByLabelText("Value 4")).toHaveValue("2.5");
    expect(screen.queryByDisplayValue("remove")).toBeNull();
  });

  it("sends only edited rows when another editor changes or deletes unedited keys", async () => {
    let stored = { owner: "old", retired: "old", score: 1 };
    get.mockImplementation(async (url) => url === "/datasets/"
      ? { data: { result: { datasets: [{ ...DS, metadata: { ...stored } }], total_count: 1, total_pages: 1 } } }
      : { data: capability(true) });
    put.mockImplementation(async (_url, { metadata }) => {
      for (const [key, value] of Object.entries(metadata)) {
        if (value === null) delete stored[key]; else stored[key] = value;
      }
      return { data: { result: "ok" } };
    });
    await setCapability(capability(true));
    mount(<Host live />);
    await waitFor(() => expect(command("metadata").when()).toBe(true));
    await openMetadata();
    expect(screen.getByLabelText("Value 1")).toHaveValue("old");
    expect(screen.getByLabelText("Value 2")).toHaveValue("old");
    expect(screen.getByLabelText("Value 3")).toHaveValue("1");
    // A concurrent write is not in this editor's opening snapshot.
    stored = { owner: "new", score: 1, concurrent: true };
    change("Value 3", "2");
    await save();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(put.mock.calls).toEqual([["/edit/d1/", { metadata: { score: 2 } }]]);
    expect(stored).toEqual({ owner: "new", score: 2, concurrent: true });
    await waitFor(() => expect(client.getQueriesData({ queryKey: ["datasets", "list"] })[0][1].items[0].metadata)
      .toEqual({ owner: "new", score: 2, concurrent: true }));
    await openMetadata();
    expect(screen.getByLabelText("Value 2")).toHaveValue("new");
    expect(screen.getByLabelText("Value 3")).toHaveValue("2");
    expect(screen.queryByDisplayValue("retired")).toBeNull();
  });

  it.each([
    ["LF", "\n"], ["CR", "\r"], ["CRLF", "\r\n"],
  ])("preserves persisted %s text in the DOM through reopening and unchanged saves", async (_name, separator) => {
    const note = `first${separator}"quoted"\\path${separator}last`;
    const stored = { note };
    get.mockImplementation(async (url) => url === "/datasets/"
      ? { data: { result: { datasets: [{ ...DS, metadata: { ...stored } }], total_count: 1, total_pages: 1 } } }
      : { data: capability(true) });
    put.mockImplementation(async (_url, { metadata }) => {
      for (const [key, value] of Object.entries(metadata)) {
        if (value === null) delete stored[key]; else stored[key] = value;
      }
      return { data: { result: "ok" } };
    });
    await setCapability(capability(true));
    mount(<Host live />);
    await waitFor(() => expect(command("metadata").when()).toBe(true));
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      await openMetadata();
      const input = screen.getByLabelText("Value 1");
      expect(input.tagName).toBe("INPUT");
      expect(input).toHaveValue(JSON.stringify(note));
      expect(input.value).not.toMatch(/[\r\n]/);
      expect(JSON.parse(input.value)).toBe(note);
      // Exercise the DOM value through the row onChange path as well.
      fireEvent.change(input, { target: { value: JSON.stringify(`${note}!`) } });
      fireEvent.change(input, { target: { value: JSON.stringify(note) } });
      await save();
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(put).toHaveBeenCalledTimes(attempt);
      expect(put).toHaveBeenNthCalledWith(attempt, "/edit/d1/", { metadata: {} });
      expect(stored).toEqual({ note });
      expect(typeof stored.note).toBe("string");
      await waitFor(() => expect(client.getQueriesData({ queryKey: ["datasets", "list"] })[0][1].items[0].metadata)
        .toEqual({ note }));
    }
  });

  it("keeps the user's rows and dialog after a server save error", async () => {
    await setCapability(capability(true));
    put.mockRejectedValue({ response: { data: { result: "Metadata could not be saved." } } });
    mount(); await openMetadata();
    change("Value 1", "edited");
    await save();
    expect(await screen.findByRole("alert")).toHaveTextContent("Metadata could not be saved.");
    expect(screen.getByLabelText("Value 1")).toHaveValue("edited");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save metadata" })).not.toBeDisabled();
  });

  it("bulk saves in sequence with merge-only patches and retains a partial failure", async () => {
    await setCapability(capability(true));
    let resolveFirst;
    put.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockRejectedValueOnce({ result: "Second dataset refused the write." });
    mount(<Host selected={[DS, { id: "d2", name: "Second", metadata: { keep: "second", other: true, remove: "old" } }]} />);
    await openMetadata();
    change("Key 1", "score"); change("Value 1", "3");
    fireEvent.click(screen.getByText("remove · 2"));
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Save metadata" })).toBeDisabled();
    await act(async () => { resolveFirst({ data: { result: "ok" } }); });
    await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    expect(put.mock.calls).toEqual([
      ["/edit/d1/", { metadata: { score: 3, remove: null } }],
      ["/edit/d2/", { metadata: { score: 3, remove: null } }],
    ]);
    expect(await screen.findByRole("alert")).toHaveTextContent("Saved on 1 of 2 · Second: Second dataset refused the write.");
    expect(screen.getByLabelText("Value 1")).toHaveValue("3");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("keeps YAML validation and sends no nested or oversized value", async () => {
    await setCapability(capability(true)); mount(); await openMetadata();
    fireEvent.click(screen.getByRole("button", { name: "Edit as YAML" }));
    change("Metadata as YAML", "nested: [one, two]"); await save();
    expect(screen.getByRole("alert")).toHaveTextContent("not a list");
    change("Metadata as YAML", `long: ${"x".repeat(201)}`); await save();
    expect(screen.getByRole("alert")).toHaveTextContent("longer than 200");
    expect(put).not.toHaveBeenCalled();
  });

  it("preserves typed metadata through the actual list adapter without adding query promises", () => {
    const metadata = { count: 2, active: false, label: "2" };
    const result = realDatasetListResult({ datasets: [{ ...DS, metadata }] });
    expect(result.items[0].metadata).toEqual(metadata);
    expect(result.facets).toEqual({}); expect(result.groups).toBeNull();
  });
});
