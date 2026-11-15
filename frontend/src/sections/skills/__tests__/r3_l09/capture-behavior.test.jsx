import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
      renderSaved={() => ({ notice: null, action: <button type="button">Set up application test</button> })} />);
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
    expect(screen.getByRole("button", { name: "Set up application test" })).toBeVisible();
  });
});

describe("captured test case copy (UX iteration 2)", () => {
  it("says saving runs nothing, names what each test checks and explains the snapshot timer", () => {
    const preview = { ...capture, expiresAt: new Date(Date.now() + 15 * 60_000).toISOString() };
    render(<CaseImportDialog {...props} preview={preview} onSave={vi.fn()} targetName="support-agent" applicationBehavior={{ skills }} />);
    expect(screen.getByText(/Step 2 of 2 · Saving doesn.t run anything\. Set the expected answer from your policy, not from the failed answer\./)).toBeVisible();
    expect(screen.queryByText(/replays this text only/)).not.toBeInTheDocument();
    // What each test checks, and what was left out, sit behind one disclosure.
    expect(screen.queryByText(/An answer check replays the selected text/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "What are these checks?" }));
    expect(screen.getByText(/An answer check replays the selected text; an application test checks the skills and business actions you specify below\./)).toBeVisible();
    expect(screen.getByText("Left out of this case: Hidden reasoning excluded")).toBeVisible();
    expect(screen.queryByText(/application verification/)).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Case name" })).toBeVisible();
    expect(screen.getByText("The trace snapshot expires in 15 min; after that, start again from the trace.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save test case" })).toHaveAccessibleDescription("Add a case name.");
  });
  it("does not promise an application test where the dialog has no application behavior", () => {
    render(<CaseImportDialog {...props} onSave={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "What are these checks?" }));
    expect(screen.getByText(/An answer check replays the selected text\./)).toBeVisible();
    expect(screen.queryByText(/application test checks/)).not.toBeInTheDocument();
  });
});

describe("captured tool results and footer (UX iteration 4)", () => {
  const structured = { ...capture, segments: [capture.segments[0], { id: "tool-1", kind: "tool_result", text: JSON.stringify({ "support-order": { age_days: 10, amount: 25, disputed: false } }) }, { id: "tool-2", kind: "tool_result", text: JSON.stringify({ status: "declined" }) }] };
  it("reads a structured tool result as named values, with the raw data one toggle away", () => {
    render(<CaseImportDialog {...props} preview={structured} onSave={vi.fn()} />);
    const tool = screen.getByRole("group", { name: "support-order" });
    expect(tool).toHaveTextContent("Age days: 10 · Amount: 25 · Disputed: false");
    // An unnamed structured result is numbered.
    expect(screen.getByRole("group", { name: "Tool result 2" })).toHaveTextContent("Status: declined");
    expect(screen.queryByRole("textbox", { name: "Redacted tool result 1" })).not.toBeInTheDocument();
    fireEvent.click(within(tool).getByRole("button", { name: "Show raw data" }));
    const raw = screen.getByRole("textbox", { name: "Redacted tool result 1" });
    expect(raw).toHaveValue(structured.segments[1].text);
    // Redacting the raw data updates the summary and clears both acknowledgments.
    review();
    acknowledge();
    fireEvent.change(raw, { target: { value: JSON.stringify({ "support-order": { age_days: 10 } }) } });
    expect(tool).toHaveTextContent("Age days: 10");
    expect(tool).not.toHaveTextContent("Amount");
    expect(screen.getByRole("checkbox", { name: /confirmed the expected behavior/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Share this redacted request/ })).not.toBeChecked();
  });
  it("keeps a plain-text tool result as an open, editable field", () => {
    render(<CaseImportDialog {...props} onSave={vi.fn()} />);
    expect(screen.getByRole("textbox", { name: "Redacted tool result 1" })).toHaveValue("Purchased fourteen days ago.");
    expect(screen.queryByRole("button", { name: "Show raw data" })).not.toBeInTheDocument();
  });
  it("says Cancel on the review step and offers Back only when there is a step to go back to", () => {
    const onClose = vi.fn();
    const onBack = vi.fn();
    const view = render(<CaseImportDialog {...props} onClose={onClose} onSave={vi.fn()} />);
    // The dialog keeps its corner close icon; the footer says Cancel, never Close.
    expect(screen.getAllByRole("button").filter((button) => button.textContent === "Close")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
    click("Cancel");
    expect(onClose).toHaveBeenCalledOnce();
    view.unmount();
    render(<CaseImportDialog {...props} onSave={vi.fn()} onBack={onBack} />);
    review();
    click("Back");
    expect(onBack).toHaveBeenCalledWith(expect.objectContaining({ snapshot: capture.captureId, title: "Refund failure", expectation: "receipt" }));
  });
});
