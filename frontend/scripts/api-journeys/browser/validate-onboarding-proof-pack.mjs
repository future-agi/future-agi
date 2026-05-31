/* eslint-disable no-console */
import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import process from "node:process";

const MANIFEST_SCHEMA =
  "onboarding-real-signup-proof-pack-manifest-2026-05-30.v1";
const REPORT_SCHEMA = "onboarding-real-signup-smoke-report-2026-05-29.v1";
const VALIDATION_SCHEMA =
  "onboarding-real-signup-proof-pack-validation-2026-05-30.v1";
const LAUNCH_METRICS_SCHEMA =
  "onboarding-real-signup-proof-pack-launch-metrics-2026-05-31.v1";
const SAMPLE_QUICK_START_ATTRIBUTION = Object.freeze({
  quick_start_goal: "explore_sample_data",
  quick_start_id: "sample_preview",
  quick_start_primary_path: "sample",
});
const OBSERVE_QUICK_START_ATTRIBUTION = Object.freeze({
  quick_start_goal: "monitor_production_ai_app",
  quick_start_id: "observe",
  quick_start_primary_path: "observe",
});
const OBSERVE_DIRECT_HANDOFF_PARAMS = Object.freeze({
  setup: "true",
  source: "onboarding",
  tour_anchor: "observe_create_project_button",
  journey_step: "connect_observability",
  ...OBSERVE_QUICK_START_ATTRIBUTION,
});
const QUICK_START_QUERY_KEYS = Object.freeze(
  Object.keys(SAMPLE_QUICK_START_ATTRIBUTION),
);

const EXPECTED_CHILDREN = [
  {
    id: "signup-sample-open-real",
    mode: "sample_open",
    viewport: "desktop",
    proof: "sample",
  },
  {
    id: "signup-sample-open-mobile-real",
    mode: "sample_open",
    viewport: "mobile",
    proof: "sample",
  },
  {
    id: "signup-quick-start-real",
    mode: "full_quality_loop",
    viewport: "desktop",
    proof: "real_quality_loop",
  },
  {
    id: "signup-quick-start-mobile-real",
    mode: "full_quality_loop",
    viewport: "mobile",
    proof: "real_quality_loop",
  },
];
const REQUIRED_AHA_PROPERTIES = [
  "source",
  "quick_start_goal",
  "quick_start_id",
  "quick_start_primary_path",
  "primary_path",
  "activation_stage",
  "activation_event_name",
  "activation_event_path",
  "daily_quality_available",
  "is_sample",
];

function parseArgs(argv) {
  const args = {
    format: "text",
    manifest: "",
    output: "",
    reportOutputDir: "",
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--manifest") {
      args.manifest = argv[index + 1] || "";
      index += 1;
    } else if (arg === "--report-output-dir") {
      args.reportOutputDir = argv[index + 1] || "";
      index += 1;
    } else if (arg === "--output") {
      args.output = argv[index + 1] || "";
      index += 1;
    } else if (arg === "--format") {
      args.format = argv[index + 1] || "text";
      index += 1;
    } else if (arg === "--help" || arg === "-h") {
      args.help = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (args.help) return args;
  if (args.format !== "text" && args.format !== "json") {
    throw new Error("--format must be text or json");
  }
  if (!args.manifest && !args.reportOutputDir) {
    throw new Error(
      "Pass --manifest <path> or --report-output-dir <directory>.",
    );
  }
  if (args.manifest && args.reportOutputDir) {
    throw new Error("Pass only one of --manifest or --report-output-dir.");
  }

  return args;
}

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

function addCheck(checks, passed, key, detail, metadata = {}) {
  checks.push({
    key,
    passed,
    detail,
    ...metadata,
  });
}

function hasObject(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function isRedactedAuth(payload) {
  if (!hasObject(payload)) return false;
  return (
    payload.password === undefined ||
    payload.password === "" ||
    payload.password === "[redacted]"
  );
}

function safeUrl(value) {
  if (!value || typeof value !== "string") return null;
  try {
    return new URL(value, "https://futureagi.local");
  } catch {
    return null;
  }
}

function paramsObject(value) {
  if (!value) return {};
  if (typeof value === "string") {
    const url = safeUrl(value);
    if (url && (value.startsWith("/") || value.includes("?"))) {
      return Object.fromEntries(url.searchParams);
    }
    try {
      return JSON.parse(value);
    } catch {
      return {};
    }
  }
  if (value instanceof URLSearchParams) {
    return Object.fromEntries(value);
  }
  if (typeof value === "object") {
    return value;
  }
  return {};
}

function hasSampleQuickStartAttribution(value) {
  const params = paramsObject(value);
  return Object.entries(SAMPLE_QUICK_START_ATTRIBUTION).every(
    ([key, expected]) => params?.[key] === expected,
  );
}

function hasExpectedParams(value, expected) {
  const params = paramsObject(value);
  return Object.entries(expected).every(
    ([key, expectedValue]) => params?.[key] === expectedValue,
  );
}

function isObserveDirectHandoffUrl(value) {
  const url = safeUrl(value);
  return (
    Boolean(url) &&
    url.pathname === "/dashboard/observe" &&
    hasExpectedParams(value, OBSERVE_DIRECT_HANDOFF_PARAMS)
  );
}

function isSetupOrgHomeUrl(value) {
  const url = safeUrl(value);
  return (
    Boolean(url) &&
    url.pathname === "/dashboard/home" &&
    url.searchParams.get("source") === "setup_org"
  );
}

function sameSampleTraceRoute(responseEntryRoute, browserRoute) {
  const responseUrl = safeUrl(responseEntryRoute);
  const browserUrl = safeUrl(browserRoute);
  if (!responseUrl || !browserUrl) return false;
  QUICK_START_QUERY_KEYS.forEach((key) => {
    browserUrl.searchParams.delete(key);
  });

  return (
    responseUrl.pathname === browserUrl.pathname &&
    responseUrl.searchParams.get("sample") === "true" &&
    browserUrl.searchParams.get("sample") === "true" &&
    (responseUrl.searchParams.get("from") || "") ===
      (browserUrl.searchParams.get("from") || "")
  );
}

function childById(manifest, id) {
  return (manifest.children || []).find((child) => child?.id === id);
}

function expectedReportPath(manifest, childId) {
  return resolve(manifest.report_output_dir, `${childId}.json`);
}

function addEvidenceFieldChecks(checks, childId, evidence, fields) {
  for (const field of fields) {
    addCheck(
      checks,
      evidence[field] !== undefined && evidence[field] !== null,
      `${childId}:evidence:${field}`,
      `Evidence field ${field} is present.`,
    );
  }
}

function uniqueSorted(values) {
  return [
    ...new Set(values.filter((value) => value !== undefined && value !== null)),
  ]
    .map(String)
    .sort();
}

function ratio(numerator, denominator) {
  if (!denominator) return null;
  return Number((numerator / denominator).toFixed(4));
}

function missingProperties(value, keys) {
  const properties = value?.properties || {};
  return keys.filter(
    (key) =>
      properties[key] === undefined ||
      properties[key] === null ||
      properties[key] === "",
  );
}

function expectedDailyQualityAvailable(evidence = {}) {
  return evidence.expected_daily_quality_available !== false;
}

function isAhaMomentContractValid(event, { dailyQualityAvailable } = {}) {
  const properties = event?.properties || {};
  return (
    event?.event === "onboarding_aha_moment_reached" &&
    properties.source === "onboarding" &&
    properties.quick_start_goal === "monitor_production_ai_app" &&
    properties.quick_start_id === "observe" &&
    properties.quick_start_primary_path === "observe" &&
    properties.primary_path === "observe" &&
    properties.activation_stage === "activated" &&
    properties.activation_event_name === "first_quality_loop_completed" &&
    properties.activation_event_path === "evals" &&
    properties.daily_quality_available === dailyQualityAvailable &&
    properties.is_sample !== true
  );
}

function sampleProjectContract(evidence = {}) {
  const responseResult = evidence.sample_project_response?.result;
  if (hasObject(responseResult)) {
    return {
      activation_state: responseResult.activation_state,
      sample_project: responseResult.sample_project,
    };
  }

  return {
    activation_state: evidence.sample_open_state,
    sample_project: evidence.sample_open_state?.sample_project,
  };
}

function launchMetricReportEntry(child, expected, report) {
  const evidence = report?.evidence || {};
  const ahaProperties = evidence.aha_moment_posthog_event?.properties || {};
  const uiAhaContractValid = isAhaMomentContractValid(
    evidence.aha_moment_posthog_event,
    {
      dailyQualityAvailable: expectedDailyQualityAvailable(evidence),
    },
  );
  return {
    id: expected.id,
    mode: expected.mode,
    proof: expected.proof,
    report_status: report?.status || null,
    runner_status: child?.runner_status || null,
    viewport: report?.viewport?.name || expected.viewport,
    setup_quick_start: evidence.setup_quick_start || null,
    sample_is_activated: Boolean(
      evidence.sample_open_state?.is_activated ||
        evidence.sample_project_response?.result?.activation_state
          ?.is_activated,
    ),
    sample_zero_click_entry:
      evidence.sample_trace_entry?.clicks_after_quick_start === 0,
    setup_direct_handoff: isObserveDirectHandoffUrl(
      evidence.setup_org_entry_url,
    ),
    setup_home_stop: isSetupOrgHomeUrl(evidence.setup_org_home_url),
    backend_loop_completed:
      evidence.eval_first_quality_loop_completed_event?.event_name ===
        "first_quality_loop_completed" &&
      evidence.eval_first_quality_loop_completed_event?.is_sample !== true,
    ui_aha_event:
      evidence.aha_moment_posthog_event?.event ===
      "onboarding_aha_moment_reached",
    ui_aha_contract_valid: uiAhaContractValid,
    ui_aha_required_properties_missing: missingProperties(
      evidence.aha_moment_posthog_event,
      REQUIRED_AHA_PROPERTIES,
    ),
    ui_aha_daily_quality_available:
      ahaProperties.daily_quality_available === true,
    ui_aha_is_sample: ahaProperties.is_sample === true,
    ui_aha_quick_start_id: ahaProperties.quick_start_id || null,
    ui_aha_quick_start_primary_path:
      ahaProperties.quick_start_primary_path || null,
    ui_aha_activation_event_name: ahaProperties.activation_event_name || null,
    ui_aha_activation_event_path: ahaProperties.activation_event_path || null,
  };
}

function buildLaunchMetrics(entries, checks) {
  const realEntries = entries.filter(
    (entry) => entry.proof === "real_quality_loop",
  );
  const sampleEntries = entries.filter((entry) => entry.proof === "sample");
  const backendLoopCount = realEntries.filter(
    (entry) => entry.backend_loop_completed,
  ).length;
  const uiAhaEntries = realEntries.filter(
    (entry) => entry.ui_aha_contract_valid,
  );
  const uiAhaRequiredPropertyFailures = realEntries.filter(
    (entry) =>
      entry.ui_aha_event && entry.ui_aha_required_properties_missing.length > 0,
  );
  const failedChecks = checks.filter((check) => !check.passed);

  return {
    schema_version: LAUNCH_METRICS_SCHEMA,
    dashboard_contract: "signup_to_aha_posthog_checks",
    children: {
      expected_count: EXPECTED_CHILDREN.length,
      observed_count: entries.length,
      passed_count: entries.filter(
        (entry) =>
          entry.report_status === "passed" && entry.runner_status === "passed",
      ).length,
    },
    ui_aha: {
      real_proof_count: realEntries.length,
      setup_direct_handoff_count: realEntries.filter(
        (entry) => entry.setup_direct_handoff,
      ).length,
      setup_home_stop_count: realEntries.filter(
        (entry) => entry.setup_home_stop,
      ).length,
      backend_loop_completed_count: backendLoopCount,
      ui_aha_count: uiAhaEntries.length,
      backend_to_ui_aha_rate: ratio(uiAhaEntries.length, backendLoopCount),
      daily_quality_available_count: uiAhaEntries.filter(
        (entry) => entry.ui_aha_daily_quality_available,
      ).length,
      required_property_failure_count: uiAhaRequiredPropertyFailures.length,
      quick_start_ids: uniqueSorted(
        uiAhaEntries.map((entry) => entry.ui_aha_quick_start_id),
      ),
      quick_start_primary_paths: uniqueSorted(
        uiAhaEntries.map((entry) => entry.ui_aha_quick_start_primary_path),
      ),
      activation_event_names: uniqueSorted(
        uiAhaEntries.map((entry) => entry.ui_aha_activation_event_name),
      ),
      activation_event_paths: uniqueSorted(
        uiAhaEntries.map((entry) => entry.ui_aha_activation_event_path),
      ),
    },
    sample: {
      sample_proof_count: sampleEntries.length,
      zero_click_count: sampleEntries.filter(
        (entry) => entry.sample_zero_click_entry,
      ).length,
      activated_count: sampleEntries.filter(
        (entry) => entry.sample_is_activated,
      ).length,
    },
    guardrails: {
      failed_check_count: failedChecks.length,
      failed_check_keys: failedChecks.map((check) => check.key),
      sample_activation_count: sampleEntries.filter(
        (entry) => entry.sample_is_activated,
      ).length,
      missing_ui_aha_count: Math.max(backendLoopCount - uiAhaEntries.length, 0),
    },
  };
}

function validateSampleEvidence(checks, childId, report) {
  const evidence = report.evidence || {};
  addEvidenceFieldChecks(checks, childId, evidence, [
    "browser_state",
    "sample_open_state",
    "sample_project_post",
    "sample_project_response",
    "sample_trace_activation_event",
    "sample_trace_entry",
    "sample_trace_url",
    "screenshot",
    "signup_post",
    "token_post",
  ]);
  addCheck(
    checks,
    evidence.setup_quick_start === "sample_preview",
    `${childId}:sample:quick_start`,
    "Sample proof used the sample preview quick start.",
  );
  addCheck(
    checks,
    !evidence.onboarding_post,
    `${childId}:sample:no_setup_completion`,
    "Sample proof does not save profile onboarding or complete setup.",
  );
  addCheck(
    checks,
    evidence.sample_trace_entry?.clicks_after_quick_start === 0 &&
      evidence.sample_trace_entry?.quick_start_goal ===
        SAMPLE_QUICK_START_ATTRIBUTION.quick_start_goal &&
      evidence.sample_trace_entry?.quick_start_id === "sample_preview" &&
      evidence.sample_trace_entry?.quick_start_primary_path ===
        SAMPLE_QUICK_START_ATTRIBUTION.quick_start_primary_path &&
      evidence.sample_trace_entry?.source === "setup_org",
    `${childId}:sample:zero_click_entry`,
    "Sample proof opened the sample trace directly from setup quick start.",
  );
  addCheck(
    checks,
    hasSampleQuickStartAttribution(evidence.sample_trace_url),
    `${childId}:sample:route_attribution`,
    "Sample proof keeps quick-start attribution on the browser trace route.",
  );
  addCheck(
    checks,
    hasSampleQuickStartAttribution(evidence.sample_project_post),
    `${childId}:sample:sample_project_post_attribution`,
    "Sample proof sends quick-start attribution when opening the sample project.",
  );
  addCheck(
    checks,
    evidence.sample_trace_activation_event?.event_name ===
      "sample_trace_detail_opened" &&
      evidence.sample_trace_activation_event?.is_sample === true &&
      hasSampleQuickStartAttribution(
        evidence.sample_trace_activation_event?.metadata,
      ),
    `${childId}:sample:event`,
    "Sample proof records sample trace detail as attributed sample-only evidence.",
  );
  addCheck(
    checks,
    !evidence.sample_open_state?.signals?.first_observe_id &&
      !evidence.sample_open_state?.signals?.first_trace_id,
    `${childId}:sample:not_real_signal`,
    "Sample proof does not expose first real observe or trace identifiers.",
  );
  const sampleContract = sampleProjectContract(evidence);
  addCheck(
    checks,
    sampleContract.sample_project?.created === true &&
      sameSampleTraceRoute(
        sampleContract.sample_project?.entry_route,
        evidence.sample_trace_url,
      ) &&
      sampleContract.activation_state?.is_activated === false,
    `${childId}:sample:response_contract`,
    "Sample proof created a sample project without activating the workspace.",
  );
}

function validateRealQualityLoopEvidence(checks, childId, report) {
  const evidence = report.evidence || {};
  addEvidenceFieldChecks(checks, childId, evidence, [
    "aha_moment_posthog_event",
    "browser_state",
    "eval_first_quality_loop_completed_event",
    "eval_fix_rerun_completed_event",
    "eval_fix_rerun_reviewed_event",
    "eval_result_reviewed_event",
    "eval_run_completed_event",
    "eval_source_fix_cta_event",
    "eval_source_fix_route_event",
    "onboarding_post",
    "post_review_state",
    "real_observe_project",
    "real_trace",
    "real_trace_review_event",
    "real_trace_review_state",
    "screenshot",
    "setup_org_entry_url",
    "signup_post",
    "token_post",
  ]);
  const shouldHaveDailyQuality = expectedDailyQualityAvailable(evidence);
  addCheck(
    checks,
    shouldHaveDailyQuality
      ? evidence.daily_quality_cta_href !== undefined &&
          evidence.daily_quality_cta_href !== null
      : evidence.daily_quality_cta_href === undefined ||
          evidence.daily_quality_cta_href === null,
    `${childId}:evidence:daily_quality_cta_href`,
    shouldHaveDailyQuality
      ? "Evidence field daily_quality_cta_href is present."
      : "Evidence field daily_quality_cta_href is absent when daily quality is unavailable.",
  );
  addCheck(
    checks,
    typeof evidence.expected_daily_quality_available === "boolean",
    `${childId}:evidence:expected_daily_quality_available`,
    "Evidence field expected_daily_quality_available is explicit.",
  );
  addCheck(
    checks,
    evidence.setup_quick_start === "observe",
    `${childId}:real_loop:quick_start`,
    "Real proof used the observe quick start.",
  );
  addCheck(
    checks,
    isObserveDirectHandoffUrl(evidence.setup_org_entry_url),
    `${childId}:real_loop:setup_direct_handoff`,
    "Real proof enters Observe setup directly from setup quick start.",
  );
  addCheck(
    checks,
    !isSetupOrgHomeUrl(evidence.setup_org_home_url),
    `${childId}:real_loop:no_setup_home_stop`,
    "Real proof does not accept the old setup-org Home stop before first action.",
  );
  addCheck(
    checks,
    evidence.real_trace_review_event?.event_name === "trace_detail_opened" &&
      evidence.real_trace_review_event?.is_sample !== true,
    `${childId}:real_loop:trace_review`,
    "Real proof records a non-sample trace review event.",
  );
  addCheck(
    checks,
    evidence.eval_first_quality_loop_completed_event?.event_name ===
      "first_quality_loop_completed" &&
      evidence.eval_first_quality_loop_completed_event?.is_sample !== true,
    `${childId}:real_loop:completion`,
    "Real proof records non-sample first quality loop completion.",
  );
  addCheck(
    checks,
    isAhaMomentContractValid(evidence.aha_moment_posthog_event, {
      dailyQualityAvailable: shouldHaveDailyQuality,
    }),
    `${childId}:real_loop:aha_posthog`,
    "Real proof captures the frontend Aha PostHog marker with observe quick-start attribution.",
  );
  addCheck(
    checks,
    Boolean(
      evidence.real_trace?.traceId && evidence.real_observe_project?.projectId,
    ),
    `${childId}:real_loop:ids`,
    "Real proof includes trace and project identifiers.",
  );
}

function validateAuthRedaction(checks, childId, report) {
  const evidence = report.evidence || {};
  addCheck(
    checks,
    isRedactedAuth(evidence.signup_post),
    `${childId}:auth_redacted:signup`,
    "Signup request evidence has no raw password.",
  );
  addCheck(
    checks,
    isRedactedAuth(evidence.token_post),
    `${childId}:auth_redacted:token`,
    "Token request evidence has no raw password.",
  );
}

function validateReport(checks, manifest, child, expected, report) {
  addCheck(
    checks,
    report.schema_version === REPORT_SCHEMA,
    `${expected.id}:report:schema`,
    "Child report schema is current.",
  );
  addCheck(
    checks,
    report.source === "onboarding_real_signup_smoke",
    `${expected.id}:report:source`,
    "Child report source is correct.",
  );
  addCheck(
    checks,
    report.status === "passed",
    `${expected.id}:report:status`,
    "Child report status passed.",
  );
  addCheck(
    checks,
    report.mode === expected.mode,
    `${expected.id}:report:mode`,
    `Child report mode is ${expected.mode}.`,
  );
  addCheck(
    checks,
    report.viewport?.name === expected.viewport,
    `${expected.id}:report:viewport`,
    `Child report viewport is ${expected.viewport}.`,
  );
  addCheck(
    checks,
    resolve(report.report_output || "") ===
      expectedReportPath(manifest, expected.id),
    `${expected.id}:report:path`,
    "Child report path matches manifest output directory.",
  );
  addCheck(
    checks,
    hasObject(report.evidence),
    `${expected.id}:report:evidence`,
    "Child report contains structured evidence.",
  );
  addCheck(
    checks,
    !report.diagnostic,
    `${expected.id}:report:no_diagnostic`,
    "Passed child report has no failure diagnostic.",
  );
  addCheck(
    checks,
    child.mode === expected.mode &&
      child.report_status === "passed" &&
      child.runner_status === "passed" &&
      child.viewport === expected.viewport &&
      !child.error_message,
    `${expected.id}:manifest:child_summary`,
    "Manifest child summary agrees with the child report.",
  );

  validateAuthRedaction(checks, expected.id, report);
  if (expected.proof === "sample") {
    validateSampleEvidence(checks, expected.id, report);
  } else {
    validateRealQualityLoopEvidence(checks, expected.id, report);
  }
}

async function validateProofPack(manifestPath) {
  const resolvedManifestPath = resolve(manifestPath);
  const manifest = await readJson(resolvedManifestPath);
  const checks = [];
  const children = [];
  const launchMetricEntries = [];

  addCheck(
    checks,
    manifest.schema_version === MANIFEST_SCHEMA,
    "manifest:schema",
    "Manifest schema is current.",
  );
  addCheck(
    checks,
    manifest.source === "onboarding_real_signup_proof_pack",
    "manifest:source",
    "Manifest source is correct.",
  );
  addCheck(
    checks,
    manifest.suite_id === "signup-real-proof-pack",
    "manifest:suite",
    "Manifest suite id is signup-real-proof-pack.",
  );
  addCheck(
    checks,
    manifest.status === "passed",
    "manifest:status",
    "Manifest status passed.",
  );
  addCheck(
    checks,
    Array.isArray(manifest.children),
    "manifest:children_array",
    "Manifest contains child entries.",
  );
  addCheck(
    checks,
    typeof manifest.report_output_dir === "string" &&
      manifest.report_output_dir.length > 0,
    "manifest:report_output_dir",
    "Manifest records report output directory.",
  );

  const actualIds = new Set(
    (manifest.children || []).map((child) => child?.id),
  );
  const expectedIds = new Set(EXPECTED_CHILDREN.map((child) => child.id));
  addCheck(
    checks,
    (manifest.children || []).length === EXPECTED_CHILDREN.length &&
      actualIds.size === expectedIds.size &&
      [...expectedIds].every((id) => actualIds.has(id)),
    "manifest:expected_children",
    "Manifest contains exactly the four required proof targets.",
    {
      actual_ids: [...actualIds].sort(),
      expected_ids: [...expectedIds].sort(),
    },
  );

  addCheck(
    checks,
    actualIds.size === expectedIds.size &&
      [...expectedIds].every((id) => actualIds.has(id)),
    "manifest:required_child_ids",
    "Manifest includes every required child id.",
    {
      actual_ids: [...actualIds].sort(),
      expected_ids: [...expectedIds].sort(),
    },
  );

  for (const expected of EXPECTED_CHILDREN) {
    const child = childById(manifest, expected.id);
    if (!child) {
      addCheck(
        checks,
        false,
        `${expected.id}:manifest:present`,
        "Required child entry exists.",
      );
      continue;
    }

    const expectedPath = expectedReportPath(manifest, expected.id);
    addCheck(
      checks,
      resolve(child.report_path || "") === expectedPath,
      `${expected.id}:manifest:report_path`,
      "Manifest child report path is deterministic.",
    );

    let report = null;
    try {
      report = await readJson(expectedPath);
    } catch (error) {
      addCheck(
        checks,
        false,
        `${expected.id}:report:read`,
        `Child report can be read: ${error.message}`,
      );
    }

    if (report) {
      validateReport(checks, manifest, child, expected, report);
      launchMetricEntries.push(
        launchMetricReportEntry(child, expected, report),
      );
      children.push({
        id: expected.id,
        mode: report.mode,
        report_path: expectedPath,
        report_status: report.status,
        runner_status: child.runner_status,
        viewport: report.viewport?.name || null,
      });
    }
  }

  const failedChecks = checks.filter((check) => !check.passed);
  const launchMetrics = buildLaunchMetrics(launchMetricEntries, checks);
  return {
    schema_version: VALIDATION_SCHEMA,
    source: "onboarding_real_signup_proof_pack_validation",
    generated_at: new Date().toISOString(),
    manifest_path: resolvedManifestPath,
    report_output_dir:
      manifest.report_output_dir || dirname(resolvedManifestPath),
    status: failedChecks.length ? "failed" : "passed",
    children,
    launch_metrics: launchMetrics,
    failed_checks: failedChecks.map((check) => ({
      key: check.key,
      detail: check.detail,
    })),
    checks,
  };
}

function textOutput(result) {
  const lines = [
    "onboarding real-signup proof pack validation",
    `status=${result.status}`,
    `manifest_path=${result.manifest_path}`,
    `report_output_dir=${result.report_output_dir}`,
    `children=${result.children.length}`,
    `ui_aha=${result.launch_metrics.ui_aha.ui_aha_count}/${result.launch_metrics.ui_aha.backend_loop_completed_count}`,
    `backend_to_ui_aha_rate=${result.launch_metrics.ui_aha.backend_to_ui_aha_rate}`,
    `sample_activated=${result.launch_metrics.sample.activated_count}`,
    `failed_checks=${result.failed_checks.length}`,
  ];
  for (const failed of result.failed_checks) {
    lines.push(`failed=${failed.key}: ${failed.detail}`);
  }
  return lines.join("\n");
}

function usage() {
  return [
    "Usage:",
    "  yarn --cwd frontend test:onboarding-smoke:validate-proof-pack --report-output-dir <directory>",
    "  node frontend/scripts/api-journeys/browser/validate-onboarding-proof-pack.mjs --report-output-dir <directory>",
    "  node frontend/scripts/api-journeys/browser/validate-onboarding-proof-pack.mjs --manifest <manifest.json>",
    "",
    "Options:",
    "  --format text|json",
    "  --output <path>",
  ].join("\n");
}

async function runCli() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }
  const manifestPath =
    args.manifest || resolve(args.reportOutputDir, "manifest.json");
  const result = await validateProofPack(manifestPath);
  const output =
    args.format === "json"
      ? `${JSON.stringify(result, null, 2)}\n`
      : `${textOutput(result)}\n`;

  if (args.output) {
    await writeFile(args.output, output, "utf8");
  }
  process.stdout.write(output);

  if (result.status !== "passed") {
    process.exitCode = 1;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  runCli().catch((error) => {
    console.error(error?.message || String(error));
    process.exitCode = 1;
  });
}

export { EXPECTED_CHILDREN, validateProofPack };
