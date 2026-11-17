import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import EditKeyDialog from "./EditKeyDialog";

const state = vi.hoisted(() => ({ mutate: vi.fn(), error: null }));
vi.mock("./hooks/useApiKeys", () => ({
  useUpdateApiKey: () => ({ mutate: state.mutate, error: state.error, isError: Boolean(state.error) }),
}));
vi.mock("./useKeyAccessOptions", () => ({ useKeyAccessOptions: () => ({ modelOptions: [] }) }));
vi.mock("./AllowedProvidersField", () => ({ default: () => null }));

const keyData = { id: "key-a", name: "CI key", expiresAt: "2099-06-01T10:20:30.000Z" };
const show = (key = keyData) => render(<EditKeyDialog open onClose={vi.fn()} keyData={key} gatewayId="default" />);
const save = () => fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

beforeEach(() => { state.mutate.mockReset(); state.error = null; });

describe("R3-KEYS-ONBOARDING-08 expiry editing", () => {
  it("hydrates an explicit UTC field but omits expiry from unrelated edits", () => {
    show();
    expect(screen.getByLabelText("Expiry (UTC)")).toHaveValue("2099-06-01T10:20:30");
    fireEvent.change(screen.getByRole("textbox", { name: /Name/ }), { target: { value: "Renamed key" } });
    save();
    expect(state.mutate.mock.calls[0][0]).not.toHaveProperty("expires_at");
  });

  it("submits the previewed instant, with no browser timezone conversion", () => {
    show();
    fireEvent.change(screen.getByLabelText("Expiry (UTC)"), { target: { value: "2099-07-02T11:22:33" } });
    expect(screen.getByText(/2099-07-02T11:22:33.000Z/)).toBeInTheDocument();
    save();
    expect(state.mutate.mock.calls[0][0].expires_at).toBe("2099-07-02T11:22:33.000Z");
  });

  it("clears expiry only after an intentional clear action", () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Clear expiry" }));
    expect(screen.getByLabelText("Expiry (UTC)")).toHaveValue("");
    save();
    expect(state.mutate.mock.calls[0][0]).toHaveProperty("expires_at", null);
  });

  it("blocks a changed expiry in the past but allows an untouched stored past expiry", () => {
    const { unmount } = show({ ...keyData, expiresAt: "2000-01-01T00:00:00Z" });
    save();
    expect(state.mutate.mock.calls[0][0]).not.toHaveProperty("expires_at");
    unmount();
    state.mutate.mockReset();
    show();
    fireEvent.change(screen.getByLabelText("Expiry (UTC)"), { target: { value: "2000-01-01T00:00:00" } });
    expect(screen.getByText("Choose a future expiry in UTC.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Changes" })).toBeDisabled();
    save();
    expect(state.mutate).not.toHaveBeenCalled();
  });

  it("does not treat an incomplete datetime input as an instruction to remove expiry", () => {
    show();
    fireEvent.change(screen.getByLabelText("Expiry (UTC)"), { target: { value: "" } });
    expect(screen.getByRole("button", { name: "Save Changes" })).toBeDisabled();
    expect(screen.getByText("Enter a valid expiry or choose Clear expiry.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear expiry" }));
    save();
    expect(state.mutate.mock.calls[0][0]).toHaveProperty("expires_at", null);
  });

  it("shows a server expiry policy refusal and keeps the editor open", () => {
    state.error = { response: { status: 400, data: { result: "This key has a fixed expiry." } } };
    show();
    expect(screen.getByRole("alert")).toHaveTextContent("This key has a fixed expiry.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("resets an unsaved expiry change when another key opens", () => {
    const view = show();
    fireEvent.click(screen.getByRole("button", { name: "Clear expiry" }));
    view.rerender(<EditKeyDialog open onClose={vi.fn()} keyData={{ id: "key-b", name: "Other key" }} gatewayId="default" />);
    save();
    expect(state.mutate.mock.calls[0][0]).toMatchObject({ keyId: "key-b" });
    expect(state.mutate.mock.calls[0][0]).not.toHaveProperty("expires_at");
  });
});
