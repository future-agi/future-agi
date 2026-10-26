import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  InputBase,
  Popover,
  Stack,
  Tooltip,
  Typography,
  ButtonBase,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  STATE_TONE,
  NO_DECISION_NOTE,
  countsTowardNoDecision,
} from "../../_mock/scenarioStates";
import {
  personalDataLine,
  personalDataDetail,
  REPRO_LABEL,
} from "../../_mock/scenarioSources";

/*
  The pieces every view of a scenario's state shares: the chip, the proposed
  mark, the version, and the quiet Archive control. The chip is the table's
  old status chip, worded to the scenario states — Valid · Re-check ·
  Outdated · Stale · Archived — with the detail on hover.
*/

const ICON = {
  valid: "solar:check-circle-linear",
  recheck: "solar:refresh-linear",
  archived: "solar:archive-linear",
  stale: "solar:clock-circle-linear",
};
const MUTED = new Set(["archived", "later"]);

export function StateChip({ state }) {
  const tone = STATE_TONE[state.id] || null;
  const checking = state.proof?.id === "checking";
  /* The proof's own words, when they add something to the state's. */
  const proofLabel =
    state.proof?.label && state.proof.label !== state.label
      ? state.proof.label
      : null;
  const title = (
    <Stack spacing={0.375} sx={{ py: 0.25, maxWidth: 300 }}>
      <Typography sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}>
        {`${state.label} — ${state.note}`}
      </Typography>
      {state.why && (
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          {state.why}
        </Typography>
      )}
      {proofLabel && (
        <Typography sx={{ typography: "s3", color: "inherit", opacity: 0.7 }}>
          {proofLabel}
        </Typography>
      )}
      {countsTowardNoDecision(state) && (
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          {NO_DECISION_NOTE}
        </Typography>
      )}
    </Stack>
  );
  return (
    <Tooltip arrow title={title}>
      <Box
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.625,
          flexShrink: 0,
          height: 22,
          px: 0.875,
          borderRadius: "4px",
          border: "1px solid",
          borderColor: tone ? alpha(tone, 0.4) : "divider",
          bgcolor: (t) =>
            tone
              ? alpha(tone, t.palette.mode === "dark" ? 0.12 : 0.06)
              : "transparent",
          color:
            tone || (MUTED.has(state.id) ? "text.disabled" : "text.secondary"),
        }}
      >
        {checking ? (
          <CircularProgress
            size={10}
            thickness={6}
            sx={{ color: "text.disabled" }}
          />
        ) : (
          ICON[state.id] && (
            <Iconify
              icon={ICON[state.id]}
              width={12}
              sx={{ color: "text.disabled" }}
            />
          )
        )}
        <Typography
          noWrap
          sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}
        >
          {checking ? "Checking…" : state.label}
        </Typography>
      </Box>
    </Tooltip>
  );
}
StateChip.propTypes = { state: PropTypes.object.isRequired };

/*
  Archive — a hand edit, reversible with Restore. No reason to pick; a note
  is optional. An icon in the table's actions column, a text link in the
  detail.
*/
export function ArchiveControl({
  onArchive,
  variant = "icon",
  disabled = false,
}) {
  const [el, setEl] = useState(null);
  const open = (e) => {
    e.stopPropagation();
    setEl(e.currentTarget);
  };
  return (
    <>
      {variant === "icon" ? (
        <Tooltip
          arrow
          title={
            disabled
              ? "Fork this environment to edit."
              : "Archive — keep it listed, stop running it"
          }
        >
          <span>
            <IconButton
              size="small"
              disabled={disabled}
              onClick={open}
              aria-label="Archive scenario"
            >
              <Iconify
                icon="solar:archive-linear"
                width={16}
                sx={{ color: "text.tertiary" }}
              />
            </IconButton>
          </span>
        </Tooltip>
      ) : (
        <TextAction onClick={open} disabled={disabled}>
          Archive
        </TextAction>
      )}
      <ArchivePopover
        anchorEl={el}
        onClose={() => setEl(null)}
        onArchive={onArchive}
      />
    </>
  );
}
ArchiveControl.propTypes = {
  onArchive: PropTypes.func.isRequired,
  variant: PropTypes.oneOf(["icon", "text"]),
  disabled: PropTypes.bool,
};

/* Archive's confirm, with its note — opened from a control or a menu item. */
export function ArchivePopover({ anchorEl, onClose, onArchive }) {
  const [note, setNote] = useState("");
  const done = () => {
    onClose();
    onArchive(note.trim());
    setNote("");
  };
  return (
    <Popover
      open={!!anchorEl}
      anchorEl={anchorEl}
      onClose={onClose}
      onClick={(e) => e.stopPropagation()}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { width: 320, p: 1.5, mt: 0.5 } } }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        Archive this scenario?
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}>
        It stays listed and can be restored, but stops running and counting.
      </Typography>
      <InputBase
        fullWidth
        multiline
        minRows={2}
        autoFocus
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Note (optional)"
        sx={{
          mt: 1.25,
          px: 1,
          py: 0.75,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
          typography: "s2",
        }}
      />
      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={0.75}
        sx={{ mt: 1.25 }}
      >
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button size="small" variant="contained" color="primary" onClick={done}>
          Archive
        </Button>
      </Stack>
    </Popover>
  );
}
ArchivePopover.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  onArchive: PropTypes.func.isRequired,
};

/* A small bordered word — the shape the table's "New · env" tag already uses. */
function Tag({ children, icon, strong = false, dashed = false }) {
  return (
    <Chip
      size="small"
      variant="outlined"
      icon={icon}
      label={children}
      sx={{
        flexShrink: 0,
        ...(dashed && { borderStyle: "dashed" }),
        ...(strong && { borderColor: "text.disabled", color: "text.primary" }),
      }}
    />
  );
}
Tag.propTypes = {
  children: PropTypes.node,
  icon: PropTypes.node,
  strong: PropTypes.bool,
  dashed: PropTypes.bool,
};

/* A scenario a source proposed that isn't in the environment yet — it joins
   once someone adds it. */
export function ProposedBadge() {
  return (
    <Tooltip
      arrow
      title="Proposed — it joins the environment once you add it. It doesn't run until then."
    >
      <Box sx={{ display: "inline-flex" }}>
        <Tag dashed>Proposed</Tag>
      </Box>
    </Tooltip>
  );
}

/* A quiet text action — the detail's Re-prove / Rewrite / Archive / Restore. */
export function TextAction({ onClick, disabled = false, children }) {
  return (
    <ButtonBase
      type="button"
      disabled={disabled}
      onClick={onClick}
      sx={{
        p: 0,
        border: 0,
        bgcolor: "transparent",
        font: "inherit",
        flexShrink: 0,
        typography: "s2",
        fontWeight: 600,
        lineHeight: 1.6,
        color: disabled ? "text.disabled" : "text.primary",
        cursor: disabled ? "default" : "pointer",
        "&:hover": disabled ? undefined : { textDecoration: "underline" },
      }}
    >
      {children}
    </ButtonBase>
  );
}
TextAction.propTypes = {
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
  children: PropTypes.node,
};

/*
  Critical or supporting, per sub-goal. A failed critical one fails the
  scenario; a failed supporting one only lowers its score. Read-only, it is
  just the word.
*/
export function MarkToggle({ value, onChange, disabled = false }) {
  if (disabled || !onChange) {
    return (
      <Typography
        sx={{
          typography: "s3",
          fontWeight: value === "critical" ? 600 : 500,
          color: value === "critical" ? "text.primary" : "text.tertiary",
          flexShrink: 0,
        }}
      >
        {value === "critical" ? "Critical" : "Supporting"}
      </Typography>
    );
  }
  return (
    <Stack
      direction="row"
      onClick={(e) => e.stopPropagation()}
      sx={{
        flexShrink: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        p: "1px",
      }}
    >
      {["critical", "supporting"].map((m) => {
        const on = value === m;
        return (
          <Tooltip
            key={m}
            arrow
            title={
              m === "critical"
                ? "Fails the scenario if it isn't met"
                : "Only lowers the score if it isn't met"
            }
          >
            <ButtonBase
              type="button"
              aria-pressed={on}
              onClick={() => !on && onChange(m)}
              sx={{
                p: 0,
                px: 0.75,
                height: 20,
                border: 0,
                borderRadius: "4px",
                font: "inherit",
                typography: "s3",
                fontWeight: on ? 600 : 500,
                cursor: on ? "default" : "pointer",
                color: on ? "text.primary" : "text.tertiary",
                bgcolor: on ? "action.selected" : "transparent",
                "&:hover": on ? undefined : { color: "text.primary" },
              }}
            >
              {m === "critical" ? "Critical" : "Supporting"}
            </ButtonBase>
          </Tooltip>
        );
      })}
    </Stack>
  );
}
MarkToggle.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  disabled: PropTypes.bool,
};

/*
  Where a production-derived scenario came from, in plain words:
  "Personal data replaced · 5 values", the reproduce state of an Error Feed
  conversation, and the mark on a word-for-word replay. Quiet tags — the
  same bordered word as the proposed mark.
*/
export function PersonalDataTag({ row }) {
  const line = personalDataLine(row);
  if (!line) return null;
  return (
    <Tooltip arrow title={personalDataDetail(row) || ""}>
      <Box sx={{ display: "inline-flex", cursor: "help" }}>
        <Tag icon={<Iconify icon="solar:shield-user-linear" width={12} />}>
          {line}
        </Tag>
      </Box>
    </Tooltip>
  );
}
PersonalDataTag.propTypes = { row: PropTypes.object };

/* Reproduced is the one that becomes a target; the others are said plainly. */
export function ReproTag({ repro, target = false }) {
  if (!repro?.state) return null;
  const label = REPRO_LABEL[repro.state] || repro.state;
  const tip =
    repro.state === "reproduced"
      ? `Failed ${repro.fails} of ${repro.of} on today's agent — ${target ? "a target the fix has to make pass" : "it becomes a target"}.`
      : repro.state === "inconclusive"
        ? `Failed ${repro.fails} of ${repro.of} — once in three is neither a reproduction nor a pass. Not a target.`
        : "Passed every time on today's agent — the failure didn't happen. Not a target.";
  return (
    <Tooltip arrow title={tip}>
      <Box sx={{ display: "inline-flex", cursor: "help" }}>
        <Tag
          strong={repro.state === "reproduced"}
          dashed={repro.state === "inconclusive"}
        >
          {label}
        </Tag>
      </Box>
    </Tooltip>
  );
}
ReproTag.propTypes = { repro: PropTypes.object, target: PropTypes.bool };
