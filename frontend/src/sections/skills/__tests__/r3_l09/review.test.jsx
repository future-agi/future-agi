import { MemoryRouter } from "react-router-dom";
import { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render as rtlRender,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReleaseReviewBody, {
  evidenceProblems,
  evidenceSelection,
  ReviewFileDiff,
  TypedEvidenceList,
} from "../../review/ReleaseReviewBody";
import ReleaseRequestPanel from "../../review/ReleaseRequestPanel";
import ReleaseInbox from "../../review/ReleaseInbox";
import HumanCheckReview from "../../review/HumanCheckReview";
import ContextReleaseReview from "../../contexts/ContextReleaseReview";
import SpendPolicyPanel from "../../review/SpendPolicyPanel";
import {
  capability,
  changes,
  CONFIG,
  DIGEST,
  draftReview,
  ID,
  inbox,
  nativeEvidence,
  packet,
  policy,
  preview,
  request,
  textEvidence,
} from "./fixtures";

const render = (element) => rtlRender(element, { wrapper: MemoryRouter });

const media = vi.hoisted(() => ({ narrow: false }));
vi.mock("@mui/material", async (load) => ({
  ...(await load()),
  useMediaQuery: () => media.narrow,
}));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  media.narrow = false;
});
const click = (name) => fireEvent.click(screen.getByRole("button", { name }));
const acknowledge = () =>
  fireEvent.click(
    screen.getByRole("checkbox", { name: /I've checked this version/ }),
  );
const panelProps = {
  scopeKey: "org:workspace:reviewer",
  state: "ready",
  preview,
  request,
  permissions: { canDecide: true, canExecute: true },
  review: { complete: true, changes, capabilities: capability },
};
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

describe("typed release evidence", () => {
  it("keeps text/native outcome, immutable configuration and external pipeline claims separate", () => {
    render(
      <ReleaseReviewBody
        packet={packet}
        changes={changes}
        capabilities={capability}
        summaries={[
          {
            source: "simulation_native",
            sourceId: nativeEvidence.sourceId,
            planned: 4,
            graded: 2,
            incomplete: 2,
            regressions: 1,
            answer: "failed",
            selection: "unknown",
            delivery: "passed",
            tools: "failed",
            disagreements: 1,
          },
        ]}
      />,
    );
    expect(screen.getByText("Answer check · v7 vs v4")).toBeVisible();
    expect(screen.getByText("Application test · v7 vs v4")).toBeVisible();
    // The evidence row states its outcome once; the exact grading counts and the evidence ID are read in Inspect.
    expect(screen.queryByText(/attempts graded/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Evidence ID/)).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Inspect", exact: true })[1]);
    expect(
      screen.getAllByText(/2\/4 attempts graded · 2 incomplete · 1 regression/),
    ).toHaveLength(1);
    expect(screen.getByText("Evidence ID")).toBeVisible();
    expect(screen.getAllByText(/human disagreement/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Operator-recorded, unimported/)).toBeVisible();
    expect(screen.getAllByText(/Result not recorded; inspect before relying on it/)).toHaveLength(1);
    expect(
      screen.getByRole("link", { name: "Open retained pipeline result" }),
    ).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getAllByText(/AGENTS.md · modified/)[0]).toBeVisible();
    expect(screen.getByText("Permissions")).toBeVisible();
  });
  it.each([
    [{ visibility: "private" }, "Private"],
    [{ status: "running" }, "not settled"],
    [{ payloadExpiresAt: "2000-01-01T00:00:00Z" }, "expired"],
    [{ candidateDigest: CONFIG }, "different content"],
  ])(
    "rejects ineligible supplied references without discarding them: %j",
    (patch, reason) => {
      const ref = { ...textEvidence, ...patch };
      expect(evidenceProblems(ref, packet).join(" ")).toContain(reason);
      render(
        <TypedEvidenceList
          packet={packet}
          evidence={[ref]}
          selected={[evidenceSelection(ref)]}
          onSelectionChange={vi.fn()}
        />,
      );
      expect(screen.getByRole("checkbox")).toBeChecked();
    },
  );
  it("blocks native applicability when only runtime configuration changes", () => {
    expect(
      evidenceProblems(nativeEvidence, {
        ...packet,
        target: { ...packet.target, configurationDigest: DIGEST },
      }),
    ).toContain("Runtime configuration does not match the target");
  });
  it("never silently removes missing selected evidence", () => {
    const changed = vi.fn();
    render(
      <TypedEvidenceList
        packet={packet}
        evidence={[]}
        selected={[evidenceSelection(textEvidence)]}
        onSelectionChange={changed}
      />,
    );
    expect(changed).not.toHaveBeenCalled();
    expect(screen.getByText(/will not be silently dropped/)).toBeVisible();
    click("Remove explicitly");
    expect(changed).toHaveBeenCalledWith([]);
  });
  it("shares only after an explicit disclosure and never auto-attaches", async () => {
    const share = vi.fn().mockResolvedValue({});
    const select = vi.fn();
    render(
      <TypedEvidenceList
        packet={packet}
        evidence={[{ ...textEvidence, visibility: "private" }]}
        selected={[]}
        onSelectionChange={select}
        onShare={share}
      />,
    );
    click("Share evidence");
    expect(share).not.toHaveBeenCalled();
    expect(screen.getByText(/Sharing does not extend expiry/)).toBeVisible();
    click("Share with workspace");
    await waitFor(() => expect(share).toHaveBeenCalledTimes(1));
    expect(select).not.toHaveBeenCalled();
  });
  it("expires a still-open review without a user rerender", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-08T10:00:00Z"));
    const expiring = {
      ...textEvidence,
      payloadExpiresAt: "2026-11-08T10:00:02Z",
    };
    render(
      <TypedEvidenceList
        packet={packet}
        evidence={[expiring]}
        selected={[]}
        onSelectionChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("checkbox")).toBeEnabled();
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByRole("checkbox")).toBeDisabled();
  });
  it("uses full-width unified diff with explicit change markers at narrow width", () => {
    media.narrow = true;
    render(<ReviewFileDiff file={changes[1]} />);
    const region = screen.getByRole("region", { name: "Unified file changes" });
    expect(within(region).getByLabelText("removed")).toHaveTextContent("−");
    expect(within(region).getByLabelText("added")).toHaveTextContent("+");
    expect(region).toHaveTextContent("Use fourteen days.");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
  it("does not turn an unloaded diff into an empty successful comparison", () => {
    render(<ReviewFileDiff file={{ path: "AGENTS.md" }} />);
    expect(screen.getByText(/Exact file text has not loaded/)).toBeVisible();
  });
  it("does not activate a javascript external pipeline URL", () => {
    render(
      <ReleaseReviewBody
        packet={{
          ...packet,
          externalPipeline: {
            ...packet.externalPipeline,
            url: ["javascript", "alert(1)"].join(":"),
          },
        }}
      />,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("exact decisions and retry safety", () => {
  it("does not decide from merely opening/reading and sends only the exact reviewed packet preconditions", async () => {
    const decide = vi.fn().mockResolvedValue({ id: ID, status: "approved" });
    render(<ReleaseRequestPanel {...panelProps} onDecide={decide} />);
    click('Add comment');
    expect(decide).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Approve release" }),
    ).toBeDisabled();
    acknowledge();
    click("Approve release");
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1));
    expect(decide.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        requestId: ID,
        revision: 2,
        expectedFingerprint: DIGEST,
        decision: "approve",
        comment: "",
        idempotencyKey: expect.any(String),
      }),
    );
  });
  it("reuses the original body and operation key after an unknown response", async () => {
    const decide = vi
      .fn()
      .mockRejectedValueOnce(new Error("lost response"))
      .mockResolvedValue({ id: ID });
    render(<ReleaseRequestPanel {...panelProps} onDecide={decide} />);
    click('Add comment');
    acknowledge();
    click("Approve release");
    await screen.findByText(/couldn't confirm whether this went through/);
    expect(
      screen.getByRole("textbox", { name: "Review comment" }),
    ).toBeDisabled();
    click("Check status");
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(2));
    expect(decide.mock.calls[1][0]).toEqual(decide.mock.calls[0][0]);
  });
  it("retains an uncertain original key when a recovery attempt loses authority", async () => {
    const decide = vi
      .fn()
      .mockRejectedValueOnce(new Error("response lost"))
      .mockRejectedValueOnce({ response: { status: 403 } })
      .mockResolvedValue({ id: ID });
    render(<ReleaseRequestPanel {...panelProps} onDecide={decide} />);
    click('Add comment');
    acknowledge();
    click("Approve release");
    await screen.findByText(/couldn't confirm whether this went through/);
    click("Check status");
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Check status" })).toBeEnabled(),
    );
    expect(
      screen.getByRole("textbox", { name: "Review comment" }),
    ).toBeDisabled();
    click("Check status");
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(3));
    expect(decide.mock.calls[2][0]).toEqual(decide.mock.calls[0][0]);
  });
  it("blocks duplicate submissions after a confirmed decision until server state refreshes", async () => {
    const decide = vi.fn().mockResolvedValue({ id: ID });
    render(<ReleaseRequestPanel {...panelProps} onDecide={decide} />);
    click('Add comment');
    acknowledge();
    click("Approve release");
    await screen.findByText(/Request recorded/);
    expect(
      screen.getByRole("button", { name: "Approve release" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Request changes" }),
    ).toBeDisabled();
  });
  it("requires complete review and a packet matching the chosen evidence set", () => {
    const { rerender } = render(
      <ReleaseRequestPanel
        {...panelProps}
        review={{ complete: false }}
        onDecide={vi.fn()}
      />,
    );
    acknowledge();
    expect(
      screen.getByRole("button", { name: "Approve release" }),
    ).toBeDisabled();
    rerender(
      <ReleaseRequestPanel
        {...panelProps}
        review={{ complete: true, selected: [evidenceSelection(textEvidence)] }}
        onDecide={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Approve release" }),
    ).toBeDisabled();
    click("Why this review is paused");
    expect(screen.getByText(/Evidence selection changed/)).toBeVisible();
  });
  it("preserves comment after a rejected CAS without refreshing the precondition", async () => {
    const decide = vi.fn().mockRejectedValue({ response: { status: 409 } });
    render(<ReleaseRequestPanel {...panelProps} onDecide={decide} />);
    click('Add comment');
    fireEvent.change(screen.getByRole("textbox", { name: "Review comment" }), {
      target: { value: "Inspect the new target." },
    });
    click("Request changes");
    await screen.findByText(/Approval no longer applies/);
    expect(screen.getByRole("textbox", { name: "Review comment" })).toHaveValue(
      "Inspect the new target.",
    );
    expect(decide.mock.calls[0][0].expectedFingerprint).toBe(DIGEST);
  });
  it.each(["annotation", "target", "role", "packet"])(
    "blocks an approval after a %s race",
    (race) => {
      const decide = vi.fn();
      const { rerender } = render(
        <ReleaseRequestPanel {...panelProps} onDecide={decide} />,
      );
      acknowledge();
      const patch =
        race === "annotation"
          ? {
              currentEvidence: [
                { ...textEvidence, annotationRevision: 2 },
                nativeEvidence,
              ],
            }
          : race === "target"
            ? { currentTarget: { ...packet.target, revision: 3 } }
            : race === "role"
              ? { permissions: {} }
              : { preview: { ...preview, fingerprint: CONFIG } };
      rerender(
        <ReleaseRequestPanel {...panelProps} {...patch} onDecide={decide} />,
      );
      expect(
        screen.getByRole("button", { name: "Approve release" }),
      ).toBeDisabled();
      expect(decide).not.toHaveBeenCalled();
    },
  );
  it("lets an assigned reviewer decline a stale packet but never approve or release it", async () => {
    const decide = vi.fn().mockResolvedValue({ id: ID });
    render(
      <ReleaseRequestPanel
        {...panelProps}
        preview={{ ...preview, current: false }}
        onDecide={decide}
      />,
    );
    expect(screen.getByText(/Approval no longer applies/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Approve release" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Release to production" })).toBeDisabled();
    click('Add comment');
    fireEvent.change(screen.getByRole("textbox", { name: "Review comment" }), {
      target: { value: "Production moved while this was pending." },
    });
    click("Request changes");
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1));
    expect(decide.mock.calls[0][0].decision).toBe("request_changes");
  });
  it("separates attributed decision, release execution and before/after receipt", async () => {
    const execute = vi
      .fn()
      .mockResolvedValue({
        requestId: ID,
        decisionId: ID,
        executedBy: ID,
        currentnessAtAction: { baseline: true },
        postMoveState: { version: 7, eventId: ID },
      });
    render(
      <ReleaseRequestPanel
        {...panelProps}
        request={{ ...request, status: "approved" }}
        onExecute={execute}
      />,
    );
    acknowledge();
    click("Release to production");
    expect(execute).not.toHaveBeenCalled();
    click("Move production");
    await screen.findByText("Release receipt");
    fireEvent.click(await screen.findByRole("button", { name: "Audit details" }));
    expect(screen.getByText("Before the move")).toBeVisible();
    expect(screen.getByText("After the move")).toBeVisible();
    expect(execute.mock.calls[0][0]).not.toHaveProperty("packet");
  });
  it("offers no decision or release on a request replaced by a fresh packet", () => {
    const decide = vi.fn();
    const execute = vi.fn();
    render(
      <ReleaseRequestPanel
        {...panelProps}
        request={{ ...request, status: "superseded" }}
        onDecide={decide}
        onExecute={execute}
      />,
    );
    acknowledge();
    expect(screen.getByRole('button', { name: 'Add comment' })).toBeDisabled();
    expect(screen.queryByLabelText('Review comment')).not.toBeInTheDocument();
    for (const name of [
      "Approve release",
      "Request changes",
      "Release to production",
    ]) {
      const action = screen.queryByRole("button", { name });
      if (action) expect(action).toBeDisabled();
    }
    expect(decide).not.toHaveBeenCalled();
    expect(execute).not.toHaveBeenCalled();
  });
  it("hides previous-scope private material and ignores a late completion", async () => {
    const d = deferred();
    const decide = vi.fn(() => d.promise);
    const { rerender } = render(
      <ReleaseRequestPanel {...panelProps} onDecide={decide} />,
    );
    acknowledge();
    click("Approve release");
    rerender(
      <ReleaseRequestPanel
        {...panelProps}
        scopeKey="different:user"
        state="forbidden"
        onDecide={decide}
      />,
    );
    await act(async () => {
      d.resolve({ id: ID });
    });
    expect(screen.queryByText(DIGEST)).not.toBeInTheDocument();
    expect(screen.queryByText(/Request recorded/)).not.toBeInTheDocument();
  });
});

function Arrival() {
  const [opened, setOpened] = useState(false);
  return opened ? (
    <ReleaseRequestPanel {...panelProps} />
  ) : (
    <ReleaseInbox
      scopeKey="scope"
      state="ready"
      data={{ ...inbox, pendingCount: 1 }}
      onStatusChange={() => {}}
      onPageChange={() => {}}
      onOpen={() => setOpened(true)}
    />
  );
}
describe("reviewer arrival and annotation", () => {
  it("finds the exact review from a server-backed inbox without a supplied detail URL", () => {
    render(<Arrival />);
    // The tab label carries the waiting count; no separate count line repeats it.
    expect(screen.queryByText("1 request waiting for your decision")).not.toBeInTheDocument();
    click("refund-context");
    expect(screen.getByText(/Production moves/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Approve release" }),
    ).toBeDisabled();
  });
  it("removes inbox details and counts on role loss instead of rendering an empty success", () => {
    const { rerender } = render(
      <ReleaseInbox
        scopeKey="scope"
        state="ready"
        data={{ ...inbox, pendingCount: 1 }}
        onStatusChange={() => {}}
        onPageChange={() => {}}
        onOpen={() => {}}
      />,
    );
    rerender(<ReleaseInbox scopeKey="scope" state="forbidden" data={{ ...inbox, pendingCount: 1 }} />);
    expect(screen.queryByText(/1 unread/)).not.toBeInTheDocument();
    expect(screen.queryByText("refund-context")).not.toBeInTheDocument();
    expect(screen.getByText(/don't have access to reviews/)).toBeVisible();
  });
  it("marks only the observed revision seen, without pretending that it is approved", async () => {
    const seen = vi.fn().mockResolvedValue({});
    render(
      <ReleaseInbox
        scopeKey="scope"
        state="ready"
        data={{ ...inbox, pendingCount: 1 }}
        onStatusChange={() => {}}
        onPageChange={() => {}}
        onOpen={() => {}}
        onSeen={seen}
      />,
    );
    click("Actions for refund-context");
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Mark this revision seen" }),
    );
    await waitFor(() => expect(seen).toHaveBeenCalledTimes(1));
    expect(seen.mock.calls[0][0]).toMatchObject({ requestId: ID, revision: 2 });
    // The tab label carries the waiting count; no separate count line repeats it.
    expect(screen.queryByText("1 request waiting for your decision")).not.toBeInTheDocument();
  });
  it("preserves machine verdict and appends an attributed disagreement with revision CAS", async () => {
    const append = vi.fn().mockResolvedValue({ id: ID });
    const ref = {
      source: "skills_text",
      sourceId: ID,
      annotationRevision: 1,
      checkId: ID,
      attemptId: ID,
    };
    const { rerender } = render(
      <HumanCheckReview
        scopeKey="scope"
        state="ready"
        reference={ref}
        machineVerdict="pass"
        canAnnotate
        onAppend={append}
      />,
    );
    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "Your assessment" }),
    );
    clickOption("Disagree");
    fireEvent.change(
      screen.getByRole("textbox", { name: "Cited clause or answer excerpt" }),
      { target: { value: "Fourteen days" } },
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Review explanation" }),
      { target: { value: "The policy excludes this case." } },
    );
    rerender(
      <HumanCheckReview
        scopeKey="scope"
        state="ready"
        reference={{ ...ref, annotationRevision: 2 }}
        machineVerdict="pass"
        canAnnotate
        onAppend={append}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Append review" }),
    ).toBeDisabled();
    click("I reviewed the new annotations");
    click("Append review");
    await waitFor(() => expect(append).toHaveBeenCalledTimes(1));
    expect(append.mock.calls[0][0]).toMatchObject({
      annotationRevision: 2,
      verdict: "disagree",
    });
    expect(screen.getByText("Passed")).toBeVisible();
  });
});
function clickOption(name) {
  fireEvent.click(screen.getByRole("option", { name }));
}

describe("publication and explicit spending", () => {
  it("publishes a historical-origin repair with reviewed head, no label and no force", async () => {
    const publish = vi.fn().mockResolvedValue({ version: 7 });
    render(
      <ContextReleaseReview
        scopeKey="scope"
        state="ready"
        draftReview={draftReview}
        canPublish
        onPublish={publish}
      />,
    );
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /these changes against the current published version/,
      }),
    );
    click("Publish version");
    await waitFor(() => expect(publish).toHaveBeenCalledTimes(1));
    expect(publish.mock.calls[0][0]).toMatchObject({
      revision: 8,
      baseVersion: 4,
      expectedHeadVersion: 6,
      expectedDigest: DIGEST,
      reviewFingerprint: DIGEST,
    });
    expect(publish.mock.calls[0][0]).not.toHaveProperty("labels");
    expect(publish.mock.calls[0][0]).not.toHaveProperty("force");
  });
  it("blocks unreviewed head and stale draft transitions", () => {
    const { rerender } = render(
      <ContextReleaseReview
        scopeKey="scope"
        state="ready"
        draftReview={draftReview}
        canPublish
        onPublish={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /these changes against the current published version/,
      }),
    );
    rerender(
      <ContextReleaseReview
        scopeKey="scope"
        state="ready"
        draftReview={{ ...draftReview, reviewFingerprint: CONFIG }}
        canPublish
        onPublish={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Publish version" }),
    ).toBeDisabled();
  });
  it("carries the reviewer's acknowledgements and note across the head acknowledgment inside the publish click, and only that step", async () => {
    const warned = {
      ...draftReview,
      headAcknowledged: false,
      note: "",
      checks: { items: [], summary: { errors: 0, warnings: 1, infos: 0 }, tokens: {} },
    };
    // The head acknowledgment now happens inside the one publish click (the controller chains it).
    const onPublish = vi.fn().mockResolvedValue({});
    const props = { scopeKey: "scope", state: "ready", canPublish: true, onPublish };
    const { rerender } = render(<ContextReleaseReview {...props} draftReview={warned} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Version note" }), { target: { value: "Cap refunds" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /these changes against the current published version/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /check warnings/ }));
    click("Publish version");
    await waitFor(() => expect(onPublish).toHaveBeenCalledTimes(1));
    // The reloaded review is for the same candidate, head and warnings with a new fingerprint.
    const acknowledged = { ...warned, headAcknowledged: true, revision: 9, reviewFingerprint: CONFIG };
    rerender(<ContextReleaseReview {...props} draftReview={acknowledged} />);
    expect(screen.getByRole("checkbox", { name: /these changes against the current published version/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /check warnings/ })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "Version note" })).toHaveValue("Cap refunds");
    expect(screen.getByRole("button", { name: "Publish version" })).toBeEnabled();
    // A candidate or warning change after the head step starts the review over.
    rerender(<ContextReleaseReview {...props} draftReview={{ ...acknowledged, candidateDigest: CONFIG }} />);
    expect(screen.getByRole("button", { name: "Publish version" })).toBeDisabled();
  });
  it("starts with no spending default and submits exact decimal text only after confirmation", async () => {
    const save = vi.fn().mockResolvedValue({ revision: 3 });
    const { rerender } = render(
      <SpendPolicyPanel
        scopeKey="scope"
        state="ready"
        policy={policy}
        canManage
        onSave={save}
      />,
    );
    expect(
      screen.getByRole("textbox", { name: "Period allowance (USD)" }),
    ).toHaveValue("");
    expect(
      screen.getByRole("button", { name: "Save" }),
    ).toBeDisabled();
    // The lead says what is true now, in currency, not accounting fields.
    expect(screen.getByText("Paid test runs are off for this workspace.")).toBeVisible();
    // One summary line under it, with the caveat once (UX iteration 4, SH7).
    expect(screen.getByText("$2.12 used · Nov 1–30 (UTC). Estimates, not invoices.")).toBeVisible();
    // The box below is the only "off" control; the field hint only describes the amount.
    expect(screen.getByText("Monthly limit in USD.")).toBeVisible();
    expect(screen.queryByText(/Leave empty/)).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Allow paid test runs up to this amount" })).not.toBeChecked();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Period allowance (USD)" }),
      { target: { value: "12.00000001" } },
    );
    click("Save");
    expect(save).not.toHaveBeenCalled();
    click("Save allowance");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toMatchObject({
      revision: 2,
      periodLimitUsd: "12.00000001",
      enabled: false,
    });
    // The refetch after our own save carries the revision it produced: no false
    // "changed while you were editing" beside "Allowance saved."
    expect(await screen.findByText(/^Allowance saved\./)).toBeVisible();
    rerender(<SpendPolicyPanel scopeKey="scope" state="ready" policy={{ ...policy, revision: 3, periodLimitUsd: "12.00000001" }} canManage onSave={save} />);
    expect(screen.queryByText(/changed while you were editing/)).not.toBeInTheDocument();
    // Someone else's later change still blocks.
    rerender(<SpendPolicyPanel scopeKey="scope" state="ready" policy={{ ...policy, revision: 4, periodLimitUsd: "12.00000001" }} canManage onSave={save} />);
    expect(screen.getByText(/changed while you were editing/)).toBeVisible();
  });
  it("keeps uncertain prior-period reservations visible and blocks stale budget updates", () => {
    const { rerender } = render(
      <SpendPolicyPanel
        scopeKey="scope"
        state="ready"
        policy={policy}
        canManage
        onSave={vi.fn()}
      />,
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Period allowance (USD)" }),
      { target: { value: "5.25" } },
    );
    rerender(
      <SpendPolicyPanel
        scopeKey="scope"
        state="ready"
        policy={{ ...policy, revision: 3 }}
        canManage
        onSave={vi.fn()}
      />,
    );
    // Unsettled spend stays visible in the summary; exact decimals are one click away.
    expect(screen.getByText(/\$1\.00 is held by runs whose final cost isn.t settled yet/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "How usage is counted" }));
    expect(screen.getByText("0.50000000")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Save" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("textbox", { name: "Period allowance (USD)" }),
    ).toHaveValue("5.25");
  });
});

it('puts pending reviews first and reserves the unread marker and Review action for them', () => {
  const onOpen = vi.fn();
  const pending = { ...inbox.results[0], id: 'pending', name: 'Pending review', status: 'pending', unread: true };
  const released = { ...inbox.results[0], id: 'released', name: 'Released review', status: 'released', unread: true };
  render(<ReleaseInbox scopeKey="scope" state="ready" data={{ ...inbox, results: [released, pending] }} onOpen={onOpen} onStatusChange={vi.fn()} onPageChange={vi.fn()} />);
  const rows = within(screen.getByRole('table', { name: 'Assigned release reviews' })).getAllByRole('row').slice(1);
  expect(rows[0]).toHaveTextContent('Pending review');
  expect(within(rows[0]).getByText('Unread')).toBeInTheDocument();
  expect(within(rows[1]).queryByText('Unread')).not.toBeInTheDocument();
  expect(screen.queryByText('New', { exact: true })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Review', exact: true }));
  expect(onOpen).toHaveBeenCalledWith(pending);
  expect(screen.getByRole('combobox', { name: 'Request status' })).toHaveTextContent('All');
});

it('names what the tab counts, shows Review only to the assigned reviewer and Open to everyone else', () => {
  const pending = { ...inbox.results[0], id: 'pending', name: 'Pending review', status: 'pending', authorId: 'author', recipients: [{ id: 'reviewer', name: 'Maya Chen' }] };
  const released = { ...inbox.results[0], id: 'released', name: 'Released review', status: 'released', unread: false, authorId: 'author' };
  const props = { scopeKey: 'scope', state: 'ready', onStatusChange: vi.fn(), onPageChange: vi.fn(), onOpen: vi.fn(), onToggleAuthored: vi.fn() };
  const data = { ...inbox, pendingCount: 1, results: [released, pending] };
  const { rerender } = render(<ReleaseInbox {...props} data={data} currentUserId="reviewer" />);
  expect(screen.getByText('Review changes before they reach production.')).toBeVisible();
  expect(screen.getByRole('tab', { name: 'Assigned to me · 1 waiting' })).toBeVisible();
  expect(screen.getByRole('tab', { name: 'My requests' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Review', exact: true })).toBeVisible();
  expect(screen.getAllByRole('button', { name: 'Open', exact: true })).toHaveLength(1);
  // The requester opens their own request; they are never offered "Review".
  rerender(<ReleaseInbox {...props} data={data} currentUserId="author" />);
  expect(screen.queryByRole('button', { name: 'Review', exact: true })).not.toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: 'Open', exact: true })).toHaveLength(2);
  rerender(<ReleaseInbox {...props} data={data} currentUserId="reviewer" authored />);
  expect(screen.queryByRole('button', { name: 'Review', exact: true })).not.toBeInTheDocument();
  rerender(<ReleaseInbox {...props} data={{ ...data, pendingCount: 0 }} currentUserId="reviewer" />);
  expect(screen.getByRole('tab', { name: 'Assigned to me' })).toBeVisible();
});

it('uses small outlined buttons for every desktop row action', () => {
  const pending = { ...inbox.results[0], id: 'pending', name: 'Pending review', status: 'pending', authorId: 'author', recipients: [{ id: 'reviewer', name: 'Maya Chen' }] };
  const released = { ...inbox.results[0], id: 'released', name: 'Released review', status: 'released', unread: false, authorId: 'author' };
  render(<ReleaseInbox scopeKey="scope" state="ready" data={{ ...inbox, pendingCount: 1, results: [released, pending] }} currentUserId="reviewer" onStatusChange={vi.fn()} onPageChange={vi.fn()} onOpen={vi.fn()} />);
  [screen.getByRole('button', { name: 'Review', exact: true }), ...screen.getAllByRole('button', { name: 'Open', exact: true })].forEach((button) => {
    expect(button).toHaveClass('MuiButton-outlined');
    expect(button).toHaveClass('MuiButton-sizeSmall');
  });
});

it('stacks each phone card as name and status, request title, then who and when, and opens the request from the whole card', () => {
  media.narrow = true;
  const onOpen = vi.fn();
  const pending = { ...inbox.results[0], id: 'pending', name: 'Pending review', status: 'pending', authorName: 'Sam Rivera', updatedAt: '2026-11-12T11:50:00Z', authorId: 'author', recipients: [{ id: 'reviewer', name: 'Maya Chen' }] };
  render(<ReleaseInbox scopeKey="scope" state="ready" data={{ ...inbox, pendingCount: 1, results: [pending] }} currentUserId="reviewer" onStatusChange={vi.fn()} onPageChange={vi.fn()} onOpen={onOpen} />);
  const card = screen.getByRole('button', { name: 'Pending review', exact: true });
  // No separate Review or Open button on a phone: the card is the one tap target; the menu stays at its top right.
  expect(screen.queryByRole('button', { name: 'Review', exact: true })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Open', exact: true })).not.toBeInTheDocument();
  expect(within(card).getByText('In review')).toBeVisible();
  expect(within(card).getByText(/Sam Rivera ·/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Actions for Pending review' })).toBeVisible();
  expect(card.contains(screen.getByRole('button', { name: 'Actions for Pending review' }))).toBe(false);
  fireEvent.click(card);
  expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'pending' }));
});
