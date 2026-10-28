import { Fragment, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  IconButton,
  InputAdornment,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { improvementPath } from "../improvements/improvementNav";
import { useSimStore } from "../store";
import {
  GROUPS,
  groupUsage,
  monthStart,
  monthUsage,
  usageCsv,
  usageEntries,
} from "../_mock/usageLedger";
import { RATE_PLAN, WORKSPACE_POLICY, savePolicy } from "../_mock/pricing";

/*
  Settings → Usage, the simulation part: this month's runs priced the same
  way as each run's Cost tab, grouped by environment, agent, project, user or
  action, every group opening onto the runs behind it, and an itemized CSV.
  Below it, the limits an admin sets — per run, per project, per person — and
  the warning line. Members see them; only admins change them.
*/

const fmt = (n) => Math.round(n).toLocaleString();
const ALERTS = [0.5, 0.8, 0.9];
const RANGES = [
  { id: "month", label: "This month" },
  { id: "30d", label: "Last 30 days" },
  { id: "all", label: "All time" },
];

/* The system table (the theme styles it to spec): 32px header, 36px rows,
   numbers and values at 12px right-aligned, a row title at 13/500. */
const tableSx = {
  "& thead th": { whiteSpace: "nowrap" },
  "& tbody tr": { height: 36 },
  "& tbody td": { py: 0 },
};
const num = {
  typography: "s3",
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
  whiteSpace: "nowrap",
};
const val = { typography: "s3", whiteSpace: "nowrap" };
const title = { typography: "s2", fontWeight: 500, color: "text.primary" };
const right = { textAlign: "right" };
const fieldSx = {
  "& .MuiInputBase-input": {
    py: 0.5,
    textAlign: "right",
    fontVariantNumeric: "tabular-nums",
  },
};

/* One limit row: what's used of it, and (for an admin) the field to change it. */
function LimitRow({ label, used, limit, admin, onChange, alertAt }) {
  const share = limit ? used / limit : null;
  /* Past the warning line: said in amber, like the run's own limit. */
  const warn = share != null && alertAt && share >= alertAt;
  return (
    <TableRow>
      <TableCell sx={title}>{label}</TableCell>
      <TableCell
        sx={{
          ...num,
          color: share == null ? "text.tertiary" : "text.secondary",
        }}
      >
        {limit == null
          ? `${fmt(used)} used · no limit`
          : `${fmt(used)} of ${fmt(limit)}`}
      </TableCell>
      <TableCell
        sx={{ ...num, color: warn ? "status.warning.fg" : "text.secondary" }}
      >
        {share == null ? "—" : `${Math.round(share * 100)}%`}
      </TableCell>
      <TableCell sx={{ ...right, width: 200 }}>
        {admin ? (
          <TextField
            size="small"
            type="number"
            value={limit ?? ""}
            placeholder="No limit"
            onChange={(e) =>
              onChange(e.target.value === "" ? null : Number(e.target.value))
            }
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">credits</InputAdornment>
              ),
            }}
            inputProps={{ min: 0, "aria-label": `${label} monthly limit` }}
            sx={{ width: 180, ...fieldSx }}
          />
        ) : (
          <Typography sx={{ ...val, color: "text.secondary" }}>
            {limit == null ? "No limit" : `${fmt(limit)} credits`}
          </Typography>
        )}
      </TableCell>
    </TableRow>
  );
}
LimitRow.propTypes = {
  label: PropTypes.string,
  used: PropTypes.number,
  limit: PropTypes.number,
  admin: PropTypes.bool,
  onChange: PropTypes.func,
  alertAt: PropTypes.number,
};

/* A group label row inside the limits table (Projects, Started by). */
function GroupRow({ children }) {
  return (
    <TableRow>
      <TableCell
        colSpan={4}
        sx={{ ...val, color: "text.tertiary", fontWeight: 500, pt: 1.5 }}
      >
        {children}
      </TableCell>
    </TableRow>
  );
}
GroupRow.propTypes = { children: PropTypes.node };

export default function SimulationUsage() {
  const { state } = useSimStore();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  /* Settings opens as a member; ?role=admin shows the admin's view. */
  const admin = params.get("role") === "admin";
  const [by, setBy] = useState("environment");
  const [range, setRange] = useState("month");
  const [open, setOpen] = useState(null);
  const [draft, setDraft] = useState(() => ({
    workspaceLimit: WORKSPACE_POLICY.workspaceLimit,
    maxRunCredits: WORKSPACE_POLICY.maxRunCredits,
    alertAt: WORKSPACE_POLICY.alertAt,
    projects: { ...WORKSPACE_POLICY.projects },
    users: { ...WORKSPACE_POLICY.users },
  }));
  const [saved, setSaved] = useState(false);

  /* The finished runs in the picked range. */
  const since = useMemo(
    () =>
      range === "month"
        ? monthStart()
        : range === "30d"
          ? Date.now() - 30 * 864e5
          : null,
    [range],
  );
  const entries = useMemo(() => usageEntries(state, { since }), [state, since]);
  const improvements = entries.filter((e) => e.kind === "improvement").length;
  const rows = useMemo(() => groupUsage(entries, by), [entries, by]);
  const total = entries.reduce((a, e) => a + e.credits, 0);
  const calls = entries.reduce((a, e) => a + e.calls, 0);
  /* What each project and person used this month — limits are monthly,
     whatever range the table above shows. */
  const usedBy = useMemo(() => {
    const m = monthUsage(state);
    return { project: m.byProject, user: m.byUser, total: m.total };
  }, [state]);

  const exportCsv = () => {
    const blob = new Blob([usageCsv(entries)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `simulation-usage-${new Date().toISOString().slice(0, 7)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const edit = (patch) => {
    setSaved(false);
    setDraft((d) => ({ ...d, ...patch }));
  };
  const setProject = (k, limit) =>
    edit({
      projects: { ...draft.projects, [k]: { ...draft.projects[k], limit } },
    });
  const setUser = (k, limit) =>
    edit({ users: { ...draft.users, [k]: { ...draft.users[k], limit } } });
  /* A run opens on its Cost tab; an improvement on its own page. */
  const openRun = (e) =>
    navigate(
      e.kind === "improvement"
        ? improvementPath(e.envId, e.id)
        : `${paths.dashboard.simulate.simulationRun(e.envId, e.id)}?tab=cost`,
    );
  const groupLabel = GROUPS.find((g) => g.id === by)?.label;
  const expandable = by !== "action";

  return (
    <Box sx={{ mt: 5 }}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={2}
        sx={{ mb: 1.5 }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="h6" fontWeight={600}>
            Simulation
          </Typography>
          <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
            {`${RANGES.find((r) => r.id === range).label} · ${fmt(total)} credits over ${fmt(entries.length - improvements)} runs${improvements ? `, ${fmt(improvements)} improvements` : ""} and ${fmt(calls)} calls · priced as each one's Cost tab`}
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" gap={1} flexShrink={0}>
          <TextField
            select
            size="small"
            value={range}
            onChange={(e) => {
              setRange(e.target.value);
              setOpen(null);
            }}
            inputProps={{ "aria-label": "Time range" }}
          >
            {RANGES.map((r) => (
              <MenuItem key={r.id} value={r.id}>
                {r.label}
              </MenuItem>
            ))}
          </TextField>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={by}
            onChange={(_, v) => {
              if (v) {
                setBy(v);
                setOpen(null);
              }
            }}
            aria-label="Group usage by"
          >
            {GROUPS.map((g) => (
              <ToggleButton key={g.id} value={g.id}>
                {g.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          <Tooltip arrow title="Export the itemized runs as CSV">
            <IconButton
              size="small"
              onClick={exportCsv}
              aria-label="Export simulation usage"
            >
              <Iconify icon="solar:download-minimalistic-linear" width={18} />
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>

      <Table sx={tableSx}>
        <TableHead>
          <TableRow>
            <TableCell>{groupLabel}</TableCell>
            <TableCell sx={right}>Jobs</TableCell>
            <TableCell sx={right}>Calls</TableCell>
            <TableCell sx={right}>Credits</TableCell>
            <TableCell sx={right}>Per call</TableCell>
            <TableCell sx={right}>Share</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.length === 0 && (
            <TableRow>
              <TableCell colSpan={6} sx={val}>
                No simulation runs finished this month.
              </TableCell>
            </TableRow>
          )}
          {rows.map((r) => {
            const expanded = open === r.key;
            return (
              <Fragment key={r.key}>
                <TableRow
                  hover={expandable}
                  tabIndex={expandable ? 0 : undefined}
                  aria-expanded={expandable ? expanded : undefined}
                  onClick={
                    expandable
                      ? () => setOpen(expanded ? null : r.key)
                      : undefined
                  }
                  onKeyDown={
                    expandable
                      ? (e) =>
                          e.key === "Enter" && setOpen(expanded ? null : r.key)
                      : undefined
                  }
                  sx={{ cursor: expandable ? "pointer" : "default" }}
                >
                  <TableCell sx={{ ...title, maxWidth: 0, width: "100%" }}>
                    <Stack
                      direction="row"
                      alignItems="center"
                      gap={0.75}
                      sx={{ minWidth: 0 }}
                    >
                      {expandable && (
                        <Iconify
                          icon={
                            expanded
                              ? "solar:alt-arrow-down-linear"
                              : "solar:alt-arrow-right-linear"
                          }
                          width={14}
                          sx={{ color: "text.tertiary", flexShrink: 0 }}
                        />
                      )}
                      <Box
                        component="span"
                        sx={{
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {r.key}
                      </Box>
                    </Stack>
                  </TableCell>
                  <TableCell sx={num}>{fmt(r.runs)}</TableCell>
                  <TableCell sx={num}>{fmt(r.calls)}</TableCell>
                  <TableCell
                    sx={{ ...num, color: "text.primary", fontWeight: 600 }}
                  >
                    {r.note || fmt(r.credits)}
                  </TableCell>
                  <TableCell sx={num}>
                    {r.perCall ? r.perCall.toFixed(1) : "—"}
                  </TableCell>
                  <TableCell sx={num}>
                    {total ? `${Math.round((r.credits / total) * 100)}%` : "—"}
                  </TableCell>
                </TableRow>
                {expanded &&
                  r.entries.map((e) => (
                    <TableRow
                      key={e.id}
                      hover
                      tabIndex={0}
                      onClick={() => openRun(e)}
                      onKeyDown={(ev) => ev.key === "Enter" && openRun(e)}
                      sx={{ cursor: "pointer" }}
                    >
                      <TableCell
                        sx={{
                          ...val,
                          color: "text.secondary",
                          maxWidth: 0,
                          pl: 4.5,
                        }}
                      >
                        <Box
                          component="span"
                          sx={{
                            display: "block",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {[e.label, e.environment, e.agent, e.user].join(
                            " · ",
                          )}
                        </Box>
                      </TableCell>
                      <TableCell sx={num}>
                        {new Date(e.finishedAt).toLocaleDateString(undefined, {
                          month: "short",
                          day: "numeric",
                        })}
                      </TableCell>
                      <TableCell sx={num}>{fmt(e.calls)}</TableCell>
                      <TableCell sx={num}>{fmt(e.credits)}</TableCell>
                      <TableCell sx={num} colSpan={2}>
                        {e.stopped
                          ? e.stopped === "stopped"
                            ? "stopped"
                            : "at limit"
                          : ""}
                      </TableCell>
                    </TableRow>
                  ))}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>

      {/* ── limits ── */}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mt: 3, mb: 1 }}
      >
        <Box>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            Limits
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {admin
              ? "Members can set a lower limit on a run, never a higher one — from the app, the CLI or CI."
              : "Set by workspace admins. You can set a lower limit on any run you start."}
          </Typography>
        </Box>
        {admin && (
          <Button
            size="small"
            variant="contained"
            onClick={() => {
              savePolicy(draft);
              setSaved(true);
            }}
          >
            {saved ? "Saved" : "Save limits"}
          </Button>
        )}
      </Stack>
      <Table sx={tableSx}>
        <TableHead>
          <TableRow>
            <TableCell>Applies to</TableCell>
            <TableCell sx={right}>Used this month</TableCell>
            <TableCell sx={right}>Share</TableCell>
            <TableCell sx={right}>Monthly limit</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          <LimitRow
            label="Workspace"
            used={usedBy.total}
            limit={draft.workspaceLimit}
            admin={admin}
            alertAt={draft.alertAt}
            onChange={(n) => edit({ workspaceLimit: n })}
          />
          <TableRow>
            <TableCell sx={title}>Per run</TableCell>
            <TableCell sx={{ ...num, color: "text.tertiary" }}>
              The most one run can use
            </TableCell>
            <TableCell />
            <TableCell sx={right}>
              {admin ? (
                <TextField
                  size="small"
                  type="number"
                  value={draft.maxRunCredits}
                  onChange={(e) =>
                    edit({ maxRunCredits: Number(e.target.value) })
                  }
                  InputProps={{
                    endAdornment: (
                      <InputAdornment position="end">credits</InputAdornment>
                    ),
                  }}
                  inputProps={{ min: 1, "aria-label": "Per-run limit" }}
                  sx={{ width: 180, ...fieldSx }}
                />
              ) : (
                <Typography
                  sx={{ ...val, color: "text.secondary" }}
                >{`${fmt(draft.maxRunCredits)} credits`}</Typography>
              )}
            </TableCell>
          </TableRow>
          <TableRow>
            <TableCell sx={title}>Warn at</TableCell>
            <TableCell sx={{ ...num, color: "text.tertiary" }}>
              Of any run, project or person limit
            </TableCell>
            <TableCell />
            <TableCell sx={right}>
              {admin ? (
                <TextField
                  select
                  size="small"
                  value={draft.alertAt}
                  onChange={(e) => edit({ alertAt: Number(e.target.value) })}
                  inputProps={{ "aria-label": "Warning threshold" }}
                  sx={{ width: 180, ...fieldSx }}
                >
                  {ALERTS.map((a) => (
                    <MenuItem
                      key={a}
                      value={a}
                    >{`${Math.round(a * 100)}%`}</MenuItem>
                  ))}
                </TextField>
              ) : (
                <Typography
                  sx={{ ...val, color: "text.secondary" }}
                >{`${Math.round(draft.alertAt * 100)}%`}</Typography>
              )}
            </TableCell>
          </TableRow>
          <GroupRow>Projects</GroupRow>
          {Object.entries(draft.projects).map(([k, v]) => (
            <LimitRow
              key={k}
              label={k}
              used={usedBy.project[k] || 0}
              limit={v.limit}
              admin={admin}
              alertAt={draft.alertAt}
              onChange={(n) => setProject(k, n)}
            />
          ))}
          <GroupRow>Started by</GroupRow>
          {[
            ...new Set([
              ...Object.keys(draft.users),
              ...Object.keys(usedBy.user),
            ]),
          ].map((k) => (
            <LimitRow
              key={k}
              label={k}
              used={usedBy.user[k] || 0}
              limit={draft.users[k]?.limit ?? null}
              admin={admin}
              alertAt={draft.alertAt}
              onChange={(n) => setUser(k, n)}
            />
          ))}
        </TableBody>
      </Table>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
        {`${RATE_PLAN.name} · ${RATE_PLAN.version} rates. A rate change applies to new runs; past runs keep the rates they ran at.`}
      </Typography>
    </Box>
  );
}
