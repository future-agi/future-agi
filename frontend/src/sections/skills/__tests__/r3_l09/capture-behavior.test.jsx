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
  fireEvent.change(screen.getByRole("textbox", { name: "Case name" }), { target: { value: "Refund failure" } });
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
    click("Save test case");
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
    expect(screen.getByRole("button", { name: "Save test case" })).toBeDisabled();
    choose("Required business action", "Track the shipment");
    expect(screen.getByRole("checkbox", { name: /confirmed the expected behavior/ })).not.toBeChecked();
    acknowledge();
    expect(screen.getByRole("button", { name: "Save test case" })).toBeEnabled();
  });
  it("adds no behavior fixture and no picker when the context has no skills to require", async () => {
    const save = vi.fn().mockResolvedValue({ revision: 4 });
    render(<CaseImportDialog {...props} onSave={save} />);
    expect(screen.queryByLabelText("Required skill")).not.toBeInTheDocument();
    review();
    acknowledge();
    click("Save test case");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].case.fixtures.some((item) => item.name === "fi.application.expectations")).toBe(false);
  });
});
describe("captured example provenance and next step", () => {
  it("leads with readable provenance, keeps fingerprints in details and hands off to the reproduction after saving", async () => {
    const save = vi.fn().mockResolvedValue({ revision: 4 });
    render(<CaseImportDialog {...props} onSave={save} targetName="support-agent" describeSource={() => "From a recorded trace · support-agent v3"}
      renderSaved={() => ({ notice: null, action: <button type="button">Reproduce on the application</button> })} />);
    expect(screen.getByText("From a recorded trace · support-agent v3")).toBeVisible();
    expect(screen.queryByText("Source fingerprint")).not.toBeInTheDocument();
    expect(screen.queryByText(/activation score/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Source details" })).toHaveAttribute("aria-expanded", "false");
    review();
    acknowledge();
    click("Save test case");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Test case saved to support-agent: Refund failure. Nothing was run or published.")).toBeVisible();
    // The saved form no longer competes with the next step.
    expect(screen.queryByRole("button", { name: "Save test case" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reproduce on the application" })).toBeVisible();
  });
});

describe("captured test case copy (UX iteration 2)", () => {
  it("says saving runs nothing, names what each test checks and explains the snapshot timer", () => {
    const preview = { ...capture, expiresAt: new Date(Date.now() + 15 * 60_000).toISOString() };
    render(<CaseImportDialog {...props} preview={preview} onSave={vi.fn()} targetName="support-agent" applicationBehavior={{ skills }} />);
    expect(screen.getByText(/Saving this case doesn.t run a model or tools\. An answer check replays the selected text; application verification checks the skills and business actions you specify below\./)).toBeVisible();
    expect(screen.queryByText(/replays this text only/)).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Case name" })).toBeVisible();
    expect(screen.getByText("The trace snapshot expires in 15 min; after that, start again from the trace.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save test case" })).toHaveAccessibleDescription("Add a case name.");
  });
  it("does not promise application verification where the dialog has no application behavior", () => {
    render(<CaseImportDialog {...props} onSave={vi.fn()} />);
    expect(screen.getByText(/An answer check replays the selected text\./)).toBeVisible();
    expect(screen.queryByText(/application verification checks/)).not.toBeInTheDocument();
  });
});
