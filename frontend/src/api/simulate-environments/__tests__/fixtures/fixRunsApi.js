import { vi } from "vitest";

import {
  FIXTURE_PULL_REQUEST_URL,
  fixtureFixRun,
  fixtureFixRuns,
  fixtureLaunch,
  fixtureLaunchOptions,
  fixturePatch,
  fixtureRunCalls,
  updateFixtureFixRun,
} from "./fixRuns";

// Test-only stand-in for the agent-fix-runs backend. It answers the contract's
// paths from the in-memory fixture store, so component tests exercise the same
// request code the app runs against the real API.
const BASE = "/simulate/api/agent-fix-runs/";

export const FIX_RUN_ENDPOINTS = {
  list: () => BASE,
  launchOptions: () => `${BASE}launch-options/`,
  detail: (id) => `${BASE}${id}/`,
  stop: (id) => `${BASE}${id}/stop/`,
  makeDefault: (id, cid) => `${BASE}${id}/candidates/${cid}/make-default/`,
  pullRequest: (id, cid) => `${BASE}${id}/candidates/${cid}/pull-request/`,
  patch: (id, cid) => `${BASE}${id}/candidates/${cid}/patch/`,
};

const ok = (data) => Promise.resolve({ data });
// The app's axios rejects with the body spread plus statusCode.
const fail = (statusCode, body) => Promise.reject({ ...body, statusCode });

const CANDIDATE = new RegExp(
  `^${BASE}([^/]+)/candidates/([^/]+)/(make-default|pull-request|patch)/$`,
);
const FIX = new RegExp(`^${BASE}([^/]+)/(stop/)?$`);

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

function makeDefault(fixId, candidateId) {
  const fix = fixtureFixRun(fixId);
  const candidate = fix.candidates.find((c) => c.id === candidateId);
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

function openPullRequest(fixId, candidateId) {
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
  const callsPath = new RegExp(
    `^${actual.endpoints.runResultsV3
      .calls("__ID__")
      .replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
      .replace("__ID__", "([^/]+)")}$`,
  );
  const get = vi.fn((url, config = {}) => {
    const params = config.params || {};
    if (url === FIX_RUN_ENDPOINTS.list()) {
      return ok(fixtureFixRuns(params.environment_id));
    }
    if (url === FIX_RUN_ENDPOINTS.launchOptions()) {
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
    if (url === FIX_RUN_ENDPOINTS.list()) {
      return ok(fixtureLaunch(null, body));
    }
    const cand = CANDIDATE.exec(url);
    if (cand && cand[3] === "make-default") return makeDefault(cand[1], cand[2]);
    if (cand && cand[3] === "pull-request") {
      return openPullRequest(cand[1], cand[2]);
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

// For vi.mock("src/utils/axios", ...): the real module with the fix-run paths
// spelled out (they are not in the generated contract yet) and the fake server.
export function withFakeFixRunsApi(actual) {
  return {
    ...actual,
    default: createFixRunsAxios(actual),
    endpoints: { ...actual.endpoints, agentFixRuns: FIX_RUN_ENDPOINTS },
  };
}
