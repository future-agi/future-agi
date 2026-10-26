import PropTypes from "prop-types";
import { SHOW_INSIGHTS } from "../_mock/featureFlags";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useParams, useSearchParams } from "react-router-dom";
import {
  createTheme,
  ThemeProvider as MuiThemeProvider,
  useTheme,
} from "@mui/material/styles";
import {
  Box,
  Button,
  GlobalStyles,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { palette as themePalette } from "src/theme/palette";
import { shadows as themeShadows } from "src/theme/shadows";
import { customShadows as themeCustomShadows } from "src/theme/custom-shadows";
import { typography as themeTypography } from "src/theme/typography";
import { componentsOverrides } from "src/theme/overrides";
import { useSimStore, useEnvState } from "../store";
import { getEnvironment } from "../_mock/environments";
import { runSummaries } from "../_mock/comparison";
import { runReportOf } from "../_mock/runReport";
import { runPinsOf } from "../_mock/envModel";
import { envLabelOf } from "../_mock/envVersionKinds";
import { runIdentityOf } from "../_mock/runIdentity";
import { isShareTokenFor, shareTokenOf, sharedUrlOf } from "../_mock/runShare";
import { getIntegrations } from "../_mock/integrations";
import {
  SectionCard,
  StatusDot,
  EmptyState,
  Verdict,
} from "../components/primitives";
import { RunSummaryBlock } from "./RunSummaryChip";
import SideDrawer from "../components/SideDrawer";
import InsightsTab from "./InsightsTab";
import { TraceSheet } from "./TraceWaterfall";
import { deriveUseCaseLabel } from "./traceTableHelpers";
import { scenarioTitle } from "../_mock/scenarios";

const colHead = {
  typography: "s3",
  fontWeight: 600,
  color: "text.tertiary",
  textTransform: "none",
  letterSpacing: 0.4,
};

/**
 * The shareable report (PRD v6 · AC-20.9).
 *
 * What a link recipient sees: the run summary — pass rate and every
 * scenario's result (plus Insights' issues and evidence where Insights is
 * shown) — under a provenance header that names every version the run held
 * fixed. Read-only: no Fix, no re-run, no eval edits. Evidence links still
 * open: a claim lists its scenarios and a call opens its trace, here.
 *
 * Route-free: LiveRunView renders it when the run's address carries
 * `?shared=<token>`. The token is stamped from the run's provenance, so a link
 * opens only the run it was minted for.
 */
export default function SharedReport() {
  const { envId, runId } = useParams();
  const [params] = useSearchParams();
  const token = params.get("shared");
  const { state } = useSimStore();
  const { envState } = useEnvState(envId);
  const env =
    getEnvironment(envId) || state.myEnvironments.find((e) => e.id === envId);

  const summaries = useMemo(
    () => (env ? runSummaries(env, envState) : []),
    [env, envState],
  );
  const identity = summaries.find((r) => r.id === runId) || null;
  const report = useMemo(
    () =>
      identity
        ? runReportOf({
            env,
            envState,
            runId,
            summaries,
            goalOf: deriveUseCaseLabel,
            integrations: getIntegrations(),
          })
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [identity?.id, env, envState],
  );

  /* The drawer: a claim's scenarios, or one call's trace. Opened from the
     link itself too — `&trace=` / `&scenarios=` — so a PDF's links land. */
  const [view, setView] = useState(() => {
    if (params.get("trace")) return { kind: "trace", id: params.get("trace") };
    if (params.get("scenarios"))
      return {
        kind: "list",
        label: "Linked scenarios",
        ids: params.get("scenarios").split(","),
      };
    return null;
  });
  const [printing, setPrinting] = useState(false);

  if (!env || !identity) {
    if (!state.hydrated) return <Box sx={{ height: "100%", minHeight: 420 }} />;
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:link-broken-linear"
          title="This report isn't available"
          body="The run it points at no longer exists, or it can't be shared."
        />
      </Box>
    );
  }
  if (!isShareTokenFor(token, identity, env, envState)) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:link-broken-linear"
          title="This link doesn't match the run"
          body="Share links are stamped with the versions the run held fixed. Copy a fresh read-only link from the run."
        />
      </Box>
    );
  }

  const tasks = report.run.tasks;
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const hrefFor = ({ ids, trace }) =>
    sharedUrlOf(
      envId,
      runId,
      token,
      trace ? { trace } : { scenarios: (ids || []).join(",") },
    );

  return (
    <Box sx={{ height: "100%", minHeight: 0, overflow: "auto" }}>
      {/* The bar says what this page is — and is the only control on it. */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{
          px: 3,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          position: "sticky",
          top: 0,
          zIndex: 2,
          bgcolor: "background.default",
        }}
      >
        <Iconify
          icon="solar:eye-linear"
          width={16}
          sx={{ color: "text.tertiary" }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Read-only report
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
        >
          Shared link · nothing here can be changed
        </Typography>
        <Button
          variant="outlined"
          size="small"
          onClick={() => setPrinting(true)}
          startIcon={<Iconify icon="solar:printer-linear" width={16} />}
          sx={{ color: "text.primary", borderColor: "divider" }}
        >
          Download PDF
        </Button>
      </Stack>

      <ReportBody
        env={env}
        envState={envState}
        identity={identity}
        report={report}
        token={token}
        hrefFor={hrefFor}
        onFilter={(label, ids) => setView({ kind: "list", label, ids })}
        onOpenTrace={(t) => t && setView({ kind: "trace", id: t.id })}
      />

      <SideDrawer
        open={!!view}
        onClose={() => setView(null)}
        width={{ xs: "100%", md: view?.kind === "trace" ? 960 : 480 }}
      >
        {view?.kind === "trace" && byId.get(view.id) && (
          <TraceSheet
            task={byId.get(view.id)}
            env={env}
            envState={envState}
            onClose={() => setView(null)}
          />
        )}
        {view?.kind === "list" && (
          <ScenarioList
            label={view.label}
            tasks={view.ids.map((id) => byId.get(id)).filter(Boolean)}
            onClose={() => setView(null)}
            onOpen={(t) => setView({ kind: "trace", id: t.id })}
          />
        )}
      </SideDrawer>

      {printing && (
        <PrintPortal
          env={env}
          envState={envState}
          identity={identity}
          report={report}
          token={token}
          hrefFor={hrefFor}
          onDone={() => setPrinting(false)}
        />
      )}
    </Box>
  );
}

/* ── the report itself — on screen and in print ─────────────────────────── */

export function ReportBody({
  env,
  envState,
  identity,
  report,
  token,
  hrefFor,
  onFilter,
  onOpenTrace,
}) {
  const pins = runPinsOf(identity, env, envState);
  const named = runIdentityOf(identity, envState, env);
  const ranAt = identity.finishedAt || identity.startedAt;
  const total = report.run.tasks.length;
  const rows = [
    /* One environment version for the world, scenarios, sub-goals and
       evals the run held fixed (envSnapshot.js). */
    [
      "Environment",
      `${env.name} · ${named.env ? `environment ${named.env}` : envLabelOf(env, envState, pins.env) || "—"}`,
    ],
    ["Agent", pins.agent || "—"],
    ["Simulated caller", pins.simulator],
    /* What the tools did, in words — never the dial's fingerprint. */
    ["Tools", pins.toolEnvSummary || "—"],
    ...(pins.secrets
      ? [
          [
            "Secrets",
            `${pins.secrets} reference${pins.secrets === 1 ? "" : "s"}, as set when it ran`,
          ],
        ]
      : []),
    /* Sealed · Live · Prompt only, and who ran it. */
    [
      "Proof",
      report.summary?.proofKind
        ? [
            report.summary.proofKind.kind === "live"
              ? "Live · tools reached real systems"
              : report.summary.proofKind.label,
            report.summary.proofKind.tierLabel,
          ]
            .filter(Boolean)
            .join(" · ")
        : "—",
    ],
    [
      "Ran",
      ranAt
        ? new Date(ranAt).toLocaleString(undefined, {
            dateStyle: "medium",
            timeStyle: "short",
          })
        : "—",
    ],
    [
      "Scenarios",
      `${total}${identity.repeats > 1 ? ` × ${identity.repeats} repeats` : ""}`,
    ],
    ["Link", token],
  ];

  return (
    <Box sx={{ maxWidth: 1180, mx: "auto", px: { xs: 2, md: 3 }, py: 2.5 }}>
      <SectionCard
        title={`Run ${identity.ordinal} · ${named.label}`}
        subtitle={env.name}
        action={
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.5}
            sx={{
              px: 0.75,
              height: 22,
              borderRadius: "4px",
              border: "1px solid",
              borderColor: "divider",
              color: "text.tertiary",
            }}
          >
            <Iconify icon="solar:lock-keyhole-minimalistic-linear" width={12} />
            <Typography sx={{ typography: "s3", fontWeight: 600 }}>
              Read-only
            </Typography>
          </Stack>
        }
        sx={{ mb: 2, breakInside: "avoid" }}
      >
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "repeat(2, minmax(0, 1fr))",
              md: "repeat(5, minmax(0, 1fr))",
            },
            columnGap: 2.5,
            rowGap: 1.25,
            px: 2.5,
            py: 1.75,
          }}
        >
          {rows.map(([k, v]) => (
            <Box key={k} minWidth={0}>
              <Typography sx={colHead}>{k}</Typography>
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 500,
                  wordBreak: "break-word",
                  fontFamily: k === "Link" ? "monospace" : undefined,
                }}
              >
                {v}
              </Typography>
            </Box>
          ))}
        </Box>
        {/* §6.3-D: what the numbers do and don't cover, said once, up front. */}
        <Typography
          sx={{
            px: 2.5,
            py: 1.25,
            typography: "s3",
            color: "text.tertiary",
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {identity.partial
            ? `A re-run of ${total} scenarios — every number below covers only these. `
            : "The whole run, not filtered. "}
          Only passed and failed count toward rates.
          {report.summary?.stale?.line ? ` ${report.summary.stale.line}.` : ""}
          {report.summary?.pulled?.count
            ? ` ${report.summary.pulled.count} pulled for review after testing invalid twice.`
            : ""}
        </Typography>
      </SectionCard>

      {/* The results themselves — pass rate and every scenario's outcome —
          are in every report. Insights adds issues and evidence on top
          where it's shown. */}
      {!SHOW_INSIGHTS && (
        <RunSummaryBlock
          summary={report.summary}
          onFilter={onFilter}
          envId={env.id}
          readOnly
        >
          <ScenarioResults tasks={report.run.tasks} onOpen={onOpenTrace} />
        </RunSummaryBlock>
      )}
      {SHOW_INSIGHTS && (
        <InsightsTab
          env={env}
          envState={envState}
          tasks={report.run.tasks}
          run={identity}
          goalOf={deriveUseCaseLabel}
          insights={report.insights}
          summary={report.summary}
          readOnly
          hrefFor={hrefFor}
          onFilter={onFilter || (() => {})}
          onOpenTrace={onOpenTrace || (() => {})}
          onFix={() => {}}
          onOpenFix={() => {}}
        />
      )}

      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          textAlign: "center",
          pt: 1,
          pb: 2,
        }}
      >
        {`Future AGI · simulation report · ${token}`}
      </Typography>
    </Box>
  );
}

ReportBody.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  identity: PropTypes.object,
  report: PropTypes.object,
  token: PropTypes.string,
  hrefFor: PropTypes.func,
  onFilter: PropTypes.func,
  onOpenTrace: PropTypes.func,
};

/* Every scenario's outcome, failures first — each opens its trace on screen. */
const RESULT_ORDER = {
  failed: 0,
  error: 1,
  flaky: 2,
  inconclusive: 3,
  invalid: 4,
  unmeasured: 5,
  passed: 6,
};
function ScenarioResults({ tasks, onOpen = null }) {
  const rows = [...tasks].sort(
    (a, b) => (RESULT_ORDER[a.status] ?? 5) - (RESULT_ORDER[b.status] ?? 5),
  );
  if (!rows.length) return null;
  return (
    <Box sx={{ breakInside: "auto" }}>
      <Stack
        direction="row"
        spacing={2}
        sx={{ px: 2, py: 1, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Typography sx={{ ...colHead, flex: 1 }}>Scenario</Typography>
        <Typography sx={{ ...colHead, width: 140, flexShrink: 0 }}>
          Result
        </Typography>
      </Stack>
      {rows.map((t) => (
        <Stack
          key={t.id}
          direction="row"
          alignItems="center"
          spacing={2}
          {...(onOpen
            ? {
                role: "button",
                tabIndex: 0,
                onClick: () => onOpen(t),
                onKeyDown: (e) => {
                  if (e.key === "Enter") onOpen(t);
                },
              }
            : {})}
          sx={{
            px: 2,
            py: 0.875,
            borderBottom: "1px solid",
            borderColor: "divider",
            breakInside: "avoid",
            ...(onOpen && {
              cursor: "pointer",
              "&:hover": { bgcolor: "action.hover" },
            }),
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography noWrap sx={{ typography: "s2", fontWeight: 500 }}>
              {scenarioTitle(t.title) || t.name || t.id}
            </Typography>
            {(t.persona?.name || t.critical || t.regressionMemory) && (
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {[
                  t.persona?.name,
                  t.critical || t.regressionMemory ? "must-pass" : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            )}
          </Box>
          <Box sx={{ width: 140, flexShrink: 0 }}>
            {/* Anything that isn't a pass, fail or flaky verdict reads "Not measured". */}
            <Verdict
              status={
                ["passed", "failed", "flaky"].includes(t.status)
                  ? t.status
                  : "unmeasured"
              }
              passes={t.passes}
              repeats={t.repeats}
            />
          </Box>
        </Stack>
      ))}
    </Box>
  );
}
ScenarioResults.propTypes = { tasks: PropTypes.array, onOpen: PropTypes.func };

/* A claim's scenarios, listed — each opens its trace. */
function ScenarioList({ label, tasks, onClose, onOpen }) {
  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2.5,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s1", fontWeight: 600 }}>
            {label}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary" }}
          >{`${tasks.length} ${tasks.length === 1 ? "scenario" : "scenarios"} · open one for its trace`}</Typography>
        </Box>
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="solar:close-linear" width={20} />
        </IconButton>
      </Stack>
      <Box sx={{ flex: 1, overflow: "auto" }}>
        {tasks.map((t) => (
          <Stack
            key={t.id}
            direction="row"
            alignItems="center"
            spacing={1}
            role="button"
            tabIndex={0}
            onClick={() => onOpen(t)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onOpen(t);
            }}
            sx={{
              px: 2.5,
              py: 1,
              cursor: "pointer",
              borderBottom: "1px solid",
              borderColor: "divider",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <StatusDot
              status={t.status === "error" ? "failed" : t.status}
              size={6}
            />
            <Box flex={1} minWidth={0}>
              <Typography noWrap sx={{ typography: "s2", fontWeight: 500 }}>
                {scenarioTitle(t.title) || t.name || t.id}
              </Typography>
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {[
                  t.persona?.name,
                  t.repeats > 1 ? `${t.passes}/${t.repeats} passed` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </Box>
            <Iconify
              icon="solar:alt-arrow-right-linear"
              width={12}
              sx={{ color: "text.tertiary" }}
            />
          </Stack>
        ))}
      </Box>
    </Stack>
  );
}

ScenarioList.propTypes = {
  label: PropTypes.string,
  tasks: PropTypes.array,
  onClose: PropTypes.func,
  onOpen: PropTypes.func,
};

/**
 * "Download PDF": the same report, mounted straight under <body> in the
 * light theme, then the browser's print dialog. Print CSS hides the app and
 * shows only this; links stay real links in the saved PDF.
 */
export function PrintPortal({
  env,
  envState,
  identity,
  report,
  token,
  hrefFor,
  onDone,
}) {
  const outer = useTheme();
  /* Latest callback, without re-running the effect (which would print again). */
  const doneRef = useRef(onDone);
  doneRef.current = onDone;
  const [host] = useState(() => {
    const el = document.createElement("div");
    el.className = "sim-print-root";
    return el;
  });
  const printTheme = useMemo(() => {
    const t = createTheme({
      palette: themePalette("light"),
      customShadows: themeCustomShadows("light"),
      shadows: themeShadows("light"),
      shape: { borderRadius: 8 },
      typography: themeTypography,
      direction: outer.direction,
    });
    t.components = componentsOverrides(t);
    return t;
  }, [outer.direction]);

  useEffect(() => {
    document.body.appendChild(host);
    const after = () => doneRef.current?.();
    window.addEventListener("afterprint", after);
    /* After paint, so the report is laid out before the dialog snapshots it. */
    const id = setTimeout(() => window.print(), 250);
    return () => {
      clearTimeout(id);
      window.removeEventListener("afterprint", after);
      host.remove();
    };
  }, [host]);

  return createPortal(
    <MuiThemeProvider theme={printTheme}>
      <GlobalStyles
        styles={{
          "@media screen": { ".sim-print-root": { display: "none" } },
          "@media print": {
            "@page": { margin: "14mm" },
            "html, body": {
              background: "#fff !important",
              height: "auto !important",
              overflow: "visible !important",
            },
            "body > *:not(.sim-print-root)": { display: "none !important" },
            // eslint-disable-next-line no-restricted-syntax -- ds: data colour
            ".sim-print-root": { display: "block", color: "#111" },
            ".sim-print-root *": {
              WebkitPrintColorAdjust: "exact",
              printColorAdjust: "exact",
            },
            ".sim-print-root .summary-how": { display: "none !important" },
            ".sim-print-root a": { color: "inherit" },
          },
        }}
      />
      {/* eslint-disable-next-line no-restricted-syntax -- ds: data colour */}
      <Box sx={{ bgcolor: "#fff", color: (t) => t.palette.text.primary }}>
        <Typography
          sx={{
            maxWidth: 1180,
            mx: "auto",
            px: 3,
            pt: 1,
            typography: "s3",
            color: "text.tertiary",
            wordBreak: "break-all",
          }}
        >
          {`Read-only report · ${sharedUrlOf(env.id, identity.id, token || shareTokenOf(identity, env, envState))}`}
        </Typography>
        <ReportBody
          env={env}
          envState={envState}
          identity={identity}
          report={report}
          token={token}
          hrefFor={hrefFor}
        />
      </Box>
    </MuiThemeProvider>,
    host,
  );
}

PrintPortal.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  identity: PropTypes.object,
  report: PropTypes.object,
  token: PropTypes.string,
  hrefFor: PropTypes.func,
  onDone: PropTypes.func,
};
