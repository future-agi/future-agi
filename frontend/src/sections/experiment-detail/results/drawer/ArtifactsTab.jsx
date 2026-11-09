import React from "react";
import PropTypes from "prop-types";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ChatMessageView from "src/components/traceDetail/ChatMessageView";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import { runKindOf } from "src/sections/common/Runs/runKind";
import { useRunConfigs } from "src/api/develop/r2";
import { RESULT_DRAWER_TAB_PROPTYPES } from "./ResultDrawerTabs.propTypes";
import {
  CODE_SUBTABS,
  HARNESS_SUBTABS,
  NO_FILES_TEXT,
  collectedFiles,
  collectedSummary,
  containerLine,
  costLine,
  endStateRow,
  evalRows,
  transcriptLine,
  transcriptMessages,
} from "./artifactsModel";

const MONO = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace";

const Empty = ({ text, action }) => (
  <Stack spacing={1} alignItems="flex-start" sx={{ px: 2, py: 2 }}>
    <Typography typography="s2" color="text.secondary">
      {text}
    </Typography>
    {action}
  </Stack>
);
Empty.propTypes = { text: PropTypes.string, action: PropTypes.node };

const Summary = ({ children }) => (
  <Typography typography="s2" color="text.secondary" sx={{ mb: 1 }}>
    {children}
  </Typography>
);
Summary.propTypes = { children: PropTypes.node };

/** What Collect gathered for one attempt: one block per file (Files). */
const CollectedFiles = ({ art }) => {
  const files = collectedFiles(art);
  if (!files.length) return <Empty text={NO_FILES_TEXT} />;
  return (
    <Stack spacing={1.25} data-testid="artifacts-files">
      <Summary>{collectedSummary(files)}</Summary>
      {files.map((f) => (
        <Box
          key={`${f.kind}-${f.name}`}
          data-testid={`collected-${f.kind}`}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
          }}
        >
          <Stack
            direction="row"
            alignItems="baseline"
            gap={1}
            sx={{ px: 1.25, py: 0.5, bgcolor: "background.neutral" }}
          >
            <Typography
              typography="s2"
              fontWeight="fontWeightMedium"
              sx={{ fontFamily: MONO }}
            >
              {f.name}
            </Typography>
            <Typography typography="s3" color="text.secondary">
              {f.paths
                ? f.paths.length
                  ? `${f.label} · ${f.paths.length} matched`
                  : `${f.label} · none matched`
                : `${f.label} · ${f.lines} ${f.lines === 1 ? "line" : "lines"}`}
            </Typography>
          </Stack>
          <Box
            component="pre"
            sx={{
              m: 0,
              px: 1.25,
              py: 0.75,
              maxHeight: 240,
              overflow: "auto",
              fontFamily: MONO,
              fontSize: 12,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {f.text || "(empty)"}
          </Box>
        </Box>
      ))}
    </Stack>
  );
};
CollectedFiles.propTypes = { art: PropTypes.object };

/**
 * Artifacts of a harness or code attempt (R2-SPEC §5.3.4): Transcript, End
 * state, Evals, Cost & time, Container (harness); Transcript, Cost & time,
 * Code (code). Sub-tab in the URL (`atab=`). Shown only when
 * isArtifactsRun(focused run).
 *
 * Props (frozen): the standard drawer-tab props (+ the shell's
 * `focusedRun`, `onOpenTab`, which other tabs ignore).
 */
const ArtifactsTab = ({
  datasetId,
  row,
  runs = [],
  focusedRunId,
  trial = 1,
  focusedRun,
  onOpenTab,
}) => {
  const [params, setParams] = useSearchParams();
  const run = focusedRun ?? runs.find((r) => r.run_id === focusedRunId);
  const { data: r1 } = useRunConfigs(datasetId, {
    enabled: Boolean(datasetId),
  });
  const full = {
    ...run,
    ...((r1?.results ?? []).find((r) => r.run_id === focusedRunId) ?? {}),
  };
  const kind = runKindOf(full);
  const subtabs = kind === "code" ? CODE_SUBTABS : HARNESS_SUBTABS;
  const atab = subtabs.some((t) => t.value === params.get("atab"))
    ? params.get("atab")
    : subtabs[0].value;
  const setAtab = (v) => {
    if (onOpenTab)
      onOpenTab("artifacts", { atab: v === subtabs[0].value ? null : v });
    else
      setParams(
        (p) => {
          const next = new URLSearchParams(p);
          if (v === subtabs[0].value) next.delete("atab");
          else next.set("atab", v);
          return next;
        },
        { replace: true },
      );
  };

  const result = row?.results?.[focusedRunId] ?? null;
  const t = Math.max(1, Number(trial) || 1);
  const art = result?.artifacts?.[t - 1] ?? null;
  const detail =
    (result?.trials_detail ?? []).find((d) => d.trial === t) ?? null;
  const summary = result?.trace_summary?.[t - 1] ?? detail?.steps ?? null;
  const latency = detail?.latency_ms ?? null;

  if (!art)
    return (
      <Empty
        text="No artifacts for this attempt. Uploads carry the output and its trace; see Trajectory."
        action={
          onOpenTab ? (
            <Button
              size="small"
              variant="outlined"
              startIcon={
                <Iconify icon="mdi:transit-connection-variant" width={16} />
              }
              onClick={() => onOpenTab("trajectory")}
            >
              Open trajectory
            </Button>
          ) : null
        }
      />
    );

  const infra =
    art.container?.exit_code !== 0 &&
    /Setup failed/i.test(art.container?.reason ?? "");

  return (
    <Stack spacing={1.5} data-testid="artifacts-tab" sx={{ px: 2, py: 1.5 }}>
      <ToggleButtonGroup
        exclusive
        size="small"
        value={atab}
        onChange={(e, v) => v && setAtab(v)}
        aria-label="Artifacts"
        sx={{
          alignSelf: "flex-start",
          "& .MuiToggleButton-root": {
            px: 1.5,
            py: 0.25,
            typography: "s2",
            textTransform: "none",
          },
        }}
      >
        {subtabs.map((s) => (
          <ToggleButton key={s.value} value={s.value}>
            {s.label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>

      {atab === "transcript" ? (
        art.transcript?.length ? (
          <Box>
            <Summary>
              {transcriptLine(summary, latency, art.transcript)}
            </Summary>
            <ChatMessageView input={transcriptMessages(art.transcript)} />
          </Box>
        ) : (
          <Empty
            text={
              infra ? art.container.reason : "No transcript for this attempt."
            }
          />
        )
      ) : null}

      {atab === "files" ? <CollectedFiles art={art} /> : null}

      {atab === "end_state" ? (
        art.end_state_diff?.length ? (
          <Box>
            <Summary>
              {art.end_state_summary ?? "End state"} · vs the row&apos;s start
              state
            </Summary>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns:
                  "16px minmax(0, max-content) minmax(0, 1fr)",
                columnGap: 1.5,
                rowGap: 0.75,
                alignItems: "baseline",
                fontFamily: MONO,
                fontSize: 12,
              }}
            >
              {art.end_state_diff.map((d, i) => {
                const r = endStateRow(d);
                return (
                  <React.Fragment key={`${r.path}-${i}`}>
                    <Iconify
                      icon={r.ok ? "mdi:check" : "mdi:close"}
                      width={14}
                      sx={{
                        color: r.ok ? "green.600" : "red.600",
                        alignSelf: "center",
                      }}
                    />
                    <Box
                      component="span"
                      sx={{ color: "text.primary", wordBreak: "break-all" }}
                    >
                      {r.path}
                    </Box>
                    <Box component="span" sx={{ color: "text.secondary" }}>
                      {r.change}
                    </Box>
                  </React.Fragment>
                );
              })}
            </Box>
          </Box>
        ) : (
          <Empty
            text={
              infra ? art.container.reason : "No changes to the sandbox state."
            }
          />
        )
      ) : null}

      {atab === "evals" ? (
        art.checks?.length ? (
          <Stack spacing={0.75}>
            {evalRows(art.checks, detail?.scores).map((e) => (
              <Stack key={e.key} direction="row" alignItems="baseline" gap={1}>
                <Typography
                  typography="s1"
                  sx={{
                    width: 16,
                    color: e.passed
                      ? "green.600"
                      : e.passed === false
                        ? "red.600"
                        : "text.disabled",
                  }}
                >
                  {e.glyph}
                </Typography>
                <Typography typography="s2" sx={{ fontFamily: MONO }}>
                  {e.key}
                </Typography>
                <Typography typography="s3" color="text.disabled">
                  {e.type}
                </Typography>
                {e.reason ? (
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    sx={{ minWidth: 0 }}
                  >
                    {`judge: "${e.reason}"`}
                  </Typography>
                ) : null}
              </Stack>
            ))}
            {art.checks.some(
              (c) => c.passed === null || c.passed === undefined,
            ) ? (
              <Typography typography="s3" color="text.disabled">
                – not reached (an earlier eval failed)
              </Typography>
            ) : null}
          </Stack>
        ) : (
          <Empty
            text={
              infra
                ? `${art.container.reason} (not counted in pass rates)`
                : "No evals scored this attempt."
            }
          />
        )
      ) : null}

      {atab === "cost" ? (
        <Box>
          <Typography typography="s2">
            {costLine(art.cost_breakdown, latency) ?? "No cost recorded."}
          </Typography>
          {art.cost_breakdown?.price_per_1k?.input ? (
            <Typography typography="s3" color="text.secondary" sx={{ mt: 0.5 }}>
              {`${art.cost_breakdown.model} · $${art.cost_breakdown.price_per_1k.input} / $${art.cost_breakdown.price_per_1k.output} per 1K in / out tokens`}
            </Typography>
          ) : null}
        </Box>
      ) : null}

      {atab === "container" ? (
        <Stack spacing={0.5}>
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography typography="s2" sx={{ fontFamily: MONO }}>
              {containerLine(art.container)?.replace(/ · exit .*$/, "")}
            </Typography>
            <CustomTooltip
              show
              title={
                art.container?.exit_code === 0
                  ? "Exited cleanly"
                  : "Non-zero exit"
              }
              size="small"
              placement="top"
              arrow
            >
              <Typography
                typography="s2"
                sx={{
                  fontFamily: MONO,
                  fontWeight: "fontWeightMedium",
                  color:
                    art.container?.exit_code === 0 ? "green.600" : "red.600",
                }}
              >
                {`exit ${art.container?.exit_code ?? "—"}`}
              </Typography>
            </CustomTooltip>
          </Stack>
          {art.container?.reason ? (
            <Typography
              typography="s2"
              color={infra ? "warning.dark" : "error.main"}
            >
              {infra
                ? `${art.container.reason} (not counted in pass rates)`
                : art.container.reason}
            </Typography>
          ) : null}
        </Stack>
      ) : null}

      {atab === "code" ? (
        <Stack spacing={1}>
          <Typography typography="s2">
            {[
              full.config?.code?.repo,
              full.config?.code?.commit
                ? `git ${full.config.code.commit}`
                : null,
              full.config?.code?.dirty?.files?.length
                ? `dirty: ${full.config.code.dirty.files.map((f) => `${f.path} +${f.plus} −${f.minus}${f.patch ? "" : " · diff not recorded"}`).join(", ")}`
                : null,
              full.config?.code?.framework,
            ]
              .filter(Boolean)
              .join(" · ") || "No commit recorded for this run."}
          </Typography>
          {/* L7b absent-sweep: a diff body only from a patch the run
              recorded (`dirty.files[].patch`: `{before, after}` or a unified
              diff string); without one the header line says so. */}
          {(full.config?.code?.dirty?.files ?? [])
            .filter((f) => f?.patch)
            .map((f) => (
              <Box
                key={f.path}
                sx={{
                  p: 1,
                  borderRadius: 0.5,
                  border: "1px solid",
                  borderColor: "divider",
                  fontFamily: MONO,
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                }}
              >
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{ mb: 0.5 }}
                >
                  {`${f.path} · uncommitted change at run time`}
                </Typography>
                {typeof f.patch === "string" ? (
                  <Box component="pre" sx={{ m: 0, fontFamily: MONO }}>
                    {f.patch}
                  </Box>
                ) : (
                  <TextDiff
                    from={String(f.patch.before ?? "")}
                    to={String(f.patch.after ?? "")}
                  />
                )}
              </Box>
            ))}
        </Stack>
      ) : null}
    </Stack>
  );
};

ArtifactsTab.propTypes = {
  ...RESULT_DRAWER_TAB_PROPTYPES,
  // shell extras (ResultDrawer passes them to every tab)
  focusedRun: PropTypes.object,
  onOpenTab: PropTypes.func,
};

export default ArtifactsTab;
