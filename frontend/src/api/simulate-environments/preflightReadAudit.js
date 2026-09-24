// The one module the backend swap touches. It turns a preflight outcome
// ({ response | error | skipped }) into the ReadAudit view model the UI renders.
// Everything the product shows is derived from the response: the packaging /
// credential gaps as section issues, and the backend's own `checks[]` verbatim.
//
// MOCK_PREFLIGHT_FAILS is OFF: the designer's failing-check fixtures are a demo
// aid only, opted into per call with `{ mock: true }`. Real users must never see
// invented tools, fixtures or questions presented as facts read from their agent.
// TODO(TH-7962): delete the overlay and the fixtures import once the response
// carries reading / questions of its own.
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

export const MOCK_PREFLIGHT_FAILS = false;

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

// The backend's own verdicts (HarnessPreflightResponse.checks), kept as a LIST:
// several can fail at once (credentials_present AND credentials_valid), so
// folding them into the one-per-section `sectionIssues` map would drop all but
// the last. `detail`, `missing` and `fix` are what tell the user what to do.
function checksFrom(response) {
  return (Array.isArray(response?.checks) ? response.checks : [])
    .filter((check) => check && check.id)
    .map((check) => ({
      id: check.id,
      label: check.label || check.id,
      status: check.status || "skipped",
      detail: check.detail || "",
      missing: Array.isArray(check.missing) ? check.missing : [],
      fix: check.fix || null,
    }));
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
    checks: [],
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

  // 3. Real-derived section gaps and the backend's own checks.
  const realIssues = realSectionIssues({ response, skipped });
  const checks = checksFrom(response);
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
  const checkFailed = checks.some((check) => check.status !== "passed");
  const status =
    hasIssue || notReady || checkFailed ? READER_STATUS.WARNING : READER_STATUS.HEALTHY;

  return { ...base, status, reading, questions, sectionIssues, checks };
}
