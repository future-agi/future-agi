import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Divider,
  FormControlLabel,
  IconButton,
  Popover,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import KeyCap from "src/sections/common/ux/KeyCap";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";

/** What `Reset view` returns to. */
export const TC_VIEW_DEFAULTS = Object.freeze({
  level: "milestones",
  align: "milestone",
  wrap: false,
  prompts: false,
  allTrials: false,
  laneMetrics: "basic",
});

const LEVELS = [
  ["milestones", "Milestones", "Actions and replies only"],
  ["calls", "Calls", "Every model call as a line"],
  ["all", "Everything", "Every call, every folded gap open"],
];
const ALIGNS = [
  ["milestone", "Milestones", "Line up the same actions"],
  ["index", "Index", "Line up step 1 with step 1"],
  ["none", "Off", "Each lane on its own"],
];

const VIEW_TOOLTIP =
  "View options: level of detail, alignment, what this page shows";
const MODIFIED_TOOLTIP = "View changed · Reset view is in this menu";

function Heading({ children }) {
  return (
    <Typography
      typography="s3"
      color="text.secondary"
      fontWeight="fontWeightSemiBold"
      sx={{ px: 1.5, pt: 1.25, pb: 0.5 }}
    >
      {children}
    </Typography>
  );
}
Heading.propTypes = { children: PropTypes.node };

function Segmented({ value, options, onChange, label }) {
  return (
    <Box sx={{ px: 1.5, pb: 1 }}>
      <ToggleButtonGroup
        exclusive
        fullWidth
        size="small"
        value={value}
        onChange={(_, v) => v && onChange(v)}
        aria-label={label}
      >
        {options.map(([v, text, tip]) => (
          <ToggleButton
            key={v}
            value={v}
            title={tip}
            sx={{ typography: "s3", py: 0.5, px: 0.5, textTransform: "none" }}
          >
            {text}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
    </Box>
  );
}
Segmented.propTypes = {
  value: PropTypes.string,
  options: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  label: PropTypes.string,
};

/**
 * Side by side's `View ▾` (ONE-VIEW-SPEC A4-06): the Linear "Display"
 * pattern, like G's ViewMenu, holding what were nine toolbar controls:
 * `Level` (Milestones · Calls · Everything) · `Align` · `Show on this page`
 * (Map, Wrap long lines, Show prompts in opened steps, Show every trial
 * under lane headers) · `Lane metrics: Basic / All` · footer `Reset view`.
 * Local wrapper: G's ViewMenu takes only checkbox extras, not choices.
 */
export default function TcViewMenu({
  alignHelp,
  mapModified,
  view,
  mapOpen,
  onChange,
  onMap,
  onReset,
  blind = false,
  onBlind,
  diffOnly = false,
  onDiffOnly,
  outline,
}) {
  const [open, setOpen] = useState(false);
  const btn = useRef(null);
  const iconOnly = !useMediaQuery("(min-width:1366px)");
  const modified =
    Boolean(mapModified) ||
    Object.entries(TC_VIEW_DEFAULTS).some(([k, v]) => view[k] !== v);
  useRegisterCommands(
    "taskCompare.view",
    [
      {
        id: "tc.view.options",
        label: "View options…",
        keywords: ["view", "display", "level", "align", "wrap", "prompts"],
        icon: "mdi:tune-variant",
        run: () => setOpen(true),
      },
      {
        id: "tc.view.reset",
        label: "Reset view",
        keywords: ["default", "restore", "view"],
        icon: "mdi:restore",
        run: onReset,
        when: () => modified,
      },
      ...LEVELS.map(([v, text]) => ({
        id: `tc.view.level.${v}`,
        label: `Level: ${text}`,
        keywords: ["level", "detail", "steps", "calls"],
        icon: "mdi:format-list-bulleted",
        run: () => onChange({ level: v }),
        when: () => view.level !== v,
      })),
    ],
    [modified, view.level],
  );
  const dot = (
    <Box
      component="span"
      aria-hidden
      sx={{
        width: 6,
        height: 6,
        borderRadius: "50%",
        bgcolor: "warning.main",
        display: "inline-block",
      }}
    />
  );
  const checks = [
    // IT-R4 G4 P5: Differences only and Expand all moved here from the
    // steps toolbar (their keys still work).
    ...(onDiffOnly
      ? [
          {
            id: "diffOnly",
            label: "Differences only",
            checked: Boolean(diffOnly),
            onChange: (v) => onDiffOnly(v),
          },
        ]
      : []),
    ...(outline
      ? [
          {
            id: "expandAll",
            label: "Expand every step",
            checked: Boolean(outline.allOpen),
            onChange: (v) => (v ? outline.expandAll() : outline.collapseAll()),
          },
        ]
      : []),
    {
      id: "map",
      label: "Map",
      checked: mapOpen,
      onChange: onMap,
      keyCap: "M",
    },
    {
      id: "wrap",
      label: "Wrap long lines",
      checked: view.wrap,
      onChange: (v) => onChange({ wrap: v }),
    },
    {
      id: "prompts",
      label: "Show prompts in opened steps",
      checked: view.prompts,
      onChange: (v) => onChange({ prompts: v }),
    },
    {
      id: "allTrials",
      label: "Show every trial under lane headers",
      checked: view.allTrials,
      onChange: (v) => onChange({ allTrials: v }),
    },
    // IT-R1 G4: Blind lives here (and in Lanes ▾ / ⌘K), not on the top bar.
    ...(onBlind
      ? [
          {
            id: "blind",
            label: "Blind review (hide run names)",
            checked: blind,
            onChange: () => onBlind(),
          },
        ]
      : []),
  ];
  return (
    <>
      <CustomTooltip
        show
        size="small"
        disableInteractive
        title={modified ? MODIFIED_TOOLTIP : VIEW_TOOLTIP}
      >
        {iconOnly ? (
          <IconButton
            ref={btn}
            size="small"
            aria-label="View options"
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={() => setOpen(true)}
            sx={{
              color: "text.secondary",
              position: "relative",
              flexShrink: 0,
            }}
          >
            <Iconify icon="mdi:tune-variant" width={18} />
            {modified ? (
              <Box sx={{ position: "absolute", top: 3, right: 3 }}>{dot}</Box>
            ) : null}
          </IconButton>
        ) : (
          <Button
            ref={btn}
            size="small"
            color="inherit"
            aria-label="View options"
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={() => setOpen(true)}
            startIcon={<Iconify icon="mdi:tune-variant" width={16} />}
            endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
            sx={{
              typography: "s2",
              color: "text.secondary",
              minWidth: 0,
              flexShrink: 0,
            }}
          >
            View
            {modified ? (
              <Box sx={{ ml: 0.75, display: "flex" }}>{dot}</Box>
            ) : null}
          </Button>
        )}
      </CustomTooltip>
      <Popover
        open={open}
        anchorEl={btn.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 300 } } }}
      >
        <Box role="dialog" aria-label="View options" sx={{ pb: 0.5 }}>
          <Heading>Level</Heading>
          <Segmented
            label="Level"
            value={view.level}
            options={LEVELS}
            onChange={(v) => onChange({ level: v })}
          />
          <Heading>Align</Heading>
          <Segmented
            label="Align"
            value={view.align}
            options={ALIGNS}
            onChange={(v) => onChange({ align: v })}
          />
          {alignHelp ? (
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ px: 1.5, pb: 1, mt: -0.25 }}
            >
              {alignHelp}
            </Typography>
          ) : null}
          <Divider />
          <Heading>Show on this page</Heading>
          <Stack sx={{ px: 1.5, pb: 0.75 }}>
            {checks.map((x) => (
              <Stack key={x.id} direction="row" alignItems="center">
                <FormControlLabel
                  sx={{ m: 0, flex: 1 }}
                  control={
                    <Checkbox
                      size="small"
                      checked={Boolean(x.checked)}
                      onChange={(e) => x.onChange(e.target.checked)}
                      sx={{ p: 0.5 }}
                    />
                  }
                  label={<Typography typography="s2">{x.label}</Typography>}
                />
                {x.keyCap ? <KeyCap>{x.keyCap}</KeyCap> : null}
              </Stack>
            ))}
          </Stack>
          <Divider />
          <Heading>Lane metrics</Heading>
          <Segmented
            label="Lane metrics"
            value={view.laneMetrics}
            options={[
              ["basic", "Basic", "Steps, cost and time; the rest on hover"],
              ["all", "All", "Also tool errors, tokens and sandbox time"],
            ]}
            onChange={(v) => onChange({ laneMetrics: v })}
          />
          <Divider />
          <Stack direction="row" sx={{ px: 1, pt: 0.5 }}>
            <Box sx={{ flex: 1 }} />
            <Button
              size="small"
              disabled={!modified}
              onClick={() => onReset()}
              startIcon={<Iconify icon="mdi:restore" width={16} />}
              sx={{ typography: "s2" }}
            >
              Reset view
            </Button>
          </Stack>
        </Box>
      </Popover>
    </>
  );
}

TcViewMenu.propTypes = {
  diffOnly: PropTypes.bool,
  onDiffOnly: PropTypes.func,
  outline: PropTypes.object,
  alignHelp: PropTypes.string,
  mapModified: PropTypes.bool,
  view: PropTypes.object.isRequired,
  mapOpen: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
  onMap: PropTypes.func.isRequired,
  onReset: PropTypes.func.isRequired,
  blind: PropTypes.bool,
  onBlind: PropTypes.func,
};
