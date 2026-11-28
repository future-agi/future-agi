import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DraftConflictDialog from "./DraftConflictDialog";

const server = { files: [{ path: "SKILL.md", content: "Server edits" }], revision: 9, baseVersion: 2, note: "" };
afterEach(cleanup);

const setup = (overrides = {}) => {
  const props = { open: true, onClose: vi.fn(), refetch: vi.fn().mockResolvedValue({ data: server }), onReload: vi.fn(), onOverwrite: vi.fn().mockResolvedValue({ ...server, revision: 10 }), ...overrides };
  render(<DraftConflictDialog {...props} />);
  return props;
};

describe("draft conflict dialog", () => {
  it("reloads the fresh server snapshot, not the cached conflict revision", async () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: "Reload theirs" }));
    await waitFor(() => expect(props.onReload).toHaveBeenCalledWith(server));
    expect(props.refetch).toHaveBeenCalledOnce();
    expect(props.onOverwrite).not.toHaveBeenCalled();
    expect(props.onClose).toHaveBeenCalledOnce();
  });

  it("fetches the current revision before overwriting with local files", async () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: "Overwrite with mine" }));
    await waitFor(() => expect(props.onOverwrite).toHaveBeenCalledWith(9));
    expect(props.onReload).not.toHaveBeenCalled();
    expect(props.onClose).toHaveBeenCalledOnce();
  });

  it("does not close or overwrite if fetching the server draft fails", async () => {
    const props = setup({ refetch: vi.fn().mockResolvedValue({ isError: true, error: new Error("offline") }) });
    fireEvent.click(screen.getByRole("button", { name: "Overwrite with mine" }));
    await screen.findByText("Couldn't load the current draft. Your edits are still here. Try again.");
    expect(props.onOverwrite).not.toHaveBeenCalled();
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("keeps the conflict open if another writer wins the overwrite race", async () => {
    const props = setup({ onOverwrite: vi.fn().mockResolvedValue(null) });
    fireEvent.click(screen.getByRole("button", { name: "Overwrite with mine" }));
    await screen.findByText("Couldn't overwrite this draft. Reload the latest revision and try again.");
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("recreates a concurrently deleted draft with revision zero", async () => {
    const props = setup({ refetch: vi.fn().mockResolvedValue({ data: null }) });
    fireEvent.click(screen.getByRole("button", { name: "Overwrite with mine" }));
    await waitFor(() => expect(props.onOverwrite).toHaveBeenCalledWith(0));
  });
});
