import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { enqueueSnackbar } from "notistack";
import AgentPicker from "../starts/AgentPicker";

vi.mock("notistack", async (actual) => ({
  ...(await actual()),
  enqueueSnackbar: vi.fn(),
}));

describe("AgentPicker outside the drawer", () => {
  it("only says connecting from here is UI only", () => {
    render(<AgentPicker agents={[]} value={null} onChange={vi.fn()} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Connect a new agent" }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "UI only: connecting a new agent from here isn't wired to the API yet.",
      { variant: "info" },
    );
  });
});
