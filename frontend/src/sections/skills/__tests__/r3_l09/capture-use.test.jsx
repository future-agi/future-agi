import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import CaseImportDialog from "../../review/CaseImportDialog";
import ContextCaptureDialog from "../../contexts/ContextCaptureDialog";
import ContextUsePanel, {
  buildContextPlan,
} from "../../contexts/ContextUsePanel";
import ContextConsumersView from "../../contexts/ContextConsumersView";
import {
  capture,
  DIGEST,
  ID,
  qualification,
  snapshot,
  VERSION_ID,
} from "./fixtures";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const click = (name) => fireEvent.click(screen.getByRole("button", { name }));
const source = {
  traceRef: {
    projectId: ID,
    traceId: "trace-a",
    spanId: "span-a",
    startTime: "2026-11-08T00:00:00Z",
  },
  authorized: true,
  readOnly: false,
  segments: [
    ...capture.segments,
    { id: "hidden", kind: "reasoning", text: "hidden-chain" },
    { id: "answer", kind: "answer", text: "incorrect answer" },
  ],
};
const useProps = {
  scopeKey: "scope",
  state: "ready",
  snapshot,
  origin: "https://example.invalid",
  organizationId: ID,
  workspaceId: ID,
  qualification,
};
const capturedProps = {
  open: true,
  onClose: vi.fn(),
  scopeKey: "scope",
  state: "ready",
  preview: capture,
  suiteRevision: 3,
  canSave: true,
};
function fillExample() {
  fireEvent.change(screen.getByRole("textbox", { name: "Case name" }), {
    target: { value: "Refund boundary" },
  });
  fireEvent.change(
    screen.getByRole("textbox", { name: "Expected answer must contain" }),
    { target: { value: "Eligible" } },
  );
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: /confirmed the expected behavior independently/,
    }),
  );
  fireEvent.click(
    screen.getByRole("checkbox", { name: /Share this redacted request/ }),
  );
}

describe("reviewed source capture", () => {
  it("previews only selected supported segments with the full physical identity", async () => {
    const preview = vi.fn().mockResolvedValue(capture);
    render(
      <ContextCaptureDialog
        open
        onClose={() => {}}
        scopeKey="scope"
        state="ready"
        source={source}
        canCapture
        onPreview={preview}
      />,
    );
    expect(screen.queryByText("hidden-chain")).not.toBeInTheDocument();
    expect(screen.queryByText("incorrect answer")).not.toBeInTheDocument();
    // Every supported segment starts selected; the reviewer deselects exceptions.
    expect(
      screen.getByRole("checkbox", { name: "Customer request" }),
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Tool result" }),
    ).toBeChecked();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Tool result" }),
    );
    click("Preview selected text");
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1));
    expect(preview.mock.calls[0][0]).toMatchObject({
      traceRef: source.traceRef,
      selectedSegmentIds: ["request-1"],
    });
    expect(
      await screen.findByRole("textbox", {
        name: "Expected answer must contain",
      }),
    ).toHaveValue("");
  });
  it("offers leaving the delivered context for another one without losing the span", () => {
    const change = vi.fn();
    render(
      <ContextCaptureDialog
        open
        onClose={() => {}}
        scopeKey="scope"
        state="ready"
        source={source}
        canCapture
        onPreview={vi.fn()}
        onChangeContext={change}
      />,
    );
    click("Change context");
    expect(change).toHaveBeenCalledOnce();
  });
  it("does not offer capture for a shared read-only trace", () => {
    render(
      <ContextCaptureDialog
        open
        onClose={() => {}}
        scopeKey="scope"
        state="ready"
        source={{ ...source, readOnly: true }}
        canCapture
        onPreview={vi.fn()}
      />,
    );
    expect(
      screen.queryByText(capture.segments[0].text),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Preview selected text" }),
    ).toBeDisabled();
  });
  it("redacts before workspace disclosure, keeps expectations out of the task, and preserves lineage", async () => {
    const save = vi.fn().mockResolvedValue({ revision: 4 });
    render(<CaseImportDialog {...capturedProps} onSave={save} />);
    fireEvent.change(
      screen.getByRole("textbox", { name: "Redacted request" }),
      { target: { value: "Customer asks for refund." } },
    );
    fillExample();
    click("Save test case");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const sent = save.mock.calls[0][0];
    expect(sent).toMatchObject({
      captureId: ID,
      sourceFingerprint: DIGEST,
      suiteRevision: 3,
      acknowledgeWorkspaceSharing: true,
      case: {
        prompt: "Customer asks for refund.",
        expectedTrigger: null,
        reviewed: true,
      },
    });
    expect(sent.case.prompt).not.toContain("Eligible");
    expect(sent.case.checks[0]).toMatchObject({
      kind: "contains",
      config: { text: "Eligible", caseSensitive: true },
    });
    expect(JSON.stringify(sent)).not.toContain("private-address");
  });
  it("clears review and disclosure acknowledgments after another edit", () => {
    render(<CaseImportDialog {...capturedProps} onSave={vi.fn()} />);
    fillExample();
    expect(
      screen.getByRole("button", { name: "Save test case" }),
    ).toBeEnabled();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Redacted tool result 1" }),
      { target: { value: "Changed fixture" } },
    );
    expect(
      screen.getByRole("button", { name: "Save test case" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("checkbox", { name: /confirmed the expected behavior/ }),
    ).not.toBeChecked();
  });
  it("expires private preview while preserving unsent edits", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-08T00:00:00Z"));
    render(
      <CaseImportDialog
        {...capturedProps}
        preview={{ ...capture, expiresAt: "2026-11-08T00:00:02Z" }}
        onSave={vi.fn()}
      />,
    );
    fillExample();
    act(() => vi.advanceTimersByTime(3000));
    expect(
      screen.getByRole("button", { name: "Save test case" }),
    ).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Case name" })).toHaveValue(
      "Refund boundary",
    );
  });
  it("does not silently refresh a saved-suite precondition while preserving edits", () => {
    const { rerender } = render(
      <CaseImportDialog {...capturedProps} onSave={vi.fn()} />,
    );
    fillExample();
    rerender(
      <CaseImportDialog
        {...capturedProps}
        suiteRevision={4}
        onSave={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Save test case" }),
    ).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Case name" })).toHaveValue(
      "Refund boundary",
    );
  });
  it("uses UTF-8 byte limits, not string length", () => {
    render(<CaseImportDialog {...capturedProps} onSave={vi.fn()} />);
    fillExample();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Redacted request" }),
      { target: { value: "é".repeat(9000) } },
    );
    expect(
      screen.getByRole("textbox", { name: "Redacted request" }),
    ).toHaveAttribute("aria-invalid", "true");
    expect(
      screen.getByRole("button", { name: "Save test case" }),
    ).toBeDisabled();
  });
  it("removes captured text immediately after access loss", () => {
    const { rerender } = render(<CaseImportDialog {...capturedProps} />);
    rerender(<CaseImportDialog {...capturedProps} state="forbidden" />);
    expect(
      screen.queryByRole("textbox", { name: "Redacted request" }),
    ).not.toBeInTheDocument();
  });
});

describe("qualified exact install handoff", () => {
  it("pins the first unpromoted version and explicit workspace without broad keys or overlay installation", () => {
    const command = buildContextPlan({
      ...useProps,
      profile: qualification.profiles[0],
      packageVersion: qualification.packageVersion,
      project: "./customer's repo",
    });
    expect(command).toContain("--version 7");
    expect(command).not.toContain("--label");
    expect(command).toContain("FI_CONTEXT_CREDENTIAL");
    expect(command).toContain("FI_CONTEXT_WORKSPACE_ID");
    expect(command).toContain("--out 'context-plan.json'");
    expect(command).toContain("./customer'\"'\"'s repo");
    expect(command).not.toMatch(
      /FI_API_KEY|FI_SECRET_KEY|unzip|--allow-preview|--adopt-local/,
    );
  });
  it.each([
    "http://example.invalid",
    "https://name:secret@example.invalid",
    "https://example.invalid?token=secret",
  ])("rejects an unsafe origin %s", (origin) => {
    expect(() =>
      buildContextPlan({
        ...useProps,
        origin,
        profile: qualification.profiles[0],
        packageVersion: "0.0.1",
        project: ".",
      }),
    ).toThrow();
  });
  it('offers an explicit preview SDK path without inventing package or host qualification', () => {
    const { rerender } = render(<ContextUsePanel {...useProps} qualification={undefined} />);
    expect(screen.getByText(/Preview installer — writes AGENTS.md/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Copy plan command' })).toBeEnabled();
    expect(screen.getByLabelText('Context install plan command')).toHaveTextContent('--allow-preview');
    expect(screen.getByLabelText('Context install plan command')).not.toHaveTextContent('pip install');
    rerender(<ContextUsePanel {...useProps} qualification={{ ...qualification, profiles: [{ ...qualification.profiles[0], state: 'preview' }] }} />);
    expect(screen.getByText(/Preview installer — writes AGENTS.md/)).toBeVisible();
    expect(screen.getByLabelText('Context install plan command')).toHaveTextContent('--allow-preview');
  });
  it('keeps the reviewed apply step visible and copies its command separately', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: write } });
    render(<ContextUsePanel {...useProps} />);
    expect(screen.getByRole('heading', { name: '4. Apply after you review the plan' })).toBeVisible();
    click('Copy apply command');
    await screen.findByText('Copied the apply command.');
    expect(write).toHaveBeenCalledWith("python -m fi.context apply --plan 'context-plan.json' --quiescent");
    expect(screen.queryByText('Copied the complete plan command.')).not.toBeInTheDocument();
  });
  it('fails closed when the public API origin is not configured', () => {
    render(<ContextUsePanel {...useProps} origin={null} originError="not_https" />);
    expect(screen.getByText(/no public HTTPS API address configured/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Copy plan command' })).not.toBeInTheDocument();
  });
  it("reports clipboard failure honestly and does not show copied until fulfillment", async () => {
    const write = vi
      .fn()
      .mockRejectedValueOnce(new Error("denied"))
      .mockResolvedValueOnce(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: write },
    });
    render(<ContextUsePanel {...useProps} />);
    click("Copy plan command");
    expect(
      screen.queryByText("Copied the complete plan command."),
    ).not.toBeInTheDocument();
    await screen.findByText(/Could not copy/);
    click("Copy plan command");
    await screen.findByText("Copied the complete plan command.");
    expect(write.mock.calls[1][0]).toContain("--version 7");
    fireEvent.change(
      screen.getByRole("textbox", { name: "Project directory" }),
      { target: { value: "./new-project" } },
    );
    expect(
      screen.queryByText("Copied the complete plan command."),
    ).not.toBeInTheDocument();
  });
  it("fences copy success from the previous command when the destination changes in flight", async () => {
    let finish;
    const write = vi.fn(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: write },
    });
    render(<ContextUsePanel {...useProps} />);
    click("Copy plan command");
    fireEvent.change(
      screen.getByRole("textbox", { name: "Project directory" }),
      { target: { value: "./changed" } },
    );
    await act(async () => {
      finish();
    });
    expect(
      screen.queryByText("Copied the complete plan command."),
    ).not.toBeInTheDocument();
  });
});

describe("independent consumer observations", () => {
  const data = {
    results: [
      {
        binding: {
          id: ID,
          name: "Pilot application",
          selector: { version: 4 },
        },
        desired: { versionId: VERSION_ID, digest: DIGEST },
        resolved: {
          versionId: VERSION_ID,
          digest: DIGEST,
          observedAt: "2026-11-08T00:00:00Z",
          origin: "supported_adapter",
        },
        installed: null,
        discovered: null,
        loaded: null,
        states: ["pinned_as_intended", "unknown"],
        observationAgeSeconds: null,
        nextActionCode: "inspect_runtime",
      },
    ],
    asOf: "2026-11-08T00:00:00Z",
    count: 1,
    page: 1,
    pageSize: 25,
  };
  it("does not infer delivery from resolution and preserves deliberate pins", () => {
    render(
      <ContextConsumersView
        scopeKey="scope"
        state="ready"
        data={data}
        onPageChange={() => {}}
        onWindowChange={() => {}}
      />,
    );
    expect(screen.getByText("Pinned as intended")).toBeVisible();
    expect(screen.getByText("No report from this app yet")).toBeVisible();
    // A calm summary, not a warning: connected consumers that have not reported delivery are named and counted.
    expect(screen.getByText("1 of 1 connected consumer needs a delivery report to confirm the version received.")).toBeVisible();
    // The queue names one missing report (no report yet); it never fills the absent delivery in.
    expect(screen.getAllByText("Waiting for first report")).toHaveLength(1);
    click("Pilot application");
    expect(screen.getByText("Delivery receipts")).toBeVisible();
    // The drawer retains all five independent stages; the three unreported ones stay unknown.
    expect(screen.getAllByText("Waiting for first report").filter((node) => node.closest('[role="dialog"]'))).toHaveLength(3);
    expect(
      screen.getByText(/Delivered means the release was sent to this runtime/),
    ).toBeVisible();
    click("Close delivery receipts");
  });
  it("closes and drops receipt content on workspace access loss", () => {
    const { rerender } = render(
      <ContextConsumersView
        scopeKey="scope"
        state="ready"
        data={data}
        onPageChange={() => {}}
        onWindowChange={() => {}}
      />,
    );
    click("Pilot application");
    rerender(
      <ContextConsumersView scopeKey="scope" state="forbidden" data={data} />,
    );
    expect(screen.queryByText("Delivery receipts")).not.toBeInTheDocument();
    expect(screen.queryByText("Pilot application")).not.toBeInTheDocument();
  });
});
