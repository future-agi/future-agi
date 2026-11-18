import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { activateOnKey } from "../helpers/activateOnKey";
import { packStats } from "../helpers/packStats";
import { formatCount } from "../helpers/formatCount";
import { BROWSE_COPY, SURFACE_ICON } from "../prebuiltEnvironments.constants";
import { TEMPLATE_SHAPE } from "../useTemplate.constants";

const sentenceCase = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/**
 * Compact, selectable master row for the templates browse.
 *
 * Reads as a dense list item — surface label, name, tagline and a stat line —
 * so a whole category fits in the left pane. Selecting it doesn't navigate; the
 * parent opens the build panel in the detail pane.
 */
export default function TemplateRow({ template, popular = false, selected = false, onClick }) {
  const stats = useMemo(() => packStats(template ?? {}), [template]);
  const rows = useMemo(
    () => (template?.seed?.tables || []).reduce((a, t) => a + (t.rows || 0), 0),
    [template],
  );
  const toolCount = template?.tools?.length || 0;

  const statLine = [
    stats.scenarios > 0 && `${formatCount(stats.scenarios)} scenario${stats.scenarios === 1 ? "" : "s"}`,
    toolCount > 0 && `${toolCount} tool${toolCount === 1 ? "" : "s"}`,
    rows > 0 && `${formatCount(rows)} row${rows === 1 ? "" : "s"}`,
  ].filter(Boolean).join(" · ");

  return (
    <Stack
      onClick={(e) => onClick?.(e)}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onKeyDown={activateOnKey((e) => onClick?.(e))}
      spacing={0.75}
      sx={{
        p: 1.5, borderRadius: "8px", cursor: "pointer",
        border: "1px solid",
        borderColor: selected ? "text.primary" : "divider",
        bgcolor: selected ? "action.selected" : "background.paper",
        transition: "border-color .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: selected ? undefined : "text.disabled",
        },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Iconify
          icon={SURFACE_ICON[template?.surface] || "solar:widget-linear"}
          width={11}
          sx={{ color: "text.subtitle" }}
        />
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 500 }}>
          {sentenceCase(template?.surface)}
        </Typography>
        {popular && (
          <>
            <Box sx={{ color: "text.disabled", fontSize: 11, lineHeight: 1 }}>·</Box>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 500 }}>
              {BROWSE_COPY.popular}
            </Typography>
          </>
        )}
      </Stack>

      <Typography noWrap sx={{ typography: "s1_2", fontWeight: 500, lineHeight: 1.2 }}>
        {template?.name}
      </Typography>
      <Typography noWrap sx={{ typography: "s1", color: "text.secondary" }}>
        {template?.tagline}
      </Typography>

      {statLine && (
        <Typography
          noWrap
          sx={{
            typography: "s3", color: "text.subtitle", fontWeight: 500,
            fontVariantNumeric: "tabular-nums", pt: 0.25,
          }}
        >
          {statLine}
        </Typography>
      )}
    </Stack>
  );
}
TemplateRow.propTypes = {
  template: TEMPLATE_SHAPE,
  popular: PropTypes.bool,
  selected: PropTypes.bool,
  onClick: PropTypes.func,
};
