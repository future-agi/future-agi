import { http, HttpResponse } from "msw";
import { HOST_API } from "src/config-global";

// TH-7217 CLEANUP: delete with its handlers.js entry once the endpoint is real.
//
// Dev mock standing in for GET /api/setup-checks/ while it does not exist.
//
// Scenario is selectable so the states that a real backend cannot produce on
// demand are still demoable:
//   VITE_MSW_OSS_SCENARIO=booting      (default) two boot failures, then a
//                                      snapshot with a warning and a failure
//   VITE_MSW_OSS_SCENARIO=all-pass     everything green immediately
//   VITE_MSW_OSS_SCENARIO=unreachable  never comes up, exercises the backoff

const SCENARIO = import.meta.env.VITE_MSW_OSS_SCENARIO || "booting";

const BOOT_FAILURES = 2;
let attempts = 0;

const BASE_CHECKS = [
  { id: "env", label: "Environment configuration" },
  { id: "database", label: "Application database · Postgres" },
  { id: "cache", label: "Cache · Redis" },
  { id: "backend", label: "Backend server · Django" },
  { id: "worker", label: "Background jobs · Celery" },
  { id: "frontend", label: "Frontend build · Vite" },
  { id: "storage", label: "Object storage" },
];

// `ports` and `ssl` are the only mode-varying entries, mirroring the prototype.
// Both are unobservable from a real backend (a bound port cannot report a
// conflict; the cert normally sits on a proxy the app cannot see), so treat
// them as illustrative of the MECHANISM, not as a proposed check list.
const modeChecks = (mode) => {
  const live = mode !== "experiment";
  return [
    {
      id: "ports",
      label: "Network ports available",
      status: "warning",
      required: live,
      detail: "Some ports need elevated privileges",
    },
    {
      id: "ssl",
      label: "SSL/TLS certificate",
      status: live ? "failed" : "skipped",
      required: live,
      detail: live
        ? "Certificate not found — required for a live setup"
        : "Not required in experimentation mode",
    },
  ];
};

const buildChecks = (mode) => {
  const passing = BASE_CHECKS.map((c) => ({
    ...c,
    status: "passed",
    required: true,
    detail: "",
  }));
  if (SCENARIO === "all-pass") return passing;
  return [...passing, ...modeChecks(mode)];
};

export const setupChecks = http.get(
  `${HOST_API}/api/setup-checks/`,
  async ({ request }) => {
    const mode = new URL(request.url).searchParams.get("mode") || "live";

    attempts += 1;

    if (
      SCENARIO === "unreachable" ||
      (SCENARIO === "booting" && attempts <= BOOT_FAILURES)
    ) {
      return new HttpResponse(null, { status: 503 });
    }

    // Real probes are not instant; a little latency keeps the reveal honest.
    await new Promise((resolve) => setTimeout(resolve, 400));

    const checks = buildChecks(mode);
    const hasBlockingFailure = checks.some(
      (c) => c.required && c.status === "failed",
    );

    return HttpResponse.json({
      status: true,
      result: {
        status: hasBlockingFailure ? "issues" : "ok",
        mode,
        checks,
      },
    });
  },
);
