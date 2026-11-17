import React from "react";
import { causesText } from "src/sections/common/Runs/errorCause";
import {
  ledgerNounOf,
  runLedgerLine,
  sampleLine,
  spendLine,
} from "src/sections/common/Runs/runLedger";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  IconButton,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "src/components/snackbar";
import { fDateTime, fToNowStrict } from "src/utils/format-time";
import RunChip from "src/sections/common/Runs/RunChip";
import { VersionNumber } from "src/sections/common/Datasets/VersionChip";
import SplitChips from "src/sections/common/Datasets/SplitChips";
import DirectionIcon from "src/sections/common/Metrics/DirectionIcon";
import {
  getRunStatus,
  runProgressOf,
} from "src/sections/common/Runs/runStatus";
import { runKindOf } from "src/sections/common/Runs/runKind";
import { settingsText } from "src/sections/common/Runs/provenanceModel";
import RunSourceBlock, { useRunWithConfig } from "./RunSourceBlock";
import { promptDoorState } from "src/sections/workbench/createPrompt/workbenchPrefill";
import { compareUrl } from "src/sections/develop-detail/ExperimentTab/leaderboardModel";
import { amendmentLines } from "./drawer/cellHistoryModel";
import { originLine } from "src/sections/common/Runs/originModel";
import { runSettingsLines } from "src/sections/common/RunSettings/runSettingsLines";
import {
  agentLinkOf,
  promptLinkOf,
} from "src/sections/common/RunSettings/targetLinks";

const Section = ({ title, children }) => (
  <Stack spacing={0.75}>
    <Typography
      typography="s3"
      color="text.disabled"
      sx={{ textTransform: "uppercase", letterSpacing: "0.5px" }}
    >
      {title}
    </Typography>
    {children}
  </Stack>
);
Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

const Line = ({ label, children }) => (
  <Stack direction="row" gap={1} alignItems="baseline" sx={{ minWidth: 0 }}>
    <Typography
      typography="s2"
      color="text.secondary"
      sx={{ width: 96, flexShrink: 0 }}
    >
      {label}
    </Typography>
    <Box
      sx={{
        minWidth: 0,
        flex: 1,
        typography: "s2",
        color: "text.primary",
        wordBreak: "break-word",
      }}
    >
      {children}
    </Box>
  </Stack>
);
Line.propTypes = { label: PropTypes.string, children: PropTypes.node };

const paramsText = (params) =>
  Object.entries(params ?? {})
    .filter(([k, v]) => v != null && !["providers", "logoUrl"].includes(k))
    .map(([k, v]) => `${k} ${v}`)
    .join(" · ");

/**
 * Run details (PRODUCT-SPEC §3.10.11, 360 px, from a run chip's menu):
 * Dataset · Target · Evals · Run params · Metadata · ID.
 */
const RunDetailsPopover = ({ anchorEl, onClose, run, color, metrics = [] }) => {
  const navigate = useNavigate();
  const { full } = useRunWithConfig(run);
  if (!run) return null;
  // R2 kinds (My code, Traces, Harness) are described by Source alone.
  const r2Kind = ["code", "traces", "harness"].includes(runKindOf(full));
  const t = run.target ?? {};
  const status = getRunStatus(run);
  const p = runProgressOf(run);
  const runMetrics = metrics.filter((m) => (m.runs ?? []).includes(run.run_id));
  const metadata = Object.entries(run.metadata ?? {}).filter(
    ([, v]) => v != null && v !== "",
  );

  return (
    <>
      <Popover
        open={!!anchorEl}
        anchorEl={anchorEl}
        onClose={onClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { width: 360, mt: 0.5, borderRadius: 1 } } }}
      >
        <Stack spacing={1.5} sx={{ p: 2 }}>
          <Stack direction="row" alignItems="center" gap={1}>
            <RunChip run={run} color={color} size="medium" />
          </Stack>
          <Typography typography="s2" color="text.secondary">
            {`${run.experiment_name ?? ""} · ${status.label}${run.created_by?.name ? ` · ${run.created_by.name}` : ""}${run.created_at ? ` · ${fToNowStrict(run.created_at)}` : ""}`}
          </Typography>
          <Divider />

          <Section title="Source">
            <RunSourceBlock run={run} />
          </Section>

          <Section title="Dataset">
            <Line label="Version">
              <Stack
                direction="row"
                gap={0.75}
                alignItems="center"
                flexWrap="wrap"
              >
                <VersionNumber number={run.dataset_version?.number} />
                {run.dataset_version?.tag && (
                  <Typography typography="s2" color="text.secondary">
                    {run.dataset_version.tag}
                  </Typography>
                )}
                {run.dataset_version && !run.dataset_version.is_latest && (
                  <Typography typography="s2" color="warning.dark">
                    not latest
                  </Typography>
                )}
              </Stack>
            </Line>
            <Line label="Splits">
              {run.splits?.length ? (
                <SplitChips splits={run.splits} max={3} />
              ) : (
                "All rows"
              )}
            </Line>
            <Line label="Rows">{`${Number(p.total || 0).toLocaleString("en-US")} rows${run.trials > 1 ? ` × ${run.trials} tries` : ""}`}</Line>
            {run.started_at && (
              <Line label="Snapshot">{fDateTime(run.started_at)}</Line>
            )}
          </Section>

          {!r2Kind && (
            <Section title="Target">
              {run.target_type === "prompt" && (
                <>
                  <Line label="Prompt">
                    {`${t.prompt_name ?? ""} ${t.prompt_version_name ?? ""}`.trim()}
                  </Line>
                  <Line label="Model">{t.model}</Line>
                  {paramsText(t.model_params) && (
                    <Line label="Params">{paramsText(t.model_params)}</Line>
                  )}
                  {t.prompt_id && (
                    <Button
                      size="small"
                      variant="outlined"
                      endIcon={
                        <Iconify icon="mdi:arrow-top-right" width={14} />
                      }
                      onClick={() =>
                        navigate(promptLinkOf(t, { config: full?.config }), {
                          state: promptDoorState(run, full?.config),
                        })
                      }
                      sx={{ alignSelf: "flex-start", typography: "s2" }}
                    >
                      Open in Prompts
                    </Button>
                  )}
                </>
              )}
              {run.target_type === "agent" && (
                <>
                  <Line label="Agent">{`${t.graph_name ?? ""} · ${t.version_name ?? ""}`}</Line>
                  {t.graph_id ? (
                    <Button
                      size="small"
                      variant="outlined"
                      endIcon={
                        <Iconify icon="mdi:arrow-top-right" width={14} />
                      }
                      onClick={() =>
                        navigate(agentLinkOf(t, { config: full?.config }))
                      }
                      sx={{ alignSelf: "flex-start", typography: "s2" }}
                    >
                      Open in Agent Playground
                    </Button>
                  ) : null}
                </>
              )}
              {run.target_type === "external" && (
                <>
                  <Line label="Source">GitHub Actions</Line>
                  <Line label="Branch">{t.branch}</Line>
                  <Line label="Commit">{t.commit}</Line>
                  {(t.pr_url || t.ci_job_url) && (
                    <Line label="CI job">{t.ci_job_url ?? t.pr_url}</Line>
                  )}
                </>
              )}
              {/* L7c g2 (result:endpoint-runs): an A2A or MCP run names its
                  agent / server and tool, never `Endpoint · POST`. */}
              {run.target_type === "http" && t.source === "a2a" && (
                <Line label="A2A agent">{`${t.url ?? ""} · skill ${t.skill ?? ""}`}</Line>
              )}
              {run.target_type === "http" && t.source === "mcp_tool" && (
                <Line label="MCP tool">{`${t.server ?? ""} · ${t.tool ?? ""}`}</Line>
              )}
              {run.target_type === "http" && !t.source && (
                <Line label="Endpoint">{`${t.method ?? "POST"} ${t.url ?? ""}`}</Line>
              )}
            </Section>
          )}

          {runMetrics.length > 0 && (
            <Section title="Evals">
              {runMetrics.map((m) => (
                <Stack
                  key={m.key}
                  direction="row"
                  alignItems="center"
                  gap={0.75}
                >
                  <Typography typography="s2">{m.name}</Typography>
                  <DirectionIcon
                    higherIsBetter={m.higher_is_better}
                    size={12}
                  />
                  <Typography typography="s3" color="text.secondary">
                    {[
                      m.versions?.[run.run_id] ?? m.version,
                      m.pass_threshold != null
                        ? `pass ≥ ${m.pass_threshold}`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </Typography>
                </Stack>
              ))}
            </Section>
          )}

          {run.started_from ? (
            <Section title="Started from">
              <Stack direction="row" alignItems="center" gap={1}>
                <Typography typography="s2">{`${run.label} is run ${run.attempt} of ${run.started_from.label?.split("·")[0] ?? run.started_from.label}; ${run.started_from.label} keeps its results.`}</Typography>
                <Button
                  size="small"
                  variant="outlined"
                  data-testid="compare-with-parent"
                  onClick={() => {
                    onClose();
                    navigate(
                      compareUrl(run.dataset_id, {
                        runIds: [run.started_from.run_id, run.run_id],
                        baselineId: run.started_from.run_id,
                        tab: "data",
                      }),
                    );
                  }}
                >
                  {`Compare with ${run.started_from.label}`}
                </Button>
              </Stack>
            </Section>
          ) : null}

          {run.amendments?.length ? (
            <Section title="Activity">
              {amendmentLines(run.amendments).map((line, i) => (
                <Typography
                  key={`${i}-${line}`}
                  typography="s2"
                  data-testid="run-activity"
                >
                  {line}
                </Typography>
              ))}
            </Section>
          ) : null}

          <Section title="Origin">
            <Typography typography="s2" data-testid="run-origin">
              {originLine(run)}
            </Typography>
          </Section>

          <Section title="Run params">
            <Line label="Tries">{run.trials ?? 1}</Line>
            <Line label="Concurrency">{run.concurrency ?? "—"}</Line>
            {settingsText(full?.config ?? {}) ? (
              <Line label="Limits">{settingsText(full.config)}</Line>
            ) : null}
            {runSettingsLines(full?.config).map(([label, text]) => (
              <Line key={label} label={label}>
                {text}
              </Line>
            ))}
            {run.sample ? (
              <Line label="Rows">
                {sampleLine(run.sample, { noun: ledgerNounOf(run.ledger) })}
              </Line>
            ) : null}
            <Line label="Progress">
              {run.ledger
                ? runLedgerLine(run)
                : `${p.done} of ${p.total} rows${p.errors ? ` · ${p.errors} errored` : ""}${run.errors_by_cause?.length ? ` (${causesText(run.errors_by_cause)})` : ""}`}
            </Line>
            {spendLine(run) ? (
              <Line label="Spend">{spendLine(run)}</Line>
            ) : null}
          </Section>

          {metadata.length > 0 && (
            <Section title="Metadata">
              {metadata.map(([k, v]) => (
                <Line key={k} label={k}>
                  {typeof v === "object" ? JSON.stringify(v) : String(v)}
                </Line>
              ))}
            </Section>
          )}

          <Divider />
          <Stack direction="row" alignItems="center" gap={0.5}>
            <Typography typography="s2" color="text.secondary">
              ID
            </Typography>
            <Typography
              typography="s2"
              noWrap
              sx={{ fontFamily: "monospace", minWidth: 0 }}
            >
              {run.run_id}
            </Typography>
            <IconButton
              size="small"
              aria-label="Copy run ID"
              onClick={() => {
                copyToClipboard(run.run_id);
                enqueueSnackbar("Run ID copied", { variant: "success" });
              }}
            >
              <Iconify icon="basil:copy-outline" width={16} />
            </IconButton>
          </Stack>
        </Stack>
      </Popover>
    </>
  );
};

RunDetailsPopover.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  run: PropTypes.object,
  color: PropTypes.object,
  metrics: PropTypes.array,
};

export default RunDetailsPopover;
