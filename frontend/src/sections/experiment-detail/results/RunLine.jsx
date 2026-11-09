import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Menu, MenuItem, Stack } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { fmtCount } from "src/sections/common/format/fmt";
import { runShortName } from "src/sections/common/Runs/runLabel";

/** `5 runs · baseline #2B` (pure; exported for tests). */
export function runLineText(runs, baselineId) {
  const live = (runs ?? []).filter((r) => !r.not_found);
  const base = live.find((r) => r.run_id === baselineId);
  const missing = (runs ?? []).length - live.length;
  return [
    `${fmtCount(live.length, "run")}${missing > 0 ? ` (${missing} missing)` : ""}`,
    base && live.length > 1 ? `baseline ${base.label}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

const Swatch = ({ color }) => (
  <Box
    component="span"
    sx={{
      display: "inline-block",
      width: 8,
      height: 14,
      borderRadius: 0.25,
      bgcolor: color ?? "text.disabled",
      flexShrink: 0,
    }}
  />
);
Swatch.propTypes = { color: PropTypes.string };

/**
 * The run bar in one line (one view, A3-09), as the title of the `runs`
 * Section: a colour square per run · `5 runs`. Phrasing content only (it
 * renders inside the Section's toggle); the Section's chevron opens the
 * full run bar in place.
 */
export function RunLineTitle({ runs, colors }) {
  const live = (runs ?? []).filter((r) => !r.not_found);
  // RC#10: a run in the link that no longer exists is counted, not hidden.
  const missing = (runs ?? []).length - live.length;
  return (
    <Box
      component="span"
      data-testid="run-line"
      title={live.map((r) => `${r.label} ${r.name ?? ""}`.trim()).join(" · ")}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.75,
        typography: "s2",
        fontWeight: 400,
        whiteSpace: "nowrap",
      }}
    >
      <Box component="span" sx={{ display: "inline-flex", gap: 0.25 }}>
        {live.map((r) => (
          <Swatch key={r.run_id} color={colors?.[r.run_id]?.solid} />
        ))}
      </Box>
      {fmtCount(live.length, "run")}
      {missing > 0 ? (
        <Box component="span" sx={{ color: "error.main" }}>
          {`(${missing} missing)`}
        </Box>
      ) : null}
    </Box>
  );
}
RunLineTitle.propTypes = { runs: PropTypes.array, colors: PropTypes.object };

/**
 * The collapsed `runs` Section's summary: `· baseline #2B`, the label a
 * picker (A3-09: click → make another run the baseline). Spans only (the
 * Section renders its summary inside a paragraph).
 */
export default function RunLine({ runs, colors, baselineId, onBaseline }) {
  const [anchor, setAnchor] = useState(null);
  const live = (runs ?? []).filter((r) => !r.not_found);
  const base = live.find((r) => r.run_id === baselineId);
  if (!base || live.length < 2) return null;
  const canPick = Boolean(onBaseline);
  return (
    <Stack
      component="span"
      direction="row"
      alignItems="center"
      gap={0.5}
      sx={{ display: "inline-flex", typography: "s2" }}
      data-testid="run-line-summary"
    >
      <span>·</span>
      <span>baseline</span>
      {canPick ? (
        <CustomTooltip
          show
          size="small"
          placement="bottom"
          arrow
          title="Change the baseline: every run is compared against it"
        >
          <ButtonBase
            aria-haspopup="menu"
            aria-label={`Baseline ${base.label}. Change the baseline`}
            onClick={(e) => setAnchor(e.currentTarget)}
            data-testid="run-line-baseline"
            sx={{
              typography: "s2",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
              borderRadius: 0.5,
              px: 0.25,
              textDecoration: "underline dotted",
              textDecorationColor: (t) => t.palette.text.disabled,
              textUnderlineOffset: "3px",
              "&:hover": { bgcolor: "action.hover" },
              "&:focus-visible": {
                outline: "2px solid",
                outlineColor: "primary.main",
              },
            }}
          >
            {base.label}
          </ButtonBase>
        </CustomTooltip>
      ) : (
        <Box component="span" sx={{ fontWeight: "fontWeightSemiBold" }}>
          {base.label}
        </Box>
      )}
      {canPick && (
        <Menu
          anchorEl={anchor}
          open={Boolean(anchor)}
          onClose={() => setAnchor(null)}
        >
          {live.map((r) => (
            <MenuItem
              key={r.run_id}
              selected={r.run_id === baselineId}
              onClick={() => {
                setAnchor(null);
                if (r.run_id !== baselineId) onBaseline(r.run_id);
              }}
              sx={{ typography: "s2", gap: 1 }}
            >
              <Swatch color={colors?.[r.run_id]?.solid} />
              {r.run_id === baselineId
                ? `${r.label} ${runShortName(r)} · baseline`
                : `Make ${r.label} the baseline · ${runShortName(r)}`}
            </MenuItem>
          ))}
        </Menu>
      )}
    </Stack>
  );
}

RunLine.propTypes = {
  runs: PropTypes.array,
  colors: PropTypes.object,
  baselineId: PropTypes.string,
  onBaseline: PropTypes.func,
};
