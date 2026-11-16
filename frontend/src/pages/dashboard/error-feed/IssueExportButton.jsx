import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button, Menu, MenuItem, Stack, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { useErrorFeedOverview } from "src/api/errorFeed/mockAdapter";
import { downloadCsv, tracesToCsv } from "./exportTracesCsv";

/*
  Export (#65): the issue's traces as dev's dataset-ready CSV, or the issue's
  analysis — what it is, the likely cause, the suggested fix, the pattern and
  the traces it rests on — as one Markdown file to share or attach.
*/
const download = (text, filename, type) => {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

const word = (v) =>
  v ? String(v).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) : "—";

const analysisOf = (issue, overview, rootCause) => {
  const traces = overview?.representative_traces || [];
  const lines = [
    `# ${issue.error?.name || issue.cluster_id}`,
    "",
    `${issue.cluster_id} · ${word(issue.severity)} · ${word(issue.status)} · ${issue.trace_count ?? traces.length} traces · ${issue.users_affected ?? "—"} users`,
    "",
  ];
  /* The same cause and fix the page shows: the issue's suggested fix first. */
  const suggested = issue.suggested_fix || null;
  const cause = suggested?.cause || rootCause?.headline;
  if (cause) lines.push("## Likely cause", "", cause, "");
  const fix = suggested?.change
    ? suggested
    : rootCause?.fix && typeof rootCause.fix === "object"
      ? rootCause.fix
      : null;
  const fixText = fix
    ? [fix.change, fix.wrongIf && `Wrong if: ${fix.wrongIf}`]
        .filter(Boolean)
        .join("\n\n")
    : typeof rootCause?.fix === "string"
      ? rootCause.fix
      : "";
  if (fixText) lines.push("## Suggested fix", "", fixText, "");
  if (overview?.pattern_summary)
    lines.push("## Pattern", "", overview.pattern_summary, "");
  if (traces.length) {
    lines.push("## Traces", "");
    traces.forEach((t) => {
      const reason = t.evidence?.judge_reason;
      lines.push(
        `- ${t.id}${t.timestamp ? ` · ${t.timestamp}` : ""}${reason ? ` — ${reason}` : ""}`,
      );
    });
    lines.push("");
  }
  return lines.join("\n");
};

export default function IssueExportButton({ issue, rootCause }) {
  const [anchor, setAnchor] = useState(null);
  const { data: overview } = useErrorFeedOverview(issue?.cluster_id);
  const traces = overview?.representative_traces || [];

  const exportTraces = () => {
    setAnchor(null);
    if (!traces.length) {
      enqueueSnackbar("No traces to export", { variant: "info" });
      return;
    }
    downloadCsv(
      tracesToCsv(traces),
      `error-feed-${issue.cluster_id}-traces.csv`,
    );
    const total = overview?.representative_total ?? traces.length;
    enqueueSnackbar(
      total > traces.length
        ? `Exported the latest ${traces.length} of ${total} traces`
        : `Exported ${traces.length} trace${traces.length === 1 ? "" : "s"}`,
      { variant: total > traces.length ? "warning" : "success" },
    );
  };
  const exportAnalysis = () => {
    setAnchor(null);
    download(
      analysisOf(issue, overview, rootCause),
      `error-feed-${issue.cluster_id}-analysis.md`,
      "text/markdown;charset=utf-8",
    );
    enqueueSnackbar("Exported the analysis", { variant: "success" });
  };

  const items = [
    {
      key: "traces",
      icon: "solar:document-text-linear",
      label: "Traces",
      note: "CSV · ready to upload as a dataset",
      onClick: exportTraces,
      disabled: !traces.length,
    },
    {
      key: "analysis",
      icon: "solar:clipboard-text-linear",
      label: "Analysis",
      note: "Markdown · cause, suggested fix, pattern, traces",
      onClick: exportAnalysis,
    },
  ];

  return (
    <>
      <Button
        size="small"
        variant="outlined"
        startIcon={
          <Iconify icon="solar:download-minimalistic-linear" width={14} />
        }
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{
          height: 30,
          borderRadius: "6px",
          borderColor: "divider",
          color: "text.secondary",
        }}
      >
        Export
      </Button>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {items.map((i) => (
          <MenuItem key={i.key} onClick={i.onClick} disabled={i.disabled}>
            <Stack direction="row" spacing={1} alignItems="flex-start">
              <Iconify
                icon={i.icon}
                width={16}
                sx={{ mt: "2px", color: "text.secondary" }}
              />
              <Stack>
                <Typography sx={{ typography: "s2", fontWeight: 500 }}>
                  {i.label}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {i.note}
                </Typography>
              </Stack>
            </Stack>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

IssueExportButton.propTypes = {
  issue: PropTypes.object,
  rootCause: PropTypes.object,
};
