import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import CaseImportDialog from "../../review/CaseImportDialog";
import { capture } from "./fixtures";

afterEach(cleanup);
const click = (name) => fireEvent.click(screen.getByRole("button", { name }));
const props = { open: true, onClose: vi.fn(), scopeKey: "scope", state: "ready", preview: capture, suiteRevision: 3, canSave: true };
const skills = [{ id: "skill-refund", label: "refund-policy" }, { id: "skill-ship", label: "shipping" }];
const choose = (label, option) => {
  fireEvent.mouseDown(screen.getByLabelText(label));
  fireEvent.click(screen.getByRole("option", { name: option }));
};
function review() {
  fireEvent.change(screen.getByRole("textbox", { name: "Example title" }), { target: { value: "Refund failure" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Expected answer must contain" }), { target: { value: "receipt" } });
}
function acknowledge() {
  fireEvent.click(screen.getByRole("checkbox", { name: /confirmed the expected behavior independently/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /Share this redacted request/ }));
}
describe("captured example with the required application behavior", () => {
  it("saves the skill and business action in the same reviewed case, with nothing typed", async () => {
    const save = vi.fn().mockResolvedValue({ revision: 4 });
    render(<CaseImportDialog {...props} onSave={save} applicationBehavior={{ skills }} renderSaved={({ caseId }) => <span>Next for {caseId.length}</span>} />);
    review();
    choose("Required skill", "refund-policy");
    choose("Required business action", "Issue a refund");
    acknowledge();
    click("Save reviewed example");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const fixture = save.mock.calls[0][0].case.fixtures.find((item) => item.name === "fi.application.expectations");
    expect(JSON.parse(fixture.text)).toEqual({ schema: 1, skill_id: "skill-refund", action: "issue_refund" });
    expect(await screen.findByText(/Next for/)).toBeVisible();
  });
  it("blocks a half-specified behavior and clears the acknowledgments when it changes", () => {
    render(<CaseImportDialog {...props} onSave={vi.fn()} applicationBehavior={{ skills }} />);
    review();
    choose("Required skill", "shipping");
    acknowledge();
    expect(screen.getByRole("button", { name: "Save reviewed example" })).toBeDisabled();
    choose("Required business action", "Track the shipment");
    expect(screen.getByRole("checkbox", { name: /confirmed the expected behavior/ })).not.toBeChecked();
    acknowledge();
    expect(screen.getByRole("button", { name: "Save reviewed example" })).toBeEnabled();
  });
  it("adds no behavior fixture and no picker when the context has no skills to require", async () => {
    const save = vi.fn().mockResolvedValue({ revision: 4 });
    render(<CaseImportDialog {...props} onSave={save} />);
    expect(screen.queryByLabelText("Required skill")).not.toBeInTheDocument();
    review();
    acknowledge();
    click("Save reviewed example");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].case.fixtures.some((item) => item.name === "fi.application.expectations")).toBe(false);
  });
});
