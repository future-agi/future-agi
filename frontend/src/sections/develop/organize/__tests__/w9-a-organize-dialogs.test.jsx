// @vitest-environment jsdom
/* eslint-disable react/prop-types */
// W9 A1, A6, A7 (W9-SCOPE §5): the Metadata, Move to folder and Agents
// dialogs send one PUT per dataset with only their organisation key, show
// the server's sentence, and report a partial failure; Move carries an
// Undo that restores each dataset's own previous folder.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const put = vi.fn();
const get = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: { put: (...a) => put(...a), get: (...a) => get(...a) },
  endpoints: {
    develop: { updateDataset: (id) => `/edit/${id}/` },
    agentPlayground: { listGraphs: "/graphs/" },
  },
}));

const { default: MetadataDialog } = await import("../MetadataDialog");
const { default: MoveToFolderDialog } = await import("../MoveToFolderDialog");
const { default: AgentsDialog } = await import("../AgentsDialog");

const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );
const GS = {
  id: "gs",
  name: "Golden",
  metadata: { domain: "billing", old: 1 },
  folder: "",
  agent_ids: [],
};
const RP = {
  id: "rp",
  name: "Refund policy QA",
  metadata: { old: 2 },
  folder: "support",
  agent_ids: [],
};
const TS = {
  id: "ts",
  name: "Tasks",
  metadata: {},
  folder: "",
  agent_ids: ["g1"],
};

beforeEach(() => {
  put.mockReset();
  get.mockReset();
  put.mockResolvedValue({ data: { result: "ok" } });
});

describe("MetadataDialog", () => {
  it("one dataset: edits rows and sends the full map, removed keys as null", async () => {
    const onDone = vi.fn();
    wrap(
      <MetadataDialog
        open
        datasets={[GS]}
        onClose={() => {}}
        onDone={onDone}
      />,
    );
    expect(screen.getByLabelText("Key 1").value).toBe("domain");
    fireEvent.click(screen.getByLabelText("Remove old"));
    fireEvent.change(screen.getByLabelText("Key 2"), {
      target: { value: "team" },
    });
    fireEvent.change(screen.getByLabelText("Value 2"), {
      target: { value: "support" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(put).toHaveBeenCalledTimes(1);
    expect(put).toHaveBeenCalledWith("/edit/gs/", {
      metadata: { domain: "billing", team: "support", old: null },
    });
    expect(onDone).toHaveBeenCalledWith({
      variant: "success",
      message: "Saved metadata on 1 dataset",
    });
  });

  it("YAML round trip and the key sentence", async () => {
    wrap(<MetadataDialog open datasets={[GS]} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit as YAML" }));
    const area = screen.getByLabelText("Metadata as YAML");
    expect(area.value).toBe("domain: billing\nold: 1\n");
    fireEvent.change(area, {
      target: { value: "domain: billing\nteam: ops\n" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Edit as rows" }));
    expect(screen.getByLabelText("Value 2").value).toBe("ops");
    fireEvent.change(screen.getByLabelText("Key 1"), {
      target: { value: "Domain" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Metadata key “Domain” must start with a lowercase letter and use only lowercase letters, digits, “.”, “_” or “-” (40 characters at most).",
    );
    expect(put).not.toHaveBeenCalled();
  });

  it("several datasets: set keys and remove keys; a partial failure is named", async () => {
    put.mockImplementation(async (url) => {
      if (url === "/edit/rp/")
        throw {
          response: {
            data: { result: "A dataset can have up to 30 metadata keys." },
          },
        };
      return { data: {} };
    });
    const onDone = vi.fn();
    wrap(
      <MetadataDialog
        open
        datasets={[GS, RP, TS]}
        onClose={() => {}}
        onDone={onDone}
      />,
    );
    expect(
      screen.getByText(
        "Keys you set are added or replaced on each of the 3 datasets; other keys stay.",
      ),
    ).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Key 1"), {
      target: { value: "domain" },
    });
    fireEvent.change(screen.getByLabelText("Value 1"), {
      target: { value: "billing" },
    });
    fireEvent.click(screen.getByText("old · 2"));
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(put.mock.calls.map((c) => c[0])).toEqual([
      "/edit/gs/",
      "/edit/rp/",
      "/edit/ts/",
    ]);
    expect(put.mock.calls[0][1]).toEqual({
      metadata: { domain: "billing", old: null },
    });
    expect(onDone).toHaveBeenCalledWith({
      variant: "warning",
      message:
        "Saved on 2 of 3 · Refund policy QA: A dataset can have up to 30 metadata keys.",
    });
  });
});

describe("MoveToFolderDialog", () => {
  const folders = [{ path: "support", count: 1, total: 1 }];

  it("moves to a new folder with an Undo that restores each previous folder", async () => {
    const onDone = vi.fn();
    wrap(
      <MoveToFolderDialog
        open
        datasets={[GS, RP]}
        folders={folders}
        onClose={() => {}}
        onDone={onDone}
      />,
    );
    expect(
      screen.getByText("A folder exists while it holds at least one dataset."),
    ).toBeTruthy();
    fireEvent.change(screen.getByLabelText("New folder"), {
      target: { value: "support/billing" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Move" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(put.mock.calls).toEqual([
      ["/edit/gs/", { folder: "support/billing" }],
      ["/edit/rp/", { folder: "support/billing" }],
    ]);
    const { message, undo } = onDone.mock.calls[0][0];
    expect(message).toBe("Moved 2 datasets to support/billing");
    put.mockClear();
    await undo();
    expect(put.mock.calls).toEqual([
      ["/edit/gs/", { folder: "" }],
      ["/edit/rp/", { folder: "support" }],
    ]);
  });

  it("refuses a bad path and skips datasets already there", async () => {
    const onDone = vi.fn();
    wrap(
      <MoveToFolderDialog
        open
        datasets={[GS, RP]}
        folders={folders}
        onClose={() => {}}
        onDone={onDone}
      />,
    );
    fireEvent.change(screen.getByLabelText("New folder"), {
      target: { value: "a//b" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Move" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "A folder name can't be empty or contain “/”.",
    );
    fireEvent.change(screen.getByLabelText("New folder"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByText("support"));
    fireEvent.click(screen.getByRole("button", { name: "Move" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(put.mock.calls).toEqual([["/edit/gs/", { folder: "support" }]]);
    expect(onDone.mock.calls[0][0].message).toBe("Moved 1 dataset to support");
  });
});

describe("AgentsDialog", () => {
  beforeEach(() => {
    get.mockResolvedValue({
      data: {
        result: {
          graphs: [
            { id: "g1", name: "Support Agent", active_version_number: 3 },
            { id: "g2", name: "Billing Agent", active_version_number: 1 },
          ],
          metadata: { total_pages: 1 },
        },
      },
    });
  });

  it("one dataset: Attach from its runs, then Save sends the agent ids", async () => {
    const onDone = vi.fn();
    const gs = {
      ...GS,
      suggested_agents: [{ id: "g1", name: "Support Agent" }],
    };
    wrap(
      <AgentsDialog open datasets={[gs]} onClose={() => {}} onDone={onDone} />,
    );
    expect(await screen.findByText("Support Agent · v3")).toBeTruthy();
    expect(screen.getByTestId("agents-from-runs").textContent).toContain(
      "Support Agent",
    );
    fireEvent.click(screen.getByRole("button", { name: "Attach" }));
    expect(screen.queryByTestId("agents-from-runs")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(put.mock.calls).toEqual([["/edit/gs/", { agent_ids: ["g1"] }]]);
  });

  it("several datasets: Attach to all and Detach from all change only who differs", async () => {
    const onDone = vi.fn();
    wrap(
      <AgentsDialog
        open
        datasets={[GS, TS]}
        onClose={() => {}}
        onDone={onDone}
      />,
    );
    await screen.findByText(/Billing Agent/);
    const attach = screen.getAllByRole("button", { name: "Attach to all 2" });
    fireEvent.click(attach[0]); // Support Agent: TS has it, GS gets it
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(put.mock.calls).toEqual([["/edit/gs/", { agent_ids: ["g1"] }]]);
    expect(onDone.mock.calls[0][0].message).toBe("Updated agents on 1 dataset");
  });
});
