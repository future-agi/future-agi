// @vitest-environment jsdom
/* eslint-disable react/prop-types */
// Dataset metadata and attaching datasets to agents are listed in the launch
// registry but a deployment does not offer them: the save route answers and
// stores nothing. Organize ▾ and ⌘K keep both entries, turned off with the
// shared line; no dialog opens and nothing is sent. Move to folder… is
// offered and works as before. The Metadata dialog promises no filtering or
// grouping.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { setCollabOfferForTest } from "src/sections/common/Collab/collabOffer";

const put = vi.fn();
const get = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: { put: (...a) => put(...a), get: (...a) => get(...a) },
  endpoints: {
    develop: { updateDataset: (id) => `/edit/${id}/` },
    agentPlayground: { listGraphs: "/graphs/" },
  },
}));
const notify = vi.fn(() => ({ undone: Promise.resolve(false) }));
vi.mock("src/sections/common/ux/toast", () => ({
  notify: (...a) => notify(...a),
  default: (...a) => notify(...a),
}));

const { default: useDatasetOrganize } = await import("../useDatasetOrganize");
const { default: ListSelectionBar } = await import("../../ListSelectionBar");
const { default: MetadataDialog } = await import("../MetadataDialog");
const { useCommandStore } = await import(
  "src/sections/common/CommandPalette/commandRegistry"
);

const GS = {
  id: "gs",
  name: "Golden",
  metadata: {},
  folder: "",
  agent_ids: [],
};

function Host({ selected, active = null, canEdit = true }) {
  const org = useDatasetOrganize({
    selected,
    active,
    canEdit,
    folders: [{ path: "support", count: 1, total: 1 }],
  });
  return (
    <>
      <ListSelectionBar
        count={selected.length}
        canCreate
        canDelete
        organize={org.menu}
        onClear={() => {}}
      />
      {org.dialogs}
    </>
  );
}
const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );
const commands = () =>
  useCommandStore.getState().sources["datasets.organize"] ?? [];
const command = (id) => commands().find((c) => c.id === id);
const openOrganize = () =>
  fireEvent.click(screen.getByRole("button", { name: /Organize/ }));

// Where a dialog still opens, a person fills it in and saves; each step is
// what the dialog asks for.
const saveMetadataIfShown = async () => {
  if (!screen.queryByRole("dialog")) return;
  fireEvent.change(screen.getByLabelText("Key 1"), {
    target: { value: "domain" },
  });
  fireEvent.change(screen.getByLabelText("Value 1"), {
    target: { value: "refunds" },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
  });
};
const saveAgentsIfShown = async () => {
  if (!screen.queryByRole("dialog")) return;
  fireEvent.click(await screen.findByRole("checkbox"));
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
  });
};

beforeEach(() => {
  setCollabOfferForTest({ all: false, madeReal: [] });
  put.mockReset();
  get.mockReset();
  notify.mockClear();
  put.mockResolvedValue({ data: { result: "ok" } });
  get.mockResolvedValue({
    data: {
      result: {
        graphs: [{ id: "g1", name: "Support Agent", active_version_number: 3 }],
        metadata: { total_pages: 1 },
      },
    },
  });
});
afterEach(() => {
  cleanup();
  setCollabOfferForTest(null);
});

describe("Organize ▾ where metadata and agents are not offered", () => {
  it("keeps Metadata… and Agents…, each disabled and described by the shared line", () => {
    wrap(<Host selected={[GS]} />);
    openOrganize();
    for (const label of ["Metadata…", "Agents…"]) {
      const item = screen.getByRole("menuitem", { name: label });
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(item).toHaveAccessibleDescription(NOT_IN_RELEASE);
      expect(within(item).getByText(NOT_IN_RELEASE)).toBeInTheDocument();
    }
    const folder = screen.getByRole("menuitem", { name: "Move to folder…" });
    expect(folder).not.toHaveAttribute("aria-disabled");
  });

  it("Metadata… opens no dialog and sends no request", async () => {
    wrap(<Host selected={[GS]} />);
    openOrganize();
    fireEvent.click(screen.getByRole("menuitem", { name: "Metadata…" }));
    await saveMetadataIfShown();
    expect(screen.queryByText("Metadata of Golden")).toBeNull();
    expect(put).not.toHaveBeenCalled();
  });

  it("Agents… opens no dialog and sends no request", async () => {
    wrap(<Host selected={[GS]} />);
    openOrganize();
    fireEvent.click(screen.getByRole("menuitem", { name: "Agents…" }));
    await saveAgentsIfShown();
    expect(screen.queryByText("Agents of Golden")).toBeNull();
    expect(put).not.toHaveBeenCalled();
  });

  it("Move to folder… still opens its dialog", () => {
    wrap(<Host selected={[GS]} />);
    openOrganize();
    fireEvent.click(screen.getByRole("menuitem", { name: "Move to folder…" }));
    expect(screen.getByRole("dialog").textContent).toMatch(/support/);
  });
});

describe("⌘K where metadata and agents are not offered", () => {
  it("keeps both commands, each carrying the shared line as its second line", () => {
    wrap(<Host selected={[]} active={GS} />);
    expect(commands().map((c) => c.label)).toEqual([
      "Set metadata…",
      "Move to folder…",
      "Attach to agents…",
    ]);
    expect(command("datasets.organize.metadata").hint).toBe(NOT_IN_RELEASE);
    expect(command("datasets.organize.agents").hint).toBe(NOT_IN_RELEASE);
    expect(command("datasets.organize.folder").hint).toBeUndefined();
  });

  it("running either says the shared line, opens nothing and sends nothing", async () => {
    wrap(<Host selected={[]} active={GS} />);
    // Both say the same line, so each run is counted: one notice per run.
    act(() => command("datasets.organize.metadata").run());
    await saveMetadataIfShown();
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenLastCalledWith(
      NOT_IN_RELEASE,
      expect.objectContaining({ variant: "info" }),
    );
    act(() => command("datasets.organize.agents").run());
    await saveAgentsIfShown();
    expect(notify).toHaveBeenCalledTimes(2);
    expect(notify).toHaveBeenLastCalledWith(
      NOT_IN_RELEASE,
      expect.objectContaining({ variant: "info" }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(put).not.toHaveBeenCalled();
  });
});

describe("where the registry makes metadata and agents real", () => {
  it("Metadata… is enabled, with no sentence, and its dialog saves", async () => {
    setCollabOfferForTest({
      all: false,
      madeReal: ["list.organize.metadata", "list.organize.agents"],
    });
    wrap(<Host selected={[GS]} />);
    openOrganize();
    const item = screen.getByRole("menuitem", { name: "Metadata…" });
    expect(item).not.toHaveAttribute("aria-disabled");
    expect(within(item).queryByText(NOT_IN_RELEASE)).toBeNull();
    fireEvent.click(item);
    expect(screen.getByText("Metadata of Golden")).toBeInTheDocument();
    await saveMetadataIfShown();
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  });
});

describe("the Metadata dialog", () => {
  it("one dataset: says its pairs are saved with the dataset, with no promise of filtering or grouping", () => {
    wrap(
      <MetadataDialog
        open
        datasets={[GS]}
        onClose={() => {}}
        onDone={() => {}}
      />,
    );
    const line = screen.getByText(/^Key = value pairs/);
    expect(line.textContent).toBe("Key = value pairs saved with the dataset.");
    expect(screen.queryByText(/filter/i)).toBeNull();
    expect(screen.queryByText(/group/i)).toBeNull();
  });
});
