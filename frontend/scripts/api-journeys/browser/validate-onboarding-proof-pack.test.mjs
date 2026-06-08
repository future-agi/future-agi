import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  EXPECTED_CHILDREN,
  validateProofPack,
} from "./validate-onboarding-proof-pack.mjs";

const MANIFEST_SCHEMA =
  "onboarding-real-signup-proof-pack-manifest-2026-05-30.v1";
const REPORT_SCHEMA = "onboarding-real-signup-smoke-report-2026-05-29.v1";

async function writeJson(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function reportFor(child, dir, overrides = {}) {
  const realEvidence = {
    first_value_posthog_event: {
      event: "onboarding_first_value_reached",
      properties: {
        source: "onboarding",
        quick_start_goal: "monitor_production_ai_app",
        quick_start_id: "observe",
        quick_start_primary_path: "observe",
        primary_path: "observe",
        activation_stage: "activated",
        activation_event_name: "first_quality_loop_completed",
        activation_event_path: "evals",
        daily_quality_available: true,
        is_sample: false,
      },
    },
    browser_state: {
      initialRender: "done",
      organizationId: "org-1",
      redirectUrl: null,
      workspaceId: "workspace-1",
    },
    daily_quality_cta_href: "/dashboard/home?mode=daily-quality",
    eval_first_quality_loop_completed_event: {
      event_name: "first_quality_loop_completed",
      is_sample: false,
    },
    eval_fix_rerun_completed_event: {
      event_name: "onboarding_eval_fix_rerun_completed",
    },
    eval_fix_rerun_reviewed_event: {
      event_name: "onboarding_eval_fix_rerun_reviewed",
    },
    eval_result_reviewed_event: { event_name: "eval_failures_reviewed" },
    eval_run_completed_event: { event_name: "eval_run_completed" },
    eval_source_fix_cta_event: {
      event_name: "onboarding_eval_source_fix_cta_clicked",
    },
    eval_source_fix_route_event: {
      event_name: "onboarding_eval_source_fix_route_viewed",
    },
    expected_daily_quality_available: true,
    onboarding_post: { goals: ["Monitor production AI app"] },
    post_review_state: { stage: "activated", is_activated: true },
    real_observe_project: { projectId: "project-1" },
    real_trace: { traceId: "trace-1" },
    real_trace_review_event: {
      event_name: "trace_detail_opened",
      is_sample: false,
    },
    real_trace_review_state: { stage: "review_first_trace" },
    screenshot: "/tmp/real.png",
    setup_org_entry_url:
      "/dashboard/observe?setup=true&source=onboarding&tour_anchor=observe_create_project_button&journey_step=connect_observability&quick_start_goal=monitor_production_ai_app&quick_start_id=observe&quick_start_primary_path=observe",
    setup_org_home_url:
      "/dashboard/home?source=setup_org&quick_start_goal=monitor_production_ai_app&quick_start_id=observe&quick_start_primary_path=observe",
    sample_project_post: null,
    sample_trace_activation_event: null,
    sample_to_real_setup_event: null,
    setup_quick_start: "observe",
    signup_post: { email: "new@example.com", password: "[redacted]" },
    token_post: { email: "new@example.com", password: "[redacted]" },
  };

  const base = {
    schema_version: REPORT_SCHEMA,
    source: "onboarding_real_signup_smoke",
    generated_at: "2026-05-30T00:00:00.000Z",
    status: "passed",
    mode: child.mode,
    app_base: "http://127.0.0.1:3035",
    api_base: "http://127.0.0.1:8011",
    report_output: join(dir, `${child.id}.json`),
    viewport: { name: child.viewport, width: 1440, height: 950 },
    evidence: realEvidence,
  };

  return {
    ...base,
    ...overrides,
    evidence: {
      ...base.evidence,
      ...(overrides.evidence || {}),
    },
  };
}

async function writeProofPack(overrides = {}) {
  const dir = await mkdtemp(join(tmpdir(), "onboarding-proof-pack-"));
  const children = [];
  for (const child of EXPECTED_CHILDREN) {
    const report = reportFor(child, dir, overrides.reports?.[child.id] || {});
    await writeJson(join(dir, `${child.id}.json`), report);
    children.push({
      id: child.id,
      mode: report.mode,
      report_path: join(dir, `${child.id}.json`),
      report_status: report.status,
      runner_status: report.status,
      viewport: report.viewport.name,
      error_message: null,
    });
  }
  const manifest = {
    schema_version: MANIFEST_SCHEMA,
    source: "onboarding_real_signup_proof_pack",
    generated_at: "2026-05-30T00:00:00.000Z",
    suite_id: "signup-real-proof-pack",
    status: "passed",
    report_output_dir: dir,
    children,
    ...(overrides.manifest || {}),
  };
  const manifestPath = join(dir, "manifest.json");
  await writeJson(manifestPath, manifest);
  return { dir, manifestPath };
}

test("proof pack validator accepts a complete desktop and mobile first-value pack", async () => {
  const { manifestPath } = await writeProofPack();

  const result = await validateProofPack(manifestPath);

  assert.equal(result.status, "passed");
  assert.equal(result.failed_checks.length, 0);
  assert.equal(result.children.length, 4);
  assert.equal(
    result.launch_metrics.schema_version,
    "onboarding-real-signup-proof-pack-launch-metrics-2026-05-31.v1",
  );
  assert.equal(result.launch_metrics.ui_first_value.real_proof_count, 2);
  assert.equal(
    result.launch_metrics.ui_first_value.setup_direct_handoff_count,
    2,
  );
  assert.equal(result.launch_metrics.ui_first_value.setup_home_stop_count, 2);
  assert.equal(
    result.launch_metrics.ui_first_value.backend_loop_completed_count,
    2,
  );
  assert.equal(result.launch_metrics.ui_first_value.ui_first_value_count, 2);
  assert.equal(
    result.launch_metrics.ui_first_value.backend_to_ui_first_value_rate,
    1,
  );
  assert.equal(
    result.launch_metrics.ui_first_value.daily_quality_available_count,
    2,
  );
  assert.deepEqual(result.launch_metrics.ui_first_value.quick_start_ids, [
    "observe",
  ]);
  assert.deepEqual(
    result.launch_metrics.ui_first_value.activation_event_names,
    ["first_quality_loop_completed"],
  );
  assert.deepEqual(
    result.launch_metrics.ui_first_value.activation_event_paths,
    ["evals"],
  );
  assert.equal(result.launch_metrics.sample.sample_proof_count, 2);
  assert.equal(result.launch_metrics.sample.zero_click_count, 0);
  assert.equal(result.launch_metrics.sample.activated_count, 0);
  assert.equal(result.launch_metrics.guardrails.failed_check_count, 0);
});

test("proof pack validator rejects real signup without setup-org Home attribution", async () => {
  const { manifestPath } = await writeProofPack({
    reports: {
      "signup-quick-start-real": {
        evidence: {
          setup_org_home_url: null,
        },
      },
    },
  });

  const result = await validateProofPack(manifestPath);

  assert.equal(result.status, "failed");
  assert.equal(
    result.launch_metrics.ui_first_value.setup_direct_handoff_count,
    2,
  );
  assert.equal(result.launch_metrics.ui_first_value.setup_home_stop_count, 1);
  assert(
    result.failed_checks.some(
      (check) =>
        check.key ===
        "signup-quick-start-real:real_loop:setup_home_attribution",
    ),
  );
});

test("proof pack validator rejects a failed child report", async () => {
  const { manifestPath } = await writeProofPack({
    manifest: { status: "failed" },
    reports: {
      "signup-quick-start-real": {
        diagnostic: { error_message: "API failed" },
        status: "failed",
      },
    },
  });

  const result = await validateProofPack(manifestPath);

  assert.equal(result.status, "failed");
  assert(result.failed_checks.some((check) => check.key === "manifest:status"));
  assert(
    result.failed_checks.some(
      (check) => check.key === "signup-quick-start-real:report:status",
    ),
  );
});

test("proof pack validator rejects missing real quality-loop completion", async () => {
  const { manifestPath } = await writeProofPack({
    reports: {
      "signup-quick-start-mobile-real": {
        evidence: {
          eval_first_quality_loop_completed_event: null,
        },
      },
    },
  });

  const result = await validateProofPack(manifestPath);

  assert.equal(result.status, "failed");
  assert(
    result.failed_checks.some(
      (check) =>
        check.key === "signup-quick-start-mobile-real:real_loop:completion",
    ),
  );
});

test("proof pack validator rejects missing frontend first-value PostHog marker", async () => {
  const { manifestPath } = await writeProofPack({
    reports: {
      "signup-quick-start-real": {
        evidence: {
          first_value_posthog_event: {
            event: "onboarding_first_value_reached",
            properties: {
              source: "onboarding",
              quick_start_id: "sample_preview",
              activation_event_name: "first_quality_loop_completed",
              daily_quality_available: true,
              is_sample: false,
            },
          },
        },
      },
    },
  });

  const result = await validateProofPack(manifestPath);

  assert.equal(result.status, "failed");
  assert.equal(result.launch_metrics.ui_first_value.ui_first_value_count, 1);
  assert.equal(
    result.launch_metrics.ui_first_value.backend_loop_completed_count,
    2,
  );
  assert.equal(
    result.launch_metrics.ui_first_value.backend_to_ui_first_value_rate,
    0.5,
  );
  assert.equal(
    result.launch_metrics.guardrails.missing_ui_first_value_count,
    1,
  );
  assert(
    result.failed_checks.some(
      (check) =>
        check.key === "signup-quick-start-real:real_loop:first_value_posthog",
    ),
  );
});

test("proof pack validator rejects sample guard that opens a sample project", async () => {
  const { manifestPath } = await writeProofPack({
    reports: {
      "signup-sample-open-real": {
        evidence: {
          sample_project_post: {
            source: "setup_org",
            reason: "sample_preview",
          },
        },
      },
    },
  });

  const result = await validateProofPack(manifestPath);

  assert.equal(result.status, "failed");
  assert(
    result.failed_checks.some(
      (check) =>
        check.key ===
        "signup-sample-open-real:sample_guard:no_sample_project_post",
    ),
  );
});

test("proof pack validator rejects sample guard that records sample trace review", async () => {
  const { manifestPath } = await writeProofPack({
    reports: {
      "signup-sample-open-real": {
        evidence: {
          sample_trace_activation_event: {
            event_name: "sample_trace_detail_opened",
            is_sample: true,
          },
        },
      },
    },
  });

  const result = await validateProofPack(manifestPath);

  assert.equal(result.status, "failed");
  assert(
    result.failed_checks.some(
      (check) =>
        check.key ===
        "signup-sample-open-real:sample_guard:no_sample_trace_event",
    ),
  );
});

test("proof pack validator rejects sample guard without setup-org attribution", async () => {
  const { manifestPath } = await writeProofPack({
    reports: {
      "signup-sample-open-real": {
        evidence: {
          setup_org_home_url: "/dashboard/home?source=setup_org",
        },
      },
    },
  });

  const result = await validateProofPack(manifestPath);

  assert.equal(result.status, "failed");
  assert(
    result.failed_checks.some(
      (check) =>
        check.key ===
        "signup-sample-open-real:sample_guard:setup_home_attribution",
    ),
  );
});
