import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Link from "@mui/material/Link";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { ConfirmDialog } from "src/components/custom-dialog";

import {
  MODERATION_CONSEQUENCES,
  MODERATION_NOTE_FIELDS,
  REMOVAL_CATEGORIES,
  REPORT_ACTIONS,
  UNRANK_REASONS,
  hubPaths,
} from "./constants";
import { useHubMe, useResolveReport, useReviewReports } from "./api/queries";
import { formatDateTime, splitRef } from "./lib/format";
import {
  HubEmpty,
  HubError,
  HubSection,
  HubSkeletonCards,
} from "./components/HubStates";
import MaintainerGate from "./components/MaintainerGate";
import { SortTabsLite } from "./components/SortTabsLite";

const RESOLVE_LABELS = {
  dismiss: "Dismiss",
  ask_publisher: "Ask publisher",
  unrank_entry: "Unrank entry",
  yank_version: "Yank version",
  remove_entry: "Remove entry",
  suspend_publisher: "Suspend publisher",
  restore: "Restore",
};

function Resolve({ report }) {
  const [action, setAction] = useState("dismiss");
  const [extra, setExtra] = useState("");
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);
  const resolve = useResolveReport();
  const needs = {
    unrank_entry: UNRANK_REASONS,
    remove_entry: REMOVAL_CATEGORIES,
  }[action];
  const yank = action === "yank_version";
  // community.resolve_report chooses report.version before the request value.
  const yankVersion = report.version || extra.trim();
  const needsConfirm = [
    "remove_entry",
    "yank_version",
    "suspend_publisher",
    "unrank_entry",
  ].includes(action);
  const noteField =
    MODERATION_NOTE_FIELDS[action] || MODERATION_NOTE_FIELDS.private;
  const label = RESOLVE_LABELS[action];
  const target =
    yank && yankVersion
      ? `${report.entry}@${yankVersion}`
      : action === "suspend_publisher"
        ? `@${splitRef(report.entry).publisher}`
        : report.entry;
  const consequence = MODERATION_CONSEQUENCES[action];
  const disabled =
    !note.trim() ||
    (needs && !extra) ||
    (yank && !yankVersion) ||
    resolve.isPending;
  const submit = async () => {
    if (disabled) return;
    try {
      await resolve.mutateAsync({
        id: report.id,
        action,
        note: note.trim(),
        ...(action === "unrank_entry" ? { reason: extra } : {}),
        ...(action === "remove_entry" ? { category: extra } : {}),
        ...(yank ? { version: yankVersion } : {}),
      });
      setConfirming(false);
    } catch {
      // Keep the confirmation open and show resolve.error for a retry.
    }
  };
  return (
    <Box
      sx={{
        display: "grid",
        gap: 1,
        gridTemplateColumns: {
          xs: "1fr",
          sm: "200px 200px minmax(0, 1fr) auto",
        },
        alignItems: "start",
      }}
    >
      <TextField
        select
        size="small"
        label="Action"
        value={action}
        onChange={(e) => {
          setAction(e.target.value);
          setExtra("");
        }}
      >
        {REPORT_ACTIONS.map((a) => (
          <MenuItem key={a.value} value={a.value}>
            {a.label}
          </MenuItem>
        ))}
      </TextField>
      {needs ? (
        <TextField
          select
          size="small"
          label={action === "remove_entry" ? "Category" : "Reason"}
          value={extra}
          onChange={(e) => setExtra(e.target.value)}
        >
          {needs.map((v) => (
            <MenuItem key={v} value={v}>
              {v}
            </MenuItem>
          ))}
        </TextField>
      ) : yank ? (
        <TextField
          size="small"
          label="Version"
          required
          value={report.version || extra}
          InputProps={{ readOnly: Boolean(report.version) }}
          helperText={
            report.version
              ? "This report is tied to this version."
              : "Enter the version to yank."
          }
          onChange={(e) => !report.version && setExtra(e.target.value)}
        />
      ) : (
        <Box />
      )}
      <TextField
        size="small"
        label={noteField.label}
        helperText={noteField.helperText}
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <Button
        variant="outlined"
        color="inherit"
        size="small"
        sx={{ height: 40 }}
        disabled={disabled}
        onClick={() => {
          if (needsConfirm) {
            resolve.reset();
            setConfirming(true);
          } else submit();
        }}
      >
        {label}
      </Button>
      {resolve.isError && !confirming && (
        <Alert severity="error" sx={{ gridColumn: "1 / -1" }}>
          {resolve.error.message}
        </Alert>
      )}
      <ConfirmDialog
        open={confirming}
        onClose={() => !resolve.isPending && setConfirming(false)}
        closeDisabled={resolve.isPending}
        title={`${label} ${target}?`}
        content={
          <>
            <Typography typography="s2">{consequence}</Typography>
            {resolve.isError && (
              <Alert severity="error">{resolve.error.message}</Alert>
            )}
          </>
        }
        action={
          <Button
            variant="contained"
            color="error"
            disabled={disabled}
            onClick={submit}
          >
            {label}
          </Button>
        }
      />
    </Box>
  );
}

Resolve.propTypes = { report: PropTypes.object.isRequired };

// Private reports (SPEC §5.5): newest first, with the reporter's account and
// details, seen only by maintainers. Each resolution needs a note.
export default function HubReportsPage() {
  const me = useHubMe();
  const [status, setStatus] = useState("open");
  const reports = useReviewReports(status, Boolean(me.data?.isMaintainer));
  const rows = reports.data || [];
  return (
    <MaintainerGate
      title="Reports"
      documentTitle="Reports"
      description="Private reports from signed-in users. The reporter gets one email with the outcome."
      testId="hub-reports"
    >
      <SortTabsLite
        value={status}
        onChange={setStatus}
        options={[
          ["open", "Open"],
          ["actioned", "Actioned"],
          ["dismissed", "Dismissed"],
        ]}
      />
      {reports.isLoading ? (
        <HubSkeletonCards count={2} />
      ) : reports.isError ? (
        <HubError error={reports.error} onRetry={() => reports.refetch()} />
      ) : rows.length === 0 ? (
        <HubEmpty
          title={status === "open" ? "No open reports" : "Nothing here"}
          description="Reports arrive from the Report button on entry pages."
        />
      ) : (
        rows.map((report) => {
          const ref = splitRef(report.entry);
          return (
            <HubSection key={report.id} testId="hub-report-row">
              <Box
                sx={{
                  display: "flex",
                  gap: 1,
                  alignItems: "center",
                  flexWrap: "wrap",
                }}
              >
                <Link
                  component={RouterLink}
                  href={hubPaths.entry(ref.publisher, ref.name)}
                  typography="s1"
                  fontWeight={600}
                >
                  {`${report.entry}${report.version ? `@${report.version}` : ""}`}
                </Link>
                <Chip
                  size="small"
                  variant="outlined"
                  color={report.reason === "security" ? "error" : "default"}
                  label={report.reason}
                />
                <Typography
                  typography="s3"
                  color="text.tertiary"
                  sx={{ ml: "auto" }}
                >
                  {`${report.reporter} · ${formatDateTime(report.createdAt)}`}
                </Typography>
              </Box>
              <Typography typography="s2" sx={{ whiteSpace: "pre-wrap" }}>
                {report.details}
              </Typography>
              {report.status === "open" ? (
                <Resolve report={report} />
              ) : (
                <Typography
                  typography="s2"
                  color="text.secondary"
                >{`Resolved: ${report.resolution || report.status}`}</Typography>
              )}
            </HubSection>
          );
        })
      )}
    </MaintainerGate>
  );
}
