import PropTypes from "prop-types";
import { useMemo } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { activateOnKey } from "../helpers/activateOnKey";
import { packStats } from "../helpers/packStats";
import { formatCount } from "../helpers/formatCount";
import { BROWSE_COPY, SURFACE_ICON } from "../prebuiltEnvironments.constants";
import { TEMPLATE_SHAPE } from "../useTemplate.constants";

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
        p: 1.5, borderRadius: 1.25, cursor: "pointer",
        border: "1px solid",
        borderColor: (th) => (selected
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.5) : th.palette.primary.main)
          : th.palette.divider),
        bgcolor: (th) => (selected
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.06) : alpha(th.palette.primary.main, 0.04))
          : "background.paper"),
        transition: "border-color .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: (th) => (selected
            ? undefined
            : (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.35) : th.palette.text.primary)),
        },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Iconify
          icon={SURFACE_ICON[template?.surface] || "solar:widget-linear"}
          width={11}
          sx={{ color: "text.subtitle" }}
        />
        <Typography
          sx={{
            typography: "s3", color: "text.subtitle", fontWeight: "fontWeightBold",
            letterSpacing: 0.5, textTransform: "uppercase",
          }}
        >
          {template?.surface}
        </Typography>
        {popular && (
          <>
            <Box sx={{ color: "text.disabled", fontSize: 10, lineHeight: 1 }}>·</Box>
            <Typography
              sx={{
                typography: "s3", color: "text.subtitle", fontWeight: "fontWeightBold",
                letterSpacing: 0.5, textTransform: "uppercase",
              }}
            >
              {BROWSE_COPY.popular}
            </Typography>
          </>
        )}
      </Stack>

      <Typography noWrap sx={{ typography: "s1", fontWeight: "fontWeightBold", lineHeight: 1.2 }}>
        {template?.name}
      </Typography>
      <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
        {template?.tagline}
      </Typography>

      {statLine && (
        <Typography
          noWrap
          sx={{
            typography: "s3", color: "text.subtitle", fontWeight: "fontWeightSemiBold",
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
