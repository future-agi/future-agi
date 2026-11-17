import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderMcp } from "../testing/renderMcp";
import SiemExportDrawer from "./SiemExportDrawer";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, ...api } };
});
const BASE = "/agentcc/siem/destinations/";
const row = {
  id: "hec-a",
  name: "Security collector",
  kind: "hec",
  url: "https://sink.example.test/services/collector/event",
  streams: ["audit_events"],
  enabled: false,
  has_secret: true,
  pending_count: 0,
  dead_letter_count: 0,
};
function routes(canManage = true) {
  api.get.mockImplementation(async (url) => ({
    data: {
      result:
        url === BASE
          ? {
              can_manage: canManage,
              export_available: true,
              destinations: [row],
            }
          : { deliveries: [] },
    },
  }));
}
beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
});

describe("HEC destination journey", () => {
  it("tests a masked token without creating a destination and surfaces rejection", async () => {
    routes();
    api.post.mockRejectedValue({
      response: { data: { message: "The collector rejected the HEC token." } },
    });
    renderMcp(<SiemExportDrawer open onClose={() => {}} />);
    fireEvent.mouseDown(await screen.findByRole("combobox", { name: "Kind" }));
    fireEvent.click(
      screen.getByRole("option", { name: "HTTP Event Collector (HEC)" }),
    );
    fireEvent.change(screen.getByLabelText("HEC URL"), {
      target: { value: row.url },
    });
    const credential = ["synthetic", "hec", "credential"].join("-");
    const field = screen.getByLabelText("HEC token");
    expect(field).toHaveAttribute("type", "password");
    fireEvent.change(field, { target: { value: credential } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(BASE + "test-connection/", {
        url: row.url,
        token: credential,
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "rejected the HEC token",
    );
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(credential)).not.toBeInTheDocument();
    api.post.mockResolvedValue({
      data: {
        result: {
          connected: true,
          message:
            "Connection successful. One synthetic event sent; nothing saved.",
        },
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "nothing saved",
    );
  });

  it("requires confirmation before editing, preserves the token when left blank", async () => {
    routes();
    api.patch.mockResolvedValue({ data: { result: row } });
    renderMcp(<SiemExportDrawer open onClose={() => {}} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit Security collector" }),
    );
    const editor = screen.getByRole("dialog", { name: "Edit destination" });
    fireEvent.change(within(editor).getByLabelText("Name"), {
      target: { value: "Renamed collector" },
    });
    fireEvent.click(
      within(editor).getByRole("button", { name: "Save changes" }),
    );
    expect(api.patch).not.toHaveBeenCalled();
    const confirm = screen.getByRole("dialog", {
      name: "Confirm destination changes",
    });
    fireEvent.click(
      within(confirm).getByRole("button", { name: "Save changes" }),
    );
    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith(BASE + row.id + "/", {
        name: "Renamed collector",
        url: row.url,
        streams: ["audit_events"],
      }),
    );
  });

  it("requires confirmation before deletion", async () => {
    routes();
    api.delete.mockResolvedValue({ data: { result: { deleted: true } } });
    renderMcp(<SiemExportDrawer open onClose={() => {}} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Delete Security collector" }),
    );
    expect(api.delete).not.toHaveBeenCalled();
    fireEvent.click(
      within(
        screen.getByRole("dialog", { name: "Delete destination" }),
      ).getByRole("button", { name: "Delete" }),
    );
    await waitFor(() =>
      expect(api.delete).toHaveBeenCalledWith(BASE + row.id + "/"),
    );
  });

  it("lets readers inspect destinations without any write controls", async () => {
    routes(false);
    renderMcp(<SiemExportDrawer open onClose={() => {}} />);
    expect(await screen.findByText("Security collector")).toBeInTheDocument();
    for (const name of [
      /Edit Security/,
      /Delete Security/,
      /Turn on/,
      /Add destination/,
      /Test connection/,
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
  });
});
