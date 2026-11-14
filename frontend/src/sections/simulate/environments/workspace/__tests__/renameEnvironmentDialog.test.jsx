import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const useRenameEnvironment = vi.fn();
vi.mock("src/api/simulate-environments/environments", () => ({
  useRenameEnvironment: (...args) => useRenameEnvironment(...args),
}));

const { default: RenameEnvironmentDialog } = await import("../RenameEnvironmentDialog");

const env = { id: "env-1", name: "Support" };

describe("RenameEnvironmentDialog", () => {
  beforeEach(() => useRenameEnvironment.mockReset());

  it("submits the rename on Enter", () => {
    const mutate = vi.fn();
    useRenameEnvironment.mockReturnValue({ mutate, isPending: false });
    render(<RenameEnvironmentDialog open env={env} onClose={vi.fn()} />);

    const field = screen.getByLabelText(/Environment name/);
    fireEvent.change(field, { target: { value: "Renamed" } });
    fireEvent.keyDown(field, { key: "Enter" });

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toMatchObject({ id: "env-1", name: "Renamed" });
  });

  it("does not double-submit on Enter while a rename is already in flight", () => {
    const mutate = vi.fn();
    useRenameEnvironment.mockReturnValue({ mutate, isPending: true });
    render(<RenameEnvironmentDialog open env={env} onClose={vi.fn()} />);

    const field = screen.getByLabelText(/Environment name/);
    fireEvent.keyDown(field, { key: "Enter" });
    fireEvent.keyDown(field, { key: "Enter" });

    expect(mutate).not.toHaveBeenCalled();
  });
});
