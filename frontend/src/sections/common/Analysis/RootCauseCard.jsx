// R2 stub (W0a) → filled by L4
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import {
  Link as RouterLink,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import {
  Box,
  Button,
  Chip,
  Collapse,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import axios, { endpoints } from "src/utils/axios";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { analysisKey, analysisParams } from "src/api/develop/analysis";
import { useRunConfigs } from "src/api/develop/r2";
import { canIterate } from "src/sections/experiment-detail/iterate/iterateModel";
import { configDiff } from "src/sections/common/Runs/configDiff";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import {
  iterateHref,
  resultsGridHref,
  taskCompareHref,
} from "src/sections/common/Analysis/analysisLinks";
import notify from "src/sections/common/ux/toast";
import {
  detectablePts,
  rowsToSettle,
} from "src/sections/common/Analysis/workbench/workbenchStats";
import { RULES_METHOD_LABEL } from "src/sections/experiment-detail/results/causeMethod";

const MINUS = "−";
const rowNo = (label) => String(label ?? "").replace(/^Row /, "");

/** `+2.5 pts is within noise. About 1,100 rows would settle it.` */
function noiseLine(vs) {
  if (!vs || vs.significant !== false || !Number.isFinite(vs.delta))
    return null;
  const pts = Math.abs(vs.delta * 100).toFixed(1);
  const sign = vs.delta > 0 ? "+" : vs.delta < 0 ? MINUS : "";
  const need = rowsToSettle({
    improved: vs.improved,
    regressed: vs.regressed,
    n: vs.n_shared,
    delta: vs.delta,
  });
  return `${sign}${pts} pts is within noise.${need ? ` About ${need.toLocaleString("en-US")} rows would settle it.` : ""}`;
}

/**
 * IT-R5 G6 P2 (R4): the two runs `See why` opens. The failing run (the
 * pattern run when only it fails) and a run that passes the first row: in
 * the pattern case every other run passes it, so the page candidate (a
 * valid `cand=`, else the analysis' candidate), else the newest other run;
 * otherwise the run the cause is measured against. Pure; exported for tests.
 * @returns {string[]} [failing, passing]
 */
export function seeWhyPair({ cause, data, runIds = [], cand, runs = [] }) {
  const failing = cause?.run_id;
  if (!failing) return runIds.slice(0, 2);
  const inPattern =
    data?.pattern?.run_id === failing &&
    (data.pattern.rows ?? []).includes(cause.rows?.[0]?.row_id);
  if (!inPattern) {
    const ref = cause.baseline_run_id ?? data?.baseline_run_id;
    const other =
      ref && ref !== failing ? ref : runIds.find((id) => id !== failing);
    return [failing, other].filter(Boolean);
  }
  const passing = runIds.filter((id) => id !== failing);
  const created = (id) =>
    Date.parse(
      (runs ?? []).find((r) => r.run_id === id)?.created_at ??
        (data?.runs ?? []).find((r) => r.run_id === id)?.created_at ??
        "",
    ) || 0;
  const newest = [...passing].sort(
    (a, b) =>
      created(b) - created(a) || passing.indexOf(b) - passing.indexOf(a),
  )[0];
  const pick = [cand, data?.candidate_run_id, newest].find(
    (id) => id && passing.includes(id),
  );
  return [failing, pick].filter(Boolean);
}

/** S2: the link names its candidate (the passing run, or the failing one). */
function withCand(href, cause, data) {
  const u = new URL(href, "http://x");
  const runs = (u.searchParams.get("runs") ?? "").split(",").filter(Boolean);
  const base = u.searchParams.get("baseline") ?? data?.baseline_run_id;
  const cand = runs.find((id) => id !== base) ?? cause?.run_id;
  if (cand) u.searchParams.set("cand", cand);
  return `${u.pathname}${u.search}`;
}

/**
 * Root cause card under the verdict (R2-SPEC F2, §6.3.4): the likely cause
 * of a regression with its evidence rows (R5 `include=root_causes`), what
 * changed (configDiff), whether the move is noise, and `Test a fix →`
 * (the Iterate dock with `scope=rows`). `Rule-based: …`; never
 * auto-applied. Renders nothing when R5 names no cause.
 *
 * Props (frozen, R2 manifest row 16):
 * @param {string} props.datasetId
 * @param {string[]} props.runIds
 * @param {string|null} [props.baselineId]
 * @param {"experiment"|"compare"} [props.mode]
 */
const RootCauseCard = ({
  datasetId,
  runIds = [],
  baselineId,
  mode = "compare",
}) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { experimentId } = useParams();
  const { role } = useAuthContext();
  const canEdit = Boolean(
    RolePermission.DATASETS?.[PERMISSIONS.UPDATE]?.[role] ?? true,
  );
  const [hidden, setHidden] = useState([]);
  const [more, setMore] = useState(false);
  const [allRows, setAllRows] = useState(false);
  const params = {
    ...analysisParams({ runIds, baselineId: baselineId ?? undefined }),
    include: "root_causes",
  };
  const { data } = useQuery({
    queryKey: [...analysisKey(datasetId), params],
    queryFn: () =>
      axios.get(endpoints.develop.datasetRuns.analysis(datasetId), { params }),
    select: (res) => res.data?.result,
    enabled: Boolean(datasetId) && runIds.length > 1,
  });
  const { data: r1 } = useRunConfigs(datasetId, { enabled: runIds.length > 1 });

  const view = useMemo(() => {
    if (!data?.root_causes?.length) return null;
    const label = (id) => data.runs?.find((r) => r.run_id === id)?.label ?? "";
    const pattern =
      data.pattern?.kind === "only_baseline_fails" &&
      data.root_causes.some((c) => c.run_id === data.pattern.run_id)
        ? data.pattern
        : null;
    let causes;
    let title;
    if (pattern && pattern.run_id === data.baseline_run_id) {
      causes = data.root_causes.filter((c) => c.run_id === pattern.run_id);
      title = `Why ${label(pattern.run_id)} fails where others pass`;
    } else {
      // the capability contract: a run that Failed (every row errored) produced no
      // output; it is named Failed on the verdict, never explained here
      // as a regression.
      const failed = (id) =>
        getRunStatus(data.runs?.find((r) => r.run_id === id) ?? null).key ===
        "Failed";
      causes = data.root_causes.filter(
        (c) =>
          runIds.includes(c.run_id) &&
          c.baseline_run_id === data.baseline_run_id &&
          !failed(c.run_id),
      );
      title = causes[0]
        ? `Why ${label(causes[0].run_id)} regressed on ${causes[0].failed_eval_name}`
        : null;
    }
    causes = causes
      .filter((c) => !hidden.includes(c.id))
      .sort((a, b) => (b.attempts ?? 0) - (a.attempts ?? 0));
    return {
      causes,
      title,
      pattern: Boolean(pattern),
      label,
      noun: data.noun ?? "row",
    };
  }, [data, runIds, hidden]);

  if (!view?.causes?.length || !view.title) return null;
  const [cause, ...rest] = view.causes;
  const runs = r1?.results ?? [];
  const base = runs.find((r) => r.run_id === cause.baseline_run_id);
  const cand = runs.find((r) => r.run_id === cause.run_id);
  const changed =
    !view.pattern && base && cand
      ? configDiff(base, cand).map((c) =>
          c.label.replace(/^(\w+) Support Agent /, "$1 "),
        )
      : [];
  const metric = data.metrics?.find(
    (m) => m.key === cause.failed_eval || m.key === `eval:${cause.failed_eval}`,
  );
  const vs = metric?.vs_baseline?.find((v) => v.run_id === cause.run_id);
  const noise = view.pattern ? null : noiseLine(vs);
  const mde = vs
    ? detectablePts({
        improved: vs.improved,
        regressed: vs.regressed,
        n: vs.n_shared,
        delta: vs.delta,
      })
    : null;
  const rowHref = (rowId) =>
    resultsGridHref({
      mode,
      datasetId,
      experimentId,
      runIds,
      baselineId: data.baseline_run_id,
      row: rowId,
    });
  const confirmed = cause.status === "confirmed";
  const testFix = () =>
    navigate(
      `${location.pathname}${iterateHref(location.search, {
        runId: cause.run_id,
        scope: "rows",
        rows: cause.rows.map((r) => rowNo(r.row_label)),
        change: cause.id,
        cause: cause.id,
      })}`,
    );
  const notThis = (c) => {
    setHidden((h) => [...h, c.id]);
    notify(`Hid “${c.title}” from this view`, {
      variant: "info",
      key: `cause-hidden:${c.id}`,
      undo: { onUndo: () => setHidden((h) => h.filter((id) => id !== c.id)) },
    });
  };
  // Door into side by side (R3 P5 MUST 5; IT-R5 G6 P2, R4): the first row
  // of the pattern, the failing run next to a run that PASSES that row —
  // the page candidate when it does, else the newest passing run. Never the
  // page pair when it does not touch the pattern.
  const traceHref = cause.rows?.[0]?.row_id
    ? taskCompareHref({
        datasetId,
        runIds: seeWhyPair({
          cause,
          data,
          runIds,
          cand: new URLSearchParams(location.search).get("cand"),
          runs: r1?.results,
        }),
        baselineId: data.baseline_run_id,
        rowId: cause.rows[0].row_id,
        from: `${location.pathname}${location.search}`,
      })
    : null;
  const seeWhyHref = traceHref ? withCand(traceHref, cause, data) : null;
  const rule = [
    `Rows where ${view.label(cause.run_id)} fails ${cause.failed_eval_name}${view.pattern ? " and the other runs pass" : ` and ${view.label(cause.baseline_run_id)} passes`}, grouped by the first step where the two traces differ.`,
    vs && !view.pattern
      ? `Noise: paired McNemar test on ${vs.n_shared} rows (${vs.improved} better, ${vs.regressed} worse). Rows to settle = (1.96·√p + 0.84·√(p − d²))² / d², 80% power.${mde ? ` These rows detect changes of about ${mde} pts.` : ""}`
      : null,
  ]
    .filter(Boolean)
    .join(" ");

  // IT-R2 G2 P3: rows the other run never ran are not "where others pass";
  // they are named apart (`+2 tasks only #2B ran: T26, T29`), so this list
  // and the verdict's counts (tasks both ran) never disagree silently.
  const rowLink = (r, i) => (
    <React.Fragment key={r.row_id}>
      {i > 0 && ", "}
      <Link component={RouterLink} to={rowHref(r.row_id)} underline="hover">
        {rowNo(r.row_label)}
      </Link>
      {r.trials > 1 && r.failed_trials < r.trials
        ? ` (${r.failed_trials} of ${r.trials} trials)`
        : ""}
    </React.Fragment>
  );
  const causeLine = (c) => {
    const both = c.rows.filter((r) => r.others_ran !== false);
    const alone = c.rows.filter((r) => r.others_ran === false);
    const noun = view.noun ?? "row";
    return (
      <Typography
        typography="s1"
        component="div"
        sx={{ fontVariantNumeric: "tabular-nums" }}
      >
        <Box component="span" fontWeight={600}>
          {c.title}
        </Box>
        {" · "}
        {both.length}:{" "}
        {/* IT-R5 G6 P3: the first 2 rows, then `+N` (budget 14). */}
        {(allRows || both.length <= 3 ? both : both.slice(0, 2)).map(rowLink)}
        {!allRows && both.length > 3 ? (
          <>
            {" "}
            <Link
              component="button"
              type="button"
              underline="hover"
              onClick={() => setAllRows(true)}
              aria-label={`Show all ${both.length} ${view.noun ?? "row"}s`}
              sx={{ typography: "s1", verticalAlign: "baseline" }}
            >
              {`+${both.length - 2}`}
            </Link>
          </>
        ) : null}
        {alone.length ? (
          <Box
            component="span"
            sx={{ color: "text.secondary" }}
            data-testid="cause-only-rows"
          >
            {` · +${alone.length} ${alone.length === 1 ? noun : `${noun}s`} only ${view.label(c.run_id)} ran: `}
            {alone.map(rowLink)}
          </Box>
        ) : null}
      </Typography>
    );
  };

  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        px: 1.5,
        py: 1.25,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={1}
        sx={{ mb: 0.5 }}
      >
        <Stack direction="row" alignItems="center" gap={0.75}>
          <Iconify
            icon="mdi:magnify-scan"
            width={18}
            sx={{ color: "primary.main" }}
          />
          <Typography typography="s1" fontWeight="fontWeightSemiBold">
            {view.title}
          </Typography>
        </Stack>
        <Chip
          size="small"
          icon={
            <Iconify
              icon={
                confirmed ? "mdi:check-circle-outline" : "mdi:creation-outline"
              }
              width={14}
            />
          }
          label={
            confirmed
              ? "Confirmed by rerun"
              : cause.status_label ?? RULES_METHOD_LABEL
          }
          color={confirmed ? "success" : "default"}
          variant="outlined"
          sx={{ height: 22, typography: "s3" }}
        />
      </Stack>
      <Stack gap={0.5}>
        {causeLine(cause)}
        {view.pattern && (
          <Typography typography="s2" color="text.secondary">
            {cause.sentence}
          </Typography>
        )}
        {changed.length > 0 && (
          <Typography typography="s2" color="text.secondary">
            What changed: {changed.join(" · ")}
          </Typography>
        )}
        {noise && (
          <Typography typography="s2" color="text.secondary">
            {noise}
          </Typography>
        )}
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{ mt: 0.5 }}
          flexWrap="wrap"
        >
          {/* IT-R2 G2 P2: the reason first. `See why` (both runs' steps on
              the first failing task) is the primary action; `Test a fix`
              comes after it. */}
          {traceHref && (
            <CustomTooltip
              show
              size="small"
              placement="top"
              arrow
              title={`Open ${rowNo(cause.rows[0].row_label) ? `row ${rowNo(cause.rows[0].row_label)}` : "the first row"} side by side: both runs' steps, aligned, with the first wrong step marked`}
            >
              <Button
                size="small"
                variant="contained"
                startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
                color="primary"
                onClick={() => navigate(seeWhyHref)}
                data-testid="root-cause-see-why"
              >
                See why
              </Button>
            </CustomTooltip>
          )}
          {/* dataset integration FE-0: no Test a fix for a run it does not apply to. */}
          {!cand || canIterate(cand) ? (
            <CustomTooltip
              show={!canEdit}
              title="Editors can start runs"
              size="small"
              placement="top"
              arrow
            >
              <span>
                <Button
                  size="small"
                  variant={traceHref ? "outlined" : "contained"}
                  color={traceHref ? "inherit" : "primary"}
                  disabled={!canEdit}
                  endIcon={<Iconify icon="mdi:arrow-right" width={16} />}
                  onClick={testFix}
                >
                  Test a fix
                </Button>
              </span>
            </CustomTooltip>
          ) : null}
          <Button size="small" color="inherit" onClick={() => notThis(cause)}>
            Not this
          </Button>
          <CustomTooltip show title={rule} size="small" placement="top" arrow>
            <Typography
              typography="s3"
              color="text.disabled"
              sx={{ cursor: "help" }}
            >
              How is this computed?
            </Typography>
          </CustomTooltip>
          {rest.length > 0 && (
            <Button
              size="small"
              color="inherit"
              onClick={() => setMore((m) => !m)}
              endIcon={
                <Iconify
                  icon={more ? "mdi:chevron-up" : "mdi:chevron-down"}
                  width={16}
                />
              }
              sx={{ ml: "auto" }}
            >
              {rest.length} more {rest.length === 1 ? "cause" : "causes"}
            </Button>
          )}
        </Stack>
        <Collapse in={more}>
          <Stack
            gap={0.5}
            sx={{ pt: 0.5, borderTop: 1, borderColor: "divider" }}
          >
            {rest.map((c) => (
              // Cause ids repeat across runs (a scripted cause such as
              // RC_PREFIX is suggested for every candidate), so key by both.
              <Stack
                key={`${c.run_id}:${c.id}`}
                direction="row"
                alignItems="center"
                gap={1}
              >
                <Box sx={{ flex: 1 }}>{causeLine(c)}</Box>
                <Button size="small" color="inherit" onClick={() => notThis(c)}>
                  Not this
                </Button>
              </Stack>
            ))}
          </Stack>
        </Collapse>
      </Stack>
    </Box>
  );
};

RootCauseCard.propTypes = {
  datasetId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  mode: PropTypes.oneOf(["experiment", "compare"]),
};

export default RootCauseCard;
