// The audit log (W4-7; W4-SCOPE §6 A2): one read over the dataset's
// versions, deleted rows, label moves, access, sharing, reviews, runs and
// evaluators, newest first, with filters and a before / after view per
// entry. Opened from Dataset settings › Audit log (scope dataset:<id>); the
// experiment and workspace scopes use the same drawer.
import {
  Box,
  Button,
  Collapse,
  Divider,
  Drawer,
  IconButton,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import {
  fetchAllAudit,
  useAuditInfinite,
} from "src/api/develop/dataset-access";
import { toCsv } from "src/sections/common/export/writers";
import { saveFile } from "src/sections/common/export/datasetExport";
import { notify } from "src/sections/common/ux/toast";
import { fToNow } from "src/utils/format-time";
import {
  actionChoices,
  AUDIT_CSV_COLUMNS,
  auditCsvName,
  auditCsvRows,
  auditParams,
  DEFAULT_PERIOD,
  diffRows,
  groupByDay,
  PERIODS,
  sentenceOf,
} from "./auditModel";

const AUDIT_DRAWER_WIDTH = 720;

function Filter({ label, value, onChange, options }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Typography variant="s2" color="text.secondary">
        {label}
      </Typography>
      <Select
        size="small"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        inputProps={{ "aria-label": label || "Period" }}
        // The drawer sits above Dataset settings, so its menus go above it.
        MenuProps={{ sx: { zIndex: (t) => t.zIndex.modal + 2 } }}
        sx={{
          minWidth: 140,
          typography: "s2",
          "& .MuiSelect-select": { py: 0.5 },
        }}
      >
        {options.map((o) => (
          <MenuItem key={o.key} value={o.key} sx={{ typography: "s2" }}>
            {o.label}
          </MenuItem>
        ))}
      </Select>
    </Stack>
  );
}
Filter.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  options: PropTypes.array.isRequired,
};

function EntryRow({ entry }) {
  const [open, setOpen] = useState(false);
  const rows = diffRows(entry);
  return (
    <Box
      sx={{ borderBottom: 1, borderColor: "divider" }}
      data-testid="audit-entry"
    >
      <Box
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) =>
          (e.key === "Enter" || e.key === " ") && setOpen((v) => !v)
        }
        sx={{
          py: 1,
          px: 0.5,
          cursor: rows.length ? "pointer" : "default",
          display: "flex",
          gap: 1,
          alignItems: "baseline",
        }}
      >
        <Iconify
          icon={open ? "mingcute:down-line" : "mingcute:right-line"}
          width={14}
          sx={{ opacity: rows.length ? 0.7 : 0 }}
        />
        <Typography variant="s2" sx={{ flex: 1 }}>
          <b>{sentenceOf(entry)}</b>
          {entry.summary ? ` · ${entry.summary}` : ""}
          {entry.scope?.kind === "experiment" && entry.scope?.name
            ? ` · ${entry.scope.name}`
            : ""}
        </Typography>
        <Typography
          variant="s3"
          color="text.secondary"
          sx={{ whiteSpace: "nowrap" }}
        >
          {fToNow(entry.at)}
        </Typography>
      </Box>
      <Collapse in={open && rows.length > 0} unmountOnExit>
        <Box sx={{ pl: 3, pb: 1.25 }} data-testid="audit-diff">
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "160px 1fr 1fr",
              columnGap: 2,
              rowGap: 0.5,
            }}
          >
            <Typography variant="s3" color="text.secondary" />
            <Typography variant="s3" color="text.secondary">
              Before
            </Typography>
            <Typography variant="s3" color="text.secondary">
              After
            </Typography>
            {rows.map((r) => (
              <React.Fragment key={r.field}>
                <Typography variant="s3" color="text.secondary">
                  {r.field}
                </Typography>
                <Typography variant="s2" sx={{ wordBreak: "break-word" }}>
                  {r.before}
                </Typography>
                <Typography variant="s2" sx={{ wordBreak: "break-word" }}>
                  {r.after}
                </Typography>
              </React.Fragment>
            ))}
          </Box>
          {entry.note && (
            <Typography
              variant="s3"
              color="text.secondary"
              sx={{ display: "block", mt: 0.75 }}
            >
              {entry.note}
            </Typography>
          )}
        </Box>
      </Collapse>
    </Box>
  );
}
EntryRow.propTypes = { entry: PropTypes.object.isRequired };

/**
 * The audit log itself (filters, day groups, entries): the drawer's content,
 * and Settings › Audit log's page (W4 §9, M6) with `scope=workspace:<id>`.
 */
export function AuditLogPanel({ scope, title, enabled = true, onClose }) {
  const [now] = useState(() => Date.now());
  const [groupKey, setGroupKey] = useState("all");
  const [actor, setActor] = useState("anyone");
  const [period, setPeriod] = useState(DEFAULT_PERIOD);
  const [facets, setFacets] = useState({ groups: [], actors: [] });
  const params = useMemo(
    () =>
      auditParams({
        scope,
        groupKey,
        groups: facets.groups,
        actor: actor === "anyone" ? null : actor,
        period,
        now,
      }),
    [scope, groupKey, facets.groups, actor, period, now],
  );
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useAuditInfinite(params, {
      enabled: enabled && Boolean(scope),
    });
  const pages = data?.pages?.map((p) => p.data) ?? [];
  const first = pages[0];
  if (
    first &&
    (first.groups !== facets.groups || first.actors !== facets.actors) &&
    groupKey === "all" &&
    actor === "anyone"
  )
    setFacets({ groups: first.groups ?? [], actors: first.actors ?? [] });
  const entries = pages.flatMap((p) => p?.entries ?? []);
  const [exporting, setExporting] = useState(false);
  const exportCsv = async () => {
    setExporting(true);
    try {
      const all = await fetchAllAudit(params);
      const name = auditCsvName(title);
      saveFile({
        data: toCsv(auditCsvRows(all), AUDIT_CSV_COLUMNS),
        mime: "text/csv",
        name,
      });
      notify(`Saved ${name} · ${all.length} entries`, {
        variant: "success",
        key: "audit-export",
      });
    } catch {
      notify("Couldn't read the audit log. Try again.", {
        variant: "error",
        key: "audit-export",
      });
    } finally {
      setExporting(false);
    }
  };
  const days = groupByDay(entries, now);
  const actionOptions = [
    { key: "all", label: "All" },
    ...actionChoices(facets.groups),
  ];
  const actorOptions = [
    { key: "anyone", label: "Anyone" },
    ...facets.actors.map((a) => ({ key: a.id, label: a.name })),
  ];

  return (
    <Box
      sx={{ p: 2, display: "flex", flexDirection: "column", height: "100%" }}
      data-testid="audit-drawer"
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography variant="m3" fontWeight={600} sx={{ flex: 1 }}>
          Audit log{title ? ` · ${title}` : ""}
        </Typography>
        {onClose ? (
          <IconButton
            size="small"
            aria-label="Close audit log"
            onClick={onClose}
          >
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        ) : null}
      </Stack>
      <Stack
        direction="row"
        spacing={2}
        sx={{ mt: 1.5, flexWrap: "wrap", rowGap: 1 }}
      >
        <Filter
          label="Action"
          value={groupKey}
          onChange={setGroupKey}
          options={actionOptions}
        />
        <Filter
          label="Person"
          value={actor}
          onChange={setActor}
          options={actorOptions}
        />
        <Filter
          label=""
          value={period}
          onChange={setPeriod}
          options={PERIODS}
        />
      </Stack>
      <Stack direction="row" alignItems="center" sx={{ mt: 1 }}>
        <Typography variant="s3" color="text.secondary" sx={{ flex: 1 }}>
          {first
            ? `${first.total} ${first.total === 1 ? "entry" : "entries"}`
            : ""}
        </Typography>
        {/* W4-7 (§9 W3 B integration, M6): the filtered log as CSV. */}
        <Button
          size="small"
          startIcon={<Iconify icon="mdi:download-outline" width={16} />}
          disabled={!first?.total || exporting}
          onClick={exportCsv}
          data-testid="audit-export-csv"
        >
          Export CSV
        </Button>
      </Stack>
      <Divider sx={{ mt: 1 }} />
      <Box sx={{ flex: 1, overflowY: "auto" }}>
        {!isLoading && entries.length === 0 && (
          <Typography
            variant="s2"
            color="text.secondary"
            sx={{ display: "block", py: 3, textAlign: "center" }}
          >
            Nothing matches these filters.
          </Typography>
        )}
        {days.map((d) => (
          <Box key={d.label} data-testid="audit-day">
            <Typography
              variant="s3"
              fontWeight={600}
              color="text.secondary"
              sx={{ display: "block", pt: 1.5, pb: 0.5 }}
            >
              {d.label}
            </Typography>
            {d.entries.map((e) => (
              <EntryRow key={e.id} entry={e} />
            ))}
          </Box>
        ))}
        {hasNextPage && (
          <Box sx={{ py: 1.5, textAlign: "center" }}>
            <Button
              size="small"
              onClick={() => fetchNextPage()}
              disabled={isFetchingNextPage}
            >
              Show more
            </Button>
          </Box>
        )}
      </Box>
    </Box>
  );
}

AuditLogPanel.propTypes = {
  scope: PropTypes.string,
  title: PropTypes.string,
  enabled: PropTypes.bool,
  onClose: PropTypes.func,
};

export default function AuditDrawer({ open, onClose, scope, title }) {
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      // Above Dataset settings (a modal), which opens it.
      sx={{ zIndex: (t) => t.zIndex.modal + 1 }}
      PaperProps={{ sx: { width: AUDIT_DRAWER_WIDTH, maxWidth: "100vw" } }}
    >
      <AuditLogPanel
        scope={scope}
        title={title}
        enabled={Boolean(open)}
        onClose={onClose}
      />
    </Drawer>
  );
}

AuditDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  scope: PropTypes.string,
  title: PropTypes.string,
};
