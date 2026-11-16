import { vi } from "vitest";

import {
  FIXTURE_PULL_REQUEST_URL,
  fixtureFixRun,
  fixtureFixRuns,
  fixtureLaunch,
  fixtureLaunchOptions,
  fixturePatch,
  fixtureRetry,
  fixtureRunCalls,
  updateFixtureFixRun,
} from "./fixRuns";

// Test-only stand-in for the agent-fix-runs backend. It answers the real
// registry's paths from the in-memory fixture store, so a wrong URL in product
// code falls through to the real axios and fails the test.
const ok = (data) => Promise.resolve({ data });
// The app's axios rejects with the body spread plus statusCode.
const fail = (statusCode, body) => Promise.reject({ ...body, statusCode });

const escape = (path) => path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function readFix(fixId) {
  const fix = fixtureFixRun(fixId);
  if (!fix) return fail(404, { detail: "Not found." });
  // A stop settles within ~10 s on the real engine: the read after the one
  // that showed "stopping" shows "stopped".
  if (fix.state === "stopping" && fix.stop_requested) {
    updateFixtureFixRun(fixId, (f) => ({
      ...f,
      state: "stopped",
      finished_at: new Date().toISOString(),
    }));
  }
  return ok(fix);
}

// The backend's ship rules (require_shippable): the winner ships without a
// reason; a trial only its must-pass check refused ships with one; nothing else.
function refuseShip(fix, candidate, reason) {
  const refuse = (error) =>
    fail(error === "fix_run_active" ? 409 : 422, { error, message: error });
  if (["queued", "running", "stopping"].includes(fix.state)) {
    return refuse("fix_run_active");
  }
  if (candidate.outcome === "winner") {
    return reason ? refuse("fix_override_not_needed") : null;
  }
  if (!candidate.override?.allowed) return refuse("fix_candidate_not_proven");
  return reason ? null : refuse("fix_override_reason_required");
}

// Kept once, as the backend does: a later ship keeps the first reason.
const recordOverride = (fixId, candidateId, reason) =>
  reason &&
  updateFixtureFixRun(fixId, (f) => ({
    ...f,
    candidates: f.candidates.map((c) =>
      c.id === candidateId && !c.override.at
        ? {
            ...c,
            override: {
              ...c.override,
              reason,
              by: { id: "user-1", name: "Test User" },
              at: "2026-11-16T10:00:00Z",
            },
          }
        : c,
    ),
  }));

function makeDefault(fixId, candidateId, body = {}) {
  const fix = fixtureFixRun(fixId);
  const candidate = fix.candidates.find((c) => c.id === candidateId);
  const refused = refuseShip(fix, candidate, body.override_reason);
  if (refused) return refused;
  recordOverride(fixId, candidateId, body.override_reason);
  if (fix.environment.default_agent_version_id === candidate.agent_version_id) {
    return ok({ outcome: "already_default" });
  }
  updateFixtureFixRun(fixId, (f) => ({
    ...f,
    environment: {
      ...f.environment,
      default_agent_version_id: candidate.agent_version_id,
    },
  }));
  return ok({ outcome: "activated" });
}

function openPullRequest(fixId, candidateId, body = {}) {
  const fix = fixtureFixRun(fixId);
  const candidate = fix.candidates.find((c) => c.id === candidateId);
  const refused = refuseShip(fix, candidate, body.override_reason);
  if (refused) return refused;
  recordOverride(fixId, candidateId, body.override_reason);
  updateFixtureFixRun(fixId, (f) => ({
    ...f,
    candidates: f.candidates.map((c) =>
      c.id === candidateId
        ? { ...c, pull_request_url: FIXTURE_PULL_REQUEST_URL }
        : c,
    ),
  }));
  return ok({ pull_request_url: FIXTURE_PULL_REQUEST_URL });
}

export function createFixRunsAxios(actual) {
  const fixRuns = actual.endpoints.agentFixRuns;
  const base = escape(fixRuns.list());
  const CANDIDATE = new RegExp(
    `^${base}([^/]+)/candidates/([^/]+)/(make-default|pull-request|patch)/$`,
  );
  const FIX = new RegExp(`^${base}([^/]+)/(stop/)?$`);
  const RETRY = new RegExp(`^${base}([^/]+)/retry/$`);
  const callsPath = new RegExp(
    `^${escape(actual.endpoints.runResultsV3.calls("__ID__")).replace(
      "__ID__",
      "([^/]+)",
    )}$`,
  );
  const get = vi.fn((url, config = {}) => {
    const params = config.params || {};
    if (url === fixRuns.list()) {
      const all = fixtureFixRuns(params.environment_id);
      const page = params.page ?? 1;
      const limit = params.limit ?? 25;
      return ok({
        count: all.length,
        results: all.slice((page - 1) * limit, page * limit),
      });
    }
    if (url === fixRuns.launchOptions()) {
      return ok(fixtureLaunchOptions(params.source_run_id));
    }
    const calls = callsPath.exec(url);
    if (calls) return ok(fixtureRunCalls(calls[1], params));
    const cand = CANDIDATE.exec(url);
    if (cand && cand[3] === "patch") return ok(fixturePatch(cand[2]));
    const fix = FIX.exec(url);
    if (fix && !fix[2]) return readFix(fix[1]);
    return actual.default.get(url, config);
  });
  const post = vi.fn((url, body, config) => {
    if (url === fixRuns.list()) {
      return ok(fixtureLaunch(null, body));
    }
    const retry = RETRY.exec(url);
    if (retry) return ok(fixtureRetry(retry[1]));
    const cand = CANDIDATE.exec(url);
    if (cand && cand[3] === "make-default") {
      return makeDefault(cand[1], cand[2], body);
    }
    if (cand && cand[3] === "pull-request") {
      return openPullRequest(cand[1], cand[2], body);
    }
    const fix = FIX.exec(url);
    if (fix && fix[2]) {
      return ok(
        updateFixtureFixRun(fix[1], (f) => ({
          ...f,
          state: "stopping",
          stop_requested: true,
        })),
      );
    }
    return actual.default.post(url, body, config);
  });
  return { get, post };
}

// For vi.mock("src/utils/axios", ...): the real module with the fake server.
export function withFakeFixRunsApi(actual) {
  return { ...actual, default: createFixRunsAxios(actual) };
}
