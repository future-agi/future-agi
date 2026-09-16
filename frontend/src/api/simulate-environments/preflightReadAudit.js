// The one module the backend swap touches. It turns a preflight outcome
// ({ response | error | skipped }) into the ReadAudit view model the UI renders.
// The real-derived rules run first; a mock overlay then fills the
// reading/questions/section-gaps the happy-path backend does not carry yet.
//
// TODO: the backend preflight returns happy-path only today. MOCK_PREFLIGHT_FAILS
// overlays the designer's failing-check fixtures so the read-audit has content.
// Flip to false (then delete the overlay + the fixtures import) once the real
// response carries reading / questions / section issues.
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import {
  READER_STATUS,
  READ_SECTION_KEY,
  READ_AUDIT_COPY,
  PREFLIGHT_SKIPPED_ISSUE,
} from "src/sections/simulate/environments/buildEnvironment/readAudit.constants";
import { agentRefLabel } from "src/sections/simulate/environments/buildEnvironment/helpers/agentRefLabel";
import {
  MOCK_READING,
  MOCK_QUESTIONS,
  MOCK_SECTION_ISSUES,
} from "./_fixtures/preflightFails";

export const MOCK_PREFLIGHT_FAILS = true;

// Icons for the real-derived section gaps. Inlined here per plan §5.2 — this is
// the single derivation site, so they do not belong in a shared constants file.
const BLOCKING_FINDING_ICON = "solar:file-remove-linear";
const MISSING_CREDENTIAL_ICON = "solar:key-minimalistic-linear";

const emptyReading = () => ({ tools: [], rules: [], data: [], behavior: [] });

const omit = (obj, keys) => {
  const drop = new Set(keys);
  return Object.fromEntries(
    Object.entries(obj).filter(([key]) => !drop.has(key))
  );
};

// The first blocking packaging finding, with the candidate that carried it, in
// document order — or null when none blocks.
function firstBlockingFinding(candidates = []) {
  for (const candidate of candidates) {
    const finding = (candidate.findings || []).find((f) => f.blocking);
    if (finding) return { candidate, finding };
  }
  return null;
}

// Env-var names any credential_choices option can satisfy (options is string[][]
// per §5.2; .flat() also tolerates a flat string[] a caller might send).
function coveredCredentialNames(choices = []) {
  return new Set(choices.flatMap((choice) => (choice.options || []).flat()));
}

// Section gaps derived from the real response (real always wins over the mock).
function realSectionIssues({ response, skipped }) {
  const credentials = response?.credentials || {};
  const packaging = response?.packaging || {};
  const issues = {};

  const blocking = firstBlockingFinding(packaging.candidates);
  if (blocking) {
    issues[READ_SECTION_KEY.TOOLS] = {
      severity: "warning",
      icon: BLOCKING_FINDING_ICON,
      message: blocking.finding.message,
      hint: blocking.candidate.path,
      retryLabel: READ_AUDIT_COPY.retry,
    };
  }

  const covered = coveredCredentialNames(credentials.credential_choices);
  const missing = (credentials.requirements || []).find(
    (req) =>
      req.required && req.status === "missing" && !covered.has(req.environment_name)
  );
  if (missing) {
    issues[READ_SECTION_KEY.BEHAVIOR] = {
      severity: "warning",
      icon: MISSING_CREDENTIAL_ICON,
      message: `${missing.environment_name} is missing`,
      hint: missing.purpose,
      retryLabel: null,
    };
  }

  if (skipped) {
    issues[READ_SECTION_KEY.TOOLS] = PREFLIGHT_SKIPPED_ISSUE(skipped);
  }

  return issues;
}

function statsFrom(response) {
  const credentials = response?.credentials || {};
  const packaging = response?.packaging || {};
  return {
    scannedFiles: credentials.scanned_files ?? 0,
    detectedConnectors: credentials.detected_connectors ?? [],
    selectedPath: packaging.selected_path ?? null,
    readyToSubmit: !!response?.ready_to_submit,
  };
}

/**
 * @param {object} input                 - { response, error, skipped, draft, retriedSections }
 * @param {object} [options]             - { mock } — overlay toggle (defaults to MOCK_PREFLIGHT_FAILS)
 * @returns {import("./_fixtures/preflightFails").ReadAudit}
 */
export function preflightToReadAudit(
  { response, error, skipped, draft, retriedSections = [] },
  { mock = MOCK_PREFLIGHT_FAILS } = {}
) {
  const agentRef = agentRefLabel(draft);
  const base = {
    hardfailReason: null,
    agentRef,
    reading: emptyReading(),
    questions: [],
    sectionIssues: {},
    stats: statsFrom(response),
  };

  // 1. A thrown/rejected preflight is a hard fail — no reading, no overlay.
  if (error) {
    return { ...base, status: READER_STATUS.HARDFAIL, hardfailReason: errorMessage(error) };
  }

  // 2. A rejected credential probe is a hard fail — the source cannot be read.
  const rejected = (response?.credentials?.probe || []).filter((p) => p.ok === false);
  if (rejected.length) {
    return {
      ...base,
      status: READER_STATUS.HARDFAIL,
      hardfailReason: rejected
        .map((p) => p.message)
        .filter(Boolean)
        .join(" · "),
    };
  }

  // 3. Real-derived section gaps.
  const realIssues = realSectionIssues({ response, skipped });
  const notReady = response?.ready_to_submit === false;

  let sectionIssues = { ...realIssues };
  let reading = emptyReading();
  let questions = [];

  // Mock overlay — fill the fields the happy-path backend does not carry.
  // retriedSections removes MOCK gaps only; real gaps survive by spreading last.
  if (mock) {
    reading = MOCK_READING;
    questions = MOCK_QUESTIONS;
    sectionIssues = { ...omit(MOCK_SECTION_ISSUES, retriedSections), ...realIssues };
  }

  const hasIssue = Object.keys(sectionIssues).length > 0;
  const status = hasIssue || notReady ? READER_STATUS.WARNING : READER_STATUS.HEALTHY;

  return { ...base, status, reading, questions, sectionIssues };
}
