// Toolbar controls of the leaderboard: Group, Filter and the dataset
// version (inside Filter, A2-04). Columns, charts, deltas and the aggregate
// live in G's View menu. Triggers share the BaselinePicker's outlined 32 px
// look so the row reads as one control strip.
import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  FormControlLabel,
  ListItemIcon,
  ListItemText,
  ListSubheader,
  Menu,
  MenuItem,
  Popover,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { GROUP_OPTIONS } from "./leaderboardModel";

export const TriggerButton = React.forwardRef(
  ({ label, value, onClick, active, icon, ...rest }, ref) => (
    <Button
      ref={ref}
      variant="outlined"
      size="small"
      onClick={onClick}
      startIcon={icon ? <Iconify icon={icon} width={16} /> : undefined}
      endIcon={
        value != null ? (
          <Iconify icon="mdi:chevron-down" width={16} />
        ) : undefined
      }
      sx={{
        height: 32,
        px: 1.25,
        gap: 0.25,
        flexShrink: 0,
        whiteSpace: "nowrap",
        color: "text.primary",
        borderColor: active ? "primary.main" : "divider",
        bgcolor: active ? "action.selected" : "transparent",
        "& .MuiButton-startIcon": { mr: 0.5, color: "text.secondary" },
      }}
      {...rest}
    >
      <Typography
        typography="s1"
        color={value != null ? "text.secondary" : "text.primary"}
        noWrap
      >
        {label}
      </Typography>
      {value != null && (
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          noWrap
          sx={{ ml: 0.5 }}
        >
          {value}
        </Typography>
      )}
    </Button>
  ),
);

TriggerButton.displayName = "TriggerButton";
TriggerButton.propTypes = {
  label: PropTypes.string,
  value: PropTypes.node,
  onClick: PropTypes.func,
  active: PropTypes.bool,
  icon: PropTypes.string,
};

const Check = ({ on }) => (
  <ListItemIcon sx={{ minWidth: 26 }}>
    {on && (
      <Iconify icon="mdi:check" width={16} sx={{ color: "primary.main" }} />
    )}
  </ListItemIcon>
);
Check.propTypes = { on: PropTypes.bool };

// ---------------------------------------------------------------- group by
export function GroupBySelect({ value, onChange, onExpandAll, onCollapseAll }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const current =
    GROUP_OPTIONS.find((o) => o.value === value) ?? GROUP_OPTIONS[0];
  return (
    <>
      <TriggerButton
        ref={ref}
        label="Group:"
        aria-label={`Group runs by ${current.label}`}
        value={current.label}
        onClick={() => setOpen(true)}
      />
      <Menu anchorEl={ref.current} open={open} onClose={() => setOpen(false)}>
        {GROUP_OPTIONS.map((o) => (
          <MenuItem
            key={o.value}
            selected={o.value === value}
            onClick={() => {
              setOpen(false);
              onChange(o.value);
            }}
            sx={{ typography: "s1" }}
          >
            <Check on={o.value === value} />
            <ListItemText
              primary={o.label}
              secondary={o.help}
              primaryTypographyProps={{ typography: "s1" }}
              secondaryTypographyProps={{
                typography: "s3",
                color: "text.secondary",
              }}
            />
          </MenuItem>
        ))}
        {value !== "none" &&
          onExpandAll &&
          onCollapseAll && [
            <Divider key="d" />,
            <MenuItem
              key="expand"
              onClick={() => {
                setOpen(false);
                onExpandAll();
              }}
              sx={{ typography: "s1" }}
            >
              <ListItemIcon>
                <Iconify icon="mdi:unfold-more-horizontal" width={16} />
              </ListItemIcon>
              <ListItemText
                primary="Expand all groups"
                primaryTypographyProps={{ typography: "s1" }}
              />
            </MenuItem>,
            <MenuItem
              key="collapse"
              onClick={() => {
                setOpen(false);
                onCollapseAll();
              }}
              sx={{ typography: "s1" }}
            >
              <ListItemIcon>
                <Iconify icon="mdi:unfold-less-horizontal" width={16} />
              </ListItemIcon>
              <ListItemText
                primary="Collapse all groups"
                primaryTypographyProps={{ typography: "s1" }}
              />
            </MenuItem>,
          ]}
      </Menu>
    </>
  );
}
GroupBySelect.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  onExpandAll: PropTypes.func,
  onCollapseAll: PropTypes.func,
};

// ---------------------------------------------------------------- version
const shortDate = (iso) =>
  iso
    ? new Date(iso).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      })
    : "";

/** `All versions`, then tags (`golden-v3 · v5`), then versions newest first. */
export function VersionSelect({ value, versions, onChange }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const tagged = useMemo(
    () => versions.flatMap((v) => (v.tags ?? []).map((tag) => ({ tag, v }))),
    [versions],
  );
  const selected = versions.find((v) => String(v.number) === String(value));
  const label = selected
    ? `v${selected.number}${selected.is_latest ? " · latest" : selected.tag ? ` · ${selected.tag}` : ""}`
    : "All versions";
  const pick = (v) => {
    setOpen(false);
    onChange(v);
  };
  return (
    <>
      <TriggerButton
        ref={ref}
        label="Version:"
        value={label}
        active={!!selected}
        onClick={() => setOpen(true)}
      />
      <Menu
        anchorEl={ref.current}
        open={open}
        onClose={() => setOpen(false)}
        slotProps={{ paper: { sx: { minWidth: 240, maxHeight: 420 } } }}
      >
        <MenuItem
          selected={!selected}
          onClick={() => pick("all")}
          sx={{ typography: "s1" }}
        >
          <Check on={!selected} />
          <ListItemText
            primary="All versions"
            secondary="Runs on any version of this dataset"
            primaryTypographyProps={{ typography: "s1" }}
            secondaryTypographyProps={{ typography: "s3" }}
          />
        </MenuItem>
        {tagged.length > 0 && (
          <ListSubheader sx={{ typography: "s3", lineHeight: "28px" }}>
            Tags
          </ListSubheader>
        )}
        {tagged.map(({ tag, v }) => (
          <MenuItem
            key={`tag-${tag}`}
            onClick={() => pick(String(v.number))}
            sx={{ typography: "s1" }}
          >
            <Check on={false} />
            <ListItemText
              primary={`${tag} · v${v.number}`}
              primaryTypographyProps={{ typography: "s1" }}
            />
          </MenuItem>
        ))}
        <ListSubheader sx={{ typography: "s3", lineHeight: "28px" }}>
          Versions
        </ListSubheader>
        {versions.map((v) => (
          <MenuItem
            key={v.id}
            selected={selected?.id === v.id}
            onClick={() => pick(String(v.number))}
            sx={{ typography: "s1" }}
          >
            <Check on={selected?.id === v.id} />
            <ListItemText
              primary={`v${v.number}${v.is_latest ? " · latest" : ""}`}
              secondary={`${shortDate(v.created_at)} · ${v.row_count} rows`}
              primaryTypographyProps={{ typography: "s1" }}
              secondaryTypographyProps={{ typography: "s3" }}
            />
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
VersionSelect.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  versions: PropTypes.array,
  onChange: PropTypes.func,
};

// ---------------------------------------------------------------- view

/**
 * IT-R4 G2 P7 (S6): ONE `View` button for the Experiments toolbar (icon
 * `mdi:tune-variant`). Its popover holds, in order: Columns (`6 shown ·
 * Edit`), Evals (`15 · Edit`), Group by, Baseline and `More` (charts,
 * deltas, saved views: the oneview ViewMenu, today's props). Built locally;
 * anchored to its trigger, never over the verdict line.
 */
export function LeaderboardViewButton({
  columnsText,
  onColumns,
  evalsText,
  evalsAlert = false,
  onEvals,
  group,
  baseline,
  runs,
  more,
  changed = false,
}) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const row = (label, body) => (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ minHeight: 36, px: 2 }}
    >
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ width: 76, flexShrink: 0 }}
      >
        {label}
      </Typography>
      <Box sx={{ minWidth: 0, flex: 1, display: "flex", alignItems: "center" }}>
        {body}
      </Box>
    </Stack>
  );
  const edit = (text, onClick, testId, alert) => (
    <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
      <Typography typography="s2" noWrap sx={{ minWidth: 0 }}>
        {text}
      </Typography>
      {alert ? (
        <Box
          component="span"
          aria-hidden
          sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "red.600" }}
        />
      ) : null}
      <Button
        size="small"
        onClick={() => {
          setOpen(false);
          onClick?.();
        }}
        data-testid={testId}
        sx={{ typography: "s2", height: 28, px: 1, ml: "auto" }}
      >
        Edit
      </Button>
    </Stack>
  );
  return (
    <>
      <Button
        ref={ref}
        size="small"
        color="inherit"
        variant="outlined"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        data-testid="leaderboard-view-button"
        startIcon={<Iconify icon="mdi:tune-variant" width={16} />}
        sx={{
          typography: "s2",
          height: 32,
          px: 1.25,
          borderColor: "divider",
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}
      >
        View
        {changed ? (
          <Box
            component="span"
            aria-label="View changed"
            sx={{
              ml: 0.75,
              width: 6,
              height: 6,
              borderRadius: "50%",
              bgcolor: "warning.main",
            }}
          />
        ) : null}
      </Button>
      <Popover
        open={open}
        anchorEl={ref.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: { mt: 0.5, width: 360, py: 1 },
            "data-testid": "leaderboard-view-popover",
          },
        }}
      >
        {row("Columns", edit(columnsText, onColumns, "view-columns-edit"))}
        {row("Evals", edit(evalsText, onEvals, "view-evals-edit", evalsAlert))}
        {row("Group by", group)}
        {row("Baseline", baseline)}
        {runs ? row("Runs", runs) : null}
        {more ? (
          <>
            <Divider sx={{ my: 0.5 }} />
            {row("More", more)}
          </>
        ) : null}
      </Popover>
    </>
  );
}
LeaderboardViewButton.propTypes = {
  columnsText: PropTypes.string,
  onColumns: PropTypes.func,
  evalsText: PropTypes.string,
  evalsAlert: PropTypes.bool,
  onEvals: PropTypes.func,
  group: PropTypes.node,
  baseline: PropTypes.node,
  runs: PropTypes.node,
  more: PropTypes.node,
  changed: PropTypes.bool,
};

// W1-1 kept runs: the View switch that lists every run of each config.
export function EveryRunSwitch({ on = false, onChange }) {
  return (
    <FormControlLabel
      sx={{ m: 0, minWidth: 0 }}
      control={
        <Switch
          size="small"
          checked={on}
          onChange={(e) => onChange?.(e.target.checked)}
          inputProps={{ "data-testid": "view-every-run" }}
        />
      }
      label={
        <Typography typography="s2" sx={{ ml: 0.5 }}>
          Show every run, not only the latest
        </Typography>
      }
    />
  );
}
EveryRunSwitch.propTypes = { on: PropTypes.bool, onChange: PropTypes.func };
