import PropTypes from "prop-types";
import { useState } from "react";
import { Box, ButtonBase, Popover, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { FAULT_PRESETS, TIERS } from "src/api/simulate-environments/_fixtures/detail/world";

// Vel's per-tool fidelity dial: four short words, the selected one filled.
// Sample only; the parent saves nothing.
// The default tier carries a small dot; Live is locked for a tool that
// writes, as Vel's dial does.
export function TierDial({ name, tier, defaultTier, liveLocked = false, onChange }) {
  return (
    <Stack
      direction="row"
      role="radiogroup"
      aria-label={`Fidelity for ${name}`}
      onClick={(e) => e.stopPropagation()}
      sx={{ flexShrink: 0, border: "1px solid", borderColor: "divider", borderRadius: "4px", p: "1px" }}
    >
      {TIERS.map((t) => {
        const on = tier === t.id;
        const locked = liveLocked && t.id === "live" && !on;
        return (
          <CustomTooltip
            key={t.id}
            show
            arrow
            size="small"
            title={locked ? `${name} writes data, so it can't run Live. It stays simulated against the world, recorded or fixed.` : `${t.word}. ${t.note}`}
          >
            <ButtonBase
              role="radio"
              aria-checked={on}
              aria-label={t.word}
              aria-disabled={locked || undefined}
              onClick={() => !on && !locked && onChange(t.id)}
              sx={{
                gap: "3px",
                px: 0.75,
                height: 20,
                borderRadius: "4px",
                typography: "s3",
                fontWeight: on ? 600 : 500,
                color: on ? "text.primary" : locked ? "text.disabled" : "text.subtitle",
                cursor: on || locked ? "default" : "pointer",
                bgcolor: on ? "action.selected" : "transparent",
                "&:hover": on || locked ? undefined : { color: "text.primary" },
              }}
            >
              {locked && <Iconify icon="solar:lock-keyhole-minimalistic-linear" width={12} sx={{ flexShrink: 0 }} />}
              {t.word}
              {t.id === defaultTier && (
                <Box component="span" aria-label="default" sx={{ width: 4, height: 4, borderRadius: "50%", bgcolor: "text.disabled", flexShrink: 0 }} />
              )}
            </ButtonBase>
          </CustomTooltip>
        );
      })}
    </Stack>
  );
}
TierDial.propTypes = {
  name: PropTypes.string.isRequired,
  tier: PropTypes.string.isRequired,
  defaultTier: PropTypes.string,
  liveLocked: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
};

// Vel's faults control: one word that opens the presets. Sample only.
export function FaultsControl({ preset, onApply }) {
  const [anchor, setAnchor] = useState(null);
  const current = FAULT_PRESETS.find((p) => p.id === preset) || FAULT_PRESETS[0];
  const on = current.id !== "none";
  return (
    <>
      <CustomTooltip show arrow size="small" title={on ? current.line : "No injected faults. Set some"}>
        <ButtonBase
          onClick={(e) => {
            e.stopPropagation();
            setAnchor(e.currentTarget);
          }}
          sx={{
            px: 0.75,
            height: 22,
            minWidth: 92,
            justifyContent: "flex-start",
            gap: 0.5,
            flexShrink: 0,
            border: "1px solid",
            borderColor: on ? "divider" : "transparent",
            borderRadius: "4px",
            typography: "s3",
            fontWeight: on ? 600 : 500,
            color: on ? "text.primary" : "text.disabled",
            "&:hover": { color: "text.primary", borderColor: "divider" },
          }}
        >
          <Iconify icon="solar:bolt-linear" width={12} sx={{ flexShrink: 0 }} />
          {on ? current.label : "No faults"}
        </ButtonBase>
      </CustomTooltip>
      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        onClick={(e) => e.stopPropagation()}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 300, mt: 0.5, p: 1 } } }}
      >
        <Typography sx={{ typography: "s3", fontWeight: 600, color: "text.subtitle", px: 1, py: 0.5 }}>
          Injected faults
        </Typography>
        {FAULT_PRESETS.map((p) => (
          <ButtonBase
            key={p.id}
            onClick={() => {
              setAnchor(null);
              onApply(p.id);
            }}
            sx={{ width: "100%", justifyContent: "flex-start", textAlign: "left", px: 1, py: 0.75, borderRadius: "4px", "&:hover": { bgcolor: "action.hover" } }}
          >
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: p.id === current.id ? 600 : 500 }}>{p.label}</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{p.line}</Typography>
            </Box>
          </ButtonBase>
        ))}
      </Popover>
    </>
  );
}
FaultsControl.propTypes = {
  preset: PropTypes.string.isRequired,
  onApply: PropTypes.func.isRequired,
};
