// Local state for the read-audit screen (the designer's AgentReadReceipt): the
// user's answers to the open questions, which question is active, the reader
// health state and — only in the URL-forced demo — the section gaps to show.
//
// Section issues are NOT reducer state in the product flow: they come from
// `audit.sectionIssues`, which the preflight mapper already returns minus any
// section the user retried (retriedSections, T6/T7). So per-section "Retry read"
// only pokes the store; the audit re-derives without that mock gap on the next
// render. `demoIssues` exists solely so `?readerStatus=warning|hardfail` can walk
// the recovery arc without a real backend — it overrides the audit's issues when
// set, and stays null otherwise.
import { ORIGIN_ID } from "../provenance.constants";
import { READER_STATUS, MOCK_SECTION_ISSUES } from "../readAudit.constants";

// Verbatim from the designer's AgentReadReceipt.jsx (line 118): a question is
// resolved when it has a real pick or non-blank "Other" and wasn't skipped.
export const isResolved = (a) =>
  !!a && !a.skipped && (a.pick != null || (a.other && a.other.trim().length > 0));

// The section gaps in effect right now: the URL-forced demo issues when a demo
// state is active, otherwise the real (already retried-minus) audit issues.
export const resolveIssues = (state, audit) =>
  state.demoIssues ?? (audit?.sectionIssues || {});

// Precedence for the reader health state: URL `?readerStatus=` > `audit.status`
// (the designer's `> "warning"` default is unnecessary — audit.status is always
// set). `demoIssues` is only seeded for the URL demo states.
export function initialReadAuditState(audit, urlOverride) {
  const readerStatus = urlOverride || audit?.status;
  const demoIssues = urlOverride
    ? (urlOverride === READER_STATUS.WARNING || urlOverride === READER_STATUS.HARDFAIL
      ? MOCK_SECTION_ISSUES
      : {})
    : null;
  return { answers: {}, activeIdx: 0, readerStatus, demoIssues };
}

const setAnswer = (state, id, patch) => ({
  ...state,
  answers: {
    ...state.answers,
    [id]: { ...(state.answers[id] || {}), ...patch, skipped: false },
  },
});

export function readAuditReducer(state, action) {
  switch (action.type) {
    case "pick":
      return setAnswer(state, action.id, { pick: action.idx });
    case "other":
      return setAnswer(state, action.id, { other: action.text });
    case "skip":
      return {
        ...state,
        answers: {
          ...state.answers,
          [action.id]: { pick: null, other: "", skipped: true },
        },
      };
    case "back":
      return { ...state, activeIdx: Math.max(0, state.activeIdx - 1) };
    case "next":
      return { ...state, activeIdx: Math.min(action.total - 1, state.activeIdx + 1) };
    case "demoRetry": {
      // Cycle hard-fail → warning → healthy, exactly like the designer's
      // cycleReaderState: the first retry restores the demo gaps, the next clears
      // them.
      const wasHardfail = state.readerStatus === READER_STATUS.HARDFAIL;
      return {
        ...state,
        readerStatus: wasHardfail ? READER_STATUS.WARNING : READER_STATUS.HEALTHY,
        demoIssues: wasHardfail ? MOCK_SECTION_ISSUES : {},
      };
    }
    case "hydrate":
      // A refetched audit moves the reader state (e.g. hard-fail → healthy). Bail
      // out when nothing changed so the harness's fresh-audit-every-render never
      // costs a re-render.
      return action.audit?.status === state.readerStatus
        ? state
        : { ...state, readerStatus: action.audit?.status };
    default:
      return state;
  }
}

// One-glance counts for the stat bar and the open-questions column.
export function deriveCounts(state, audit) {
  const reading = audit?.reading || {};
  const questions = audit?.questions || [];
  const counts = {
    tools: reading.tools?.length || 0,
    rules: reading.rules?.length || 0,
    data: reading.data?.length || 0,
    behavior: reading.behavior?.length || 0,
  };
  const inferredCount = [
    ...(reading.behavior || []),
    ...(reading.tools || []),
    ...(reading.rules || []),
    ...(reading.data || []),
  ].filter((f) => f.origin === ORIGIN_ID.INFERRED).length;
  const resolvedCount = questions.filter((q) => isResolved(state.answers[q.id])).length;
  const skippedCount = questions.filter((q) => state.answers[q.id]?.skipped).length;
  const openCount = questions.length - resolvedCount - skippedCount;
  const issueCount = Object.keys(resolveIssues(state, audit)).length;
  return { counts, inferredCount, resolvedCount, skippedCount, openCount, issueCount };
}
