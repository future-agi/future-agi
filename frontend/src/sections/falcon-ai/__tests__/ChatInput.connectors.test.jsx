import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import useFalconStore from "../store/useFalconStore";
import { updateConnector, fetchConnectors } from "../hooks/useFalconAPI";
import ChatInput from "../components/ChatInput";

const { notify, push, adminState } = vi.hoisted(() => ({
  notify: vi.fn(),
  push: vi.fn(),
  adminState: { canEdit: true },
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: notify,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
}));
vi.mock("../components/ContextSelector", () => ({ default: () => null }));
vi.mock("../components/SlashCommandPicker", () => ({ default: () => null }));
vi.mock("../components/AttachedFileChip", () => ({ default: () => null }));
vi.mock("../hooks/useFalconAPI", () => ({
  uploadFile: vi.fn(),
  updateConnector: vi.fn(),
  fetchConnectors: vi.fn(),
}));
vi.mock("src/sections/gateway/hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: adminState.canEdit }),
}));

const CONNECTORS = [
  { id: "c1", name: "Notion", is_active: true, is_verified: true },
  { id: "c3", name: "Jira", is_active: false, is_verified: true },
];

beforeEach(() => {
  adminState.canEdit = true;
  useFalconStore.getState().resetAll();
  useFalconStore.getState().setConnectors(CONNECTORS);
  notify.mockReset();
  push.mockReset();
  updateConnector.mockReset();
  fetchConnectors.mockReset();
});

async function openConnectors() {
  render(<ChatInput onSend={vi.fn()} />);
  fireEvent.click(screen.getByTitle("More"));
  fireEvent.mouseEnter(screen.getByText("Connectors"));
  await screen.findByText("Notion");
}

const switchFor = (name, state) =>
  screen.getByRole("checkbox", {
    name: `Turn ${name} ${state} for this workspace`,
  });

const storedActive = (id) =>
  useFalconStore.getState().connectors.find((c) => c.id === id).is_active;

describe("ChatInput connector switch (workspace-wide)", () => {
  it("is enabled for an owner or admin and says it applies to the workspace", async () => {
    await openConnectors();

    const sw = switchFor("Notion", "off");
    expect(sw).toBeEnabled();
    expect(sw).toHaveAccessibleDescription(
      "Applies to everyone in this workspace",
    );
  });

  it("turns a connector off with is_active false and shows it at once", async () => {
    // The save has not answered yet: the new state is already on screen.
    updateConnector.mockReturnValue(new Promise(() => {}));
    await openConnectors();

    fireEvent.click(switchFor("Notion", "off"));

    expect(updateConnector).toHaveBeenCalledTimes(1);
    expect(updateConnector).toHaveBeenCalledWith("c1", { is_active: false });
    expect(switchFor("Notion", "on")).not.toBeChecked();
    expect(storedActive("c1")).toBe(false);
    // Another save for the same connector waits for this one.
    expect(switchFor("Notion", "on")).toBeDisabled();
    expect(push).not.toHaveBeenCalled();
  });

  it("turns an off connector on with is_active true", async () => {
    updateConnector.mockResolvedValue({});
    fetchConnectors.mockResolvedValue({
      results: [CONNECTORS[0], { ...CONNECTORS[1], is_active: true }],
    });
    await openConnectors();

    fireEvent.click(switchFor("Jira", "on"));

    expect(updateConnector).toHaveBeenCalledWith("c3", { is_active: true });
    await waitFor(() => expect(switchFor("Jira", "off")).toBeChecked());
  });

  it("reads the connector list again after a save", async () => {
    updateConnector.mockResolvedValue({});
    fetchConnectors.mockResolvedValue({
      results: [
        { ...CONNECTORS[0], is_active: false },
        CONNECTORS[1],
        { id: "c9", name: "Slack", is_active: true, is_verified: true },
      ],
    });
    await openConnectors();

    fireEvent.click(switchFor("Notion", "off"));

    await waitFor(() => expect(fetchConnectors).toHaveBeenCalledTimes(1));
    const names = () => useFalconStore.getState().connectors.map((c) => c.name);
    await waitFor(() => expect(names()).toEqual(["Notion", "Jira", "Slack"]));
    expect(updateConnector.mock.invocationCallOrder[0]).toBeLessThan(
      fetchConnectors.mock.invocationCallOrder[0],
    );
  });

  it("puts the switch back and shows the server's message when the save is refused", async () => {
    const refusal =
      "Only an organization owner or admin can change a connector. Ask one of them to change it.";
    updateConnector.mockRejectedValue({
      response: { status: 403, data: { message: refusal } },
    });
    await openConnectors();

    fireEvent.click(switchFor("Notion", "off"));

    expect(await screen.findByRole("alert")).toHaveTextContent(refusal);
    expect(notify).toHaveBeenCalledWith(
      refusal,
      expect.objectContaining({ variant: "error" }),
    );
    expect(switchFor("Notion", "off")).toBeChecked();
    expect(switchFor("Notion", "off")).toBeEnabled();
    expect(storedActive("c1")).toBe(true);
    expect(fetchConnectors).not.toHaveBeenCalled();
  });

  it("shows a fixed sentence when the failed request has no message", async () => {
    updateConnector.mockRejectedValue(new Error("Network Error"));
    await openConnectors();

    fireEvent.click(switchFor("Jira", "on"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The connector could not be changed. Try again.",
    );
    expect(switchFor("Jira", "on")).not.toBeChecked();
    expect(storedActive("c3")).toBe(false);
  });

  it("does not open the connectors settings page when the switch is clicked", async () => {
    updateConnector.mockResolvedValue({});
    fetchConnectors.mockResolvedValue({ results: CONNECTORS });
    await openConnectors();

    fireEvent.click(switchFor("Notion", "off").closest(".MuiSwitch-root"));

    expect(push).not.toHaveBeenCalled();
  });

  it("shows the switch disabled to a member, with who can use it", async () => {
    adminState.canEdit = false;
    await openConnectors();

    const row = screen.getByText("Notion").closest("li");
    const sw = within(row).getByRole("checkbox", {
      name: "Turn Notion off for this workspace",
    });
    expect(sw).toBeDisabled();
    expect(sw).toHaveAccessibleDescription(
      "Only an owner or admin can turn a connector on or off",
    );
    fireEvent.click(sw.closest(".MuiSwitch-root"));
    expect(updateConnector).not.toHaveBeenCalled();
    expect(storedActive("c1")).toBe(true);
  });
});
