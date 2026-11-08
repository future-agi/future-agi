import { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
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
    screen.getByRole("checkbox", { name: /I reviewed this exact candidate/ }),
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
    expect(screen.getByText("Text comparison")).toBeVisible();
    expect(screen.getByText("Native application evidence")).toBeVisible();
    expect(
      screen.getByText(/2\/4 attempts graded · 2 incomplete · 1 regressions/),
    ).toBeVisible();
    expect(screen.getByText(/human disagreement/)).toBeVisible();
    expect(screen.getByText(/Operator-recorded, unimported/)).toBeVisible();
    expect(screen.getAllByText(/Outcome details not loaded/)).toHaveLength(1);
    expect(
      screen.getByRole("link", { name: "Open retained pipeline result" }),
    ).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getAllByText(/AGENTS.md · modified/)[0]).toBeVisible();
    expect(screen.getByText(/not a sandbox/)).toBeVisible();
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
    expect(decide).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Approve exact packet" }),
    ).toBeDisabled();
    acknowledge();
    click("Approve exact packet");
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
    acknowledge();
    click("Approve exact packet");
    await screen.findByText(/response was not confirmed/);
    expect(
      screen.getByRole("textbox", { name: "Review comment" }),
    ).toBeDisabled();
    click("Retry original request");
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(2));
    expect(decide.mock.calls[1][0]).toEqual(decide.mock.calls[0][0]);
  });
  it("blocks duplicate submissions after a confirmed decision until server state refreshes", async () => {
    const decide = vi.fn().mockResolvedValue({ id: ID });
    render(<ReleaseRequestPanel {...panelProps} onDecide={decide} />);
    acknowledge();
    click("Approve exact packet");
    await screen.findByText(/Request recorded/);
    expect(
      screen.getByRole("button", { name: "Approve exact packet" }),
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
      screen.getByRole("button", { name: "Approve exact packet" }),
    ).toBeDisabled();
    rerender(
      <ReleaseRequestPanel
        {...panelProps}
        review={{ complete: true, selected: [evidenceSelection(textEvidence)] }}
        onDecide={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Approve exact packet" }),
    ).toBeDisabled();
    expect(screen.getByText(/Evidence selection changed/)).toBeVisible();
  });
  it("preserves comment after a rejected CAS without refreshing the precondition", async () => {
    const decide = vi.fn().mockRejectedValue({ response: { status: 409 } });
    render(<ReleaseRequestPanel {...panelProps} onDecide={decide} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Review comment" }), {
      target: { value: "Inspect the new target." },
    });
    click("Request changes");
    await screen.findByText(/Request rejected/);
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
        screen.getByRole("button", { name: "Approve exact packet" }),
      ).toBeDisabled();
      expect(decide).not.toHaveBeenCalled();
    },
  );
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
    click("Release approved packet");
    expect(execute).not.toHaveBeenCalled();
    click("Move production");
    await screen.findByText("Durable release receipt");
    expect(screen.getByText("Currentness before the move")).toBeVisible();
    expect(screen.getByText("Pointer state after the move")).toBeVisible();
    expect(execute.mock.calls[0][0]).not.toHaveProperty("packet");
  });
  it("hides previous-scope private material and ignores a late completion", async () => {
    const d = deferred();
    const decide = vi.fn(() => d.promise);
    const { rerender } = render(
      <ReleaseRequestPanel {...panelProps} onDecide={decide} />,
    );
    acknowledge();
    click("Approve exact packet");
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
      data={inbox}
      onStatusChange={() => {}}
      onPageChange={() => {}}
      onOpen={() => setOpened(true)}
    />
  );
}
describe("reviewer arrival and annotation", () => {
  it("finds the exact review from a server-backed inbox without a supplied detail URL", () => {
    render(<Arrival />);
    expect(screen.getByText("1 unread assigned request(s)")).toBeVisible();
    click("refund-context");
    expect(screen.getByText("Exact release")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Approve exact packet" }),
    ).toBeDisabled();
  });
  it("removes inbox details and counts on role loss instead of rendering an empty success", () => {
    const { rerender } = render(
      <ReleaseInbox
        scopeKey="scope"
        state="ready"
        data={inbox}
        onStatusChange={() => {}}
        onPageChange={() => {}}
        onOpen={() => {}}
      />,
    );
    rerender(<ReleaseInbox scopeKey="scope" state="forbidden" data={inbox} />);
    expect(screen.queryByText(/1 unread/)).not.toBeInTheDocument();
    expect(screen.queryByText("refund-context")).not.toBeInTheDocument();
    expect(screen.getByText(/no longer have access/)).toBeVisible();
  });
  it("marks only the observed revision seen, without pretending that it is approved", async () => {
    const seen = vi.fn().mockResolvedValue({});
    render(
      <ReleaseInbox
        scopeKey="scope"
        state="ready"
        data={inbox}
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
    expect(screen.getByText("1 unread assigned request(s)")).toBeVisible();
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
        name: /saved candidate against its content origin/,
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
        name: /saved candidate against its content origin/,
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
  it("starts with no spending default and submits exact decimal text only after confirmation", async () => {
    const save = vi.fn().mockResolvedValue({ revision: 3 });
    render(
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
      screen.getByRole("button", { name: "Review policy change" }),
    ).toBeDisabled();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Period allowance (USD)" }),
      { target: { value: "12.00000001" } },
    );
    click("Review policy change");
    expect(save).not.toHaveBeenCalled();
    click("Apply policy change");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toMatchObject({
      revision: 2,
      periodLimitUsd: "12.00000001",
      enabled: false,
    });
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
    expect(screen.getByText("0.50000000")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Review policy change" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("textbox", { name: "Period allowance (USD)" }),
    ).toHaveValue("5.25");
  });
});
