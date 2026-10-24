import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Tooltip,
  Popover,
  TextField,
  Button,
  InputAdornment,
  ButtonBase,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  TIERS,
  tierOf,
  FAULT_PRESETS,
  FAULT_FIELDS,
  faultPresetOf,
  faultLine,
  liveAllowedOf,
  liveAdvisoryOf,
} from "../_mock/toolEnv";

/*
  The per-tool fidelity dial and its fault control (PRD v6 Story 5, §4.5).

  Same bordered segmented control the sub-goal mark uses — four short
  words, the selected one filled. The default tier carries a small dot, and
  every segment says what it means on hover. Faults sit beside it as one
  word (Clean · Realistic · Adversarial · Custom) that opens the numbers.
*/

const EFFECT_WORD = {
  read: "read-only tool",
  fresh: "tool whose answer goes stale",
  write: "tool that writes data",
  real: "tool that needs real execution",
};

export function TierDial({ setting, onChange, onAllowLive }) {
  const [allowAnchor, setAllowAnchor] = useState(null);
  const live = liveAllowedOf(setting);
  const tip = (t) => {
    const base = `${t.label} — ${t.note}${t.id === setting.defaultTier ? ` Default for a ${EFFECT_WORD[setting.effect] || "tool like this"}.` : ""}`;
    if (t.id === "live" && setting.tier !== "live" && !live.allowed)
      return live.needsAllow && onAllowLive
        ? `${live.reason} Click to allow.`
        : live.reason;
    return base;
  };

  if (!onChange) {
    const t = tierOf(setting.tier);
    return (
      <Tooltip arrow title={tip(t)}>
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "text.secondary",
            flexShrink: 0,
            minWidth: 64,
            textAlign: "right",
          }}
        >
          {t.word}
        </Typography>
      </Tooltip>
    );
  }

  return (
    <>
      <Stack
        direction="row"
        role="radiogroup"
        aria-label={`Fidelity for ${setting.name}`}
        onClick={(e) => e.stopPropagation()}
        sx={{
          flexShrink: 0,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          p: "1px",
        }}
      >
        {TIERS.map((t) => {
          const on = setting.tier === t.id;
          const isDefault = t.id === setting.defaultTier;
          /* Live: offered for read-only tools once allowed, never for writes. */
          const gated = t.id === "live" && !on && !live.allowed;
          const canAllow = gated && live.needsAllow && !!onAllowLive;
          const disabled = gated && !canAllow;
          return (
            <Tooltip key={t.id} arrow title={tip(t)}>
              <ButtonBase
                type="button"
                role="radio"
                aria-checked={on}
                aria-disabled={disabled || undefined}
                onClick={(e) => {
                  if (on || disabled) return;
                  if (canAllow) {
                    setAllowAnchor(e.currentTarget);
                    return;
                  }
                  onChange(t.id);
                }}
                sx={{
                  p: 0,
                  px: 0.75,
                  height: 20,
                  border: 0,
                  borderRadius: "4px",
                  font: "inherit",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "3px",
                  typography: "s3",
                  fontWeight: on ? 600 : 500,
                  cursor: on || disabled ? "default" : "pointer",
                  color: on
                    ? "text.primary"
                    : disabled
                      ? "text.disabled"
                      : "text.tertiary",
                  bgcolor: on ? "action.selected" : "transparent",
                  "&:hover":
                    on || disabled ? undefined : { color: "text.primary" },
                }}
              >
                {gated && (
                  <Iconify
                    icon="solar:lock-keyhole-minimalistic-linear"
                    width={12}
                    sx={{ flexShrink: 0 }}
                  />
                )}
                {t.word}
                {isDefault && (
                  <Box
                    component="span"
                    aria-label="default"
                    sx={{
                      width: 4,
                      height: 4,
                      borderRadius: "50%",
                      bgcolor: "text.disabled",
                      flexShrink: 0,
                    }}
                  />
                )}
              </ButtonBase>
            </Tooltip>
          );
        })}
      </Stack>
      <Popover
        open={!!allowAnchor}
        anchorEl={allowAnchor}
        onClose={() => setAllowAnchor(null)}
        onClick={(e) => e.stopPropagation()}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              width: 320,
              mt: 0.5,
              border: "1px solid",
              borderColor: "divider",
            },
          },
        }}
      >
        <Box sx={{ px: 2, pt: 1.75, pb: 1.25 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Allow live calls to{" "}
            <Box
              component="span"
              sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
            >
              {setting.name}
            </Box>
            ?
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
          >
            {setting.liveTarget
              ? `It only reads, so a run may call the real tool instead of the world — at ${setting.liveTarget}${setting.viaGateway ? ", with the Gateway's rate limits and checks" : ""}. Answers aren't deterministic. Write tools never run live.`
              : "It only reads, so a run may call the real tool instead of the world. Answers aren't deterministic, and you'll confirm a safe target before the first run. Write tools never run live."}
          </Typography>
        </Box>
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Button
            size="small"
            onClick={() => setAllowAnchor(null)}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => {
              setAllowAnchor(null);
              onAllowLive();
            }}
          >
            Allow and use Live
          </Button>
        </Stack>
      </Popover>
    </>
  );
}
TierDial.propTypes = {
  setting: PropTypes.object.isRequired,
  onChange: PropTypes.func,
  onAllowLive: PropTypes.func,
};

const PRESET_IDS = ["none", "realistic", "adversarial"];
const toDraft = (faults) =>
  Object.fromEntries(
    FAULT_FIELDS.map((f) => [f.key, String(faults?.[f.key] ?? 0)]),
  );
const fromDraft = (draft) => {
  const out = Object.fromEntries(
    FAULT_FIELDS.map((f) => [
      f.key,
      Math.max(0, Math.min(f.max, Math.round(Number(draft[f.key]) || 0))),
    ]),
  );
  return FAULT_FIELDS.some((f) => out[f.key]) ? out : null;
};

/** One word for the profile; click for the preset and the numbers. */
export function FaultsControl({ setting, onApply }) {
  const [anchor, setAnchor] = useState(null);
  const [draft, setDraft] = useState(() => toDraft(setting.faults));
  const preset = faultPresetOf(setting.faults);
  const word = preset === "custom" ? "Custom" : FAULT_PRESETS[preset].label;
  const on = preset !== "none";
  const draftPreset = faultPresetOf(fromDraft(draft));

  const open = (e) => {
    e.stopPropagation();
    if (!onApply) return;
    setDraft(toDraft(setting.faults));
    setAnchor(e.currentTarget);
  };
  const apply = () => {
    const faults = fromDraft(draft);
    const p = faultPresetOf(faults);
    onApply(
      faults,
      p === "custom" ? "custom" : FAULT_PRESETS[p].label.toLowerCase(),
    );
    setAnchor(null);
  };

  return (
    <>
      <Tooltip
        arrow
        title={
          on
            ? faultLine(setting.faults)
            : onApply
              ? "No injected faults — set some"
              : "No injected faults"
        }
      >
        <ButtonBase
          type="button"
          onClick={open}
          sx={{
            p: 0,
            px: 0.75,
            height: 22,
            border: "1px solid",
            borderColor: on ? "divider" : "transparent",
            borderRadius: "4px",
            font: "inherit",
            bgcolor: "transparent",
            cursor: onApply ? "pointer" : "default",
            flexShrink: 0,
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            minWidth: 92,
            justifyContent: "flex-start",
            typography: "s3",
            fontWeight: on ? 600 : 500,
            color: on ? "text.primary" : "text.disabled",
            "&:hover": onApply
              ? { color: "text.primary", borderColor: "divider" }
              : undefined,
          }}
        >
          <Iconify
            icon="solar:bolt-linear"
            width={12}
            sx={{
              color: on ? "text.secondary" : "text.disabled",
              flexShrink: 0,
            }}
          />
          {on ? word : "No faults"}
        </ButtonBase>
      </Tooltip>
      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        onClick={(e) => e.stopPropagation()}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              width: 340,
              mt: 0.5,
              border: "1px solid",
              borderColor: "divider",
            },
          },
        }}
      >
        <Box
          sx={{
            px: 2,
            pt: 1.75,
            pb: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Faults on{" "}
            <Box
              component="span"
              sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
            >
              {setting.name}
            </Box>
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            This share of its calls fails this way in every run. A world edit,
            saved as the next environment version.
          </Typography>
        </Box>
        <Stack spacing={1.5} sx={{ p: 2 }}>
          <Stack
            direction="row"
            sx={{
              alignSelf: "flex-start",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
              p: "1px",
            }}
          >
            {PRESET_IDS.map((id) => {
              const sel = draftPreset === id;
              return (
                <ButtonBase
                  key={id}
                  type="button"
                  aria-pressed={sel}
                  onClick={() => setDraft(toDraft(FAULT_PRESETS[id].faults))}
                  sx={{
                    p: 0,
                    px: 1,
                    height: 22,
                    border: 0,
                    borderRadius: "4px",
                    font: "inherit",
                    typography: "s3",
                    fontWeight: sel ? 600 : 500,
                    cursor: "pointer",
                    color: sel ? "text.primary" : "text.tertiary",
                    bgcolor: sel ? "action.selected" : "transparent",
                  }}
                >
                  {FAULT_PRESETS[id].label}
                </ButtonBase>
              );
            })}
            {draftPreset === "custom" && (
              <Box
                sx={{
                  px: 1,
                  height: 22,
                  display: "inline-flex",
                  alignItems: "center",
                  typography: "s3",
                  fontWeight: 600,
                  borderRadius: "4px",
                  bgcolor: "action.selected",
                }}
              >
                Custom
              </Box>
            )}
          </Stack>
          <Box
            sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1.25 }}
          >
            {FAULT_FIELDS.map((f) => (
              <TextField
                key={f.key}
                size="small"
                type="number"
                label={f.label}
                value={draft[f.key]}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, [f.key]: e.target.value }))
                }
                inputProps={{
                  min: 0,
                  max: f.max,
                  step: f.unit === "ms" ? 100 : 1,
                }}
                InputProps={{
                  endAdornment: (
                    <InputAdornment position="end">
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {f.unit}
                      </Typography>
                    </InputAdornment>
                  ),
                }}
              />
            ))}
          </Box>
        </Stack>
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Button
            size="small"
            onClick={() => setAnchor(null)}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={apply}
          >
            Apply
          </Button>
        </Stack>
      </Popover>
    </>
  );
}
FaultsControl.propTypes = {
  setting: PropTypes.object.isRequired,
  onApply: PropTypes.func,
};

/**
 * Live with nowhere safe to point it — the run waits (AC-5.10). Confirm a
 * sandbox for it, or take the tier back to its default.
 */
export function SafeTargetPrompt({
  setting,
  onConfirm,
  onDowngrade,
  bare = false,
}) {
  const [url, setUrl] = useState("");
  const valid = /^https?:\/\/\S+\.\S+/.test(url.trim());
  const fallback = tierOf(setting.defaultTier);
  return (
    <Stack spacing={1} onClick={(e) => e.stopPropagation()}>
      {!bare && (
        <Stack direction="row" spacing={0.75} alignItems="baseline">
          <Iconify
            icon="solar:danger-triangle-linear"
            width={14}
            sx={{
              color: "status.danger.fg",
              flexShrink: 0,
              position: "relative",
              top: 2,
            }}
          />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            <Box
              component="span"
              sx={{ fontWeight: 600, color: "status.danger.fg" }}
            >
              Blocks runs.
            </Box>{" "}
            Live calls the real{" "}
            <Box
              component="span"
              sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
            >
              {setting.name}
            </Box>
            , and nothing records a sandbox for it. Confirm a safe target or
            lower the tier.
          </Typography>
        </Stack>
      )}
      {(onConfirm || onDowngrade) && (
        <Stack
          direction="row"
          flexWrap="wrap"
          alignItems="center"
          sx={{ gap: 1, pl: bare ? 0 : 2.5 }}
        >
          {onConfirm && (
            <>
              <TextField
                size="small"
                placeholder="https://sandbox.example.com"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                sx={{
                  flex: 1,
                  minWidth: 220,
                  "& input": {
                    typography: "s2",
                    fontFamily: "ui-monospace, Menlo, monospace",
                  },
                }}
              />
              <Button
                size="small"
                variant="outlined"
                disabled={!valid}
                onClick={() => onConfirm(url.trim())}
                sx={{
                  flexShrink: 0,
                  color: "text.primary",
                  borderColor: "divider",
                }}
              >
                Confirm safe target
              </Button>
            </>
          )}
          {onDowngrade && (
            <Button
              size="small"
              onClick={() => onDowngrade(setting.defaultTier)}
              sx={{ flexShrink: 0, color: "text.secondary" }}
            >
              Use {fallback.word} instead
            </Button>
          )}
        </Stack>
      )}
    </Stack>
  );
}
SafeTargetPrompt.propTypes = {
  setting: PropTypes.object.isRequired,
  onConfirm: PropTypes.func,
  onDowngrade: PropTypes.func,
  bare: PropTypes.bool,
};

/**
 * A saved dial the Live rule no longer allows — a write tool on Live. Advice,
 * not a block: the dial is left as it is until someone switches it.
 */
export function LiveAdvisory({ setting, onSwitch }) {
  const advice = liveAdvisoryOf(setting);
  if (!advice) return null;
  return (
    <Stack
      direction="row"
      spacing={0.75}
      alignItems="baseline"
      onClick={(e) => e.stopPropagation()}
    >
      <Iconify
        icon="solar:info-circle-linear"
        width={14}
        sx={{
          color: "text.tertiary",
          flexShrink: 0,
          position: "relative",
          top: 2,
        }}
      />
      <Typography
        sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }}
      >
        <Box component="span" sx={{ fontWeight: 600 }}>
          {advice.label}.
        </Box>{" "}
        {advice.note}
      </Typography>
      {onSwitch && (
        <Button
          size="small"
          onClick={() => onSwitch(advice.to)}
          sx={{ flexShrink: 0, color: "text.secondary" }}
        >
          Switch to {tierOf(advice.to).word}
        </Button>
      )}
    </Stack>
  );
}
LiveAdvisory.propTypes = {
  setting: PropTypes.object.isRequired,
  onSwitch: PropTypes.func,
};
