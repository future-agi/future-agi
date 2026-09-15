import PropTypes from "prop-types";
import { useMemo } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { activateOnKey } from "../helpers/activateOnKey";
import { packStats } from "../helpers/packStats";
import { formatCount } from "../helpers/formatCount";
import { SURFACE_ICON } from "../prebuiltEnvironments.constants";

export default function TemplateTile({ template, popular, onClick }) {
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
      onKeyDown={activateOnKey((e) => onClick?.(e))}
      spacing={1.5}
      sx={{
        p: 2, borderRadius: 1.5, cursor: "pointer",
        border: "1px solid", borderColor: "divider", bgcolor: "background.paper",
        minHeight: 176,
        position: "relative",
        overflow: "hidden",
        transition: "border-color .12s ease, transform .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: (th) => th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.4) : th.palette.text.primary,
          transform: "translateY(-1px)",
        },
        "&:hover .tile-arrow": { opacity: 1, transform: "translateX(0)" },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ position: "relative" }}>
        <Iconify
          icon={SURFACE_ICON[template?.surface] || "solar:widget-linear"}
          width={12}
          sx={{ color: "text.subtitle" }}
        />
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            fontWeight: "fontWeightBold",
            letterSpacing: 0.5,
            textTransform: "uppercase",
          }}
        >
          {template?.surface}
        </Typography>
        {popular && (
          <>
            <Box sx={{ color: "text.disabled", fontSize: 10, lineHeight: 1 }}>·</Box>
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                fontWeight: "fontWeightBold",
                letterSpacing: 0.5,
                textTransform: "uppercase",
              }}
            >
              Popular
            </Typography>
          </>
        )}
      </Stack>

      <Box sx={{ position: "relative", flex: 1 }}>
        <Typography sx={{ typography: "m2", fontWeight: "fontWeightBold", lineHeight: 1.2 }}>
          {template?.name}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5, lineHeight: 1.45 }}>
          {template?.tagline}
        </Typography>
      </Box>

      {statLine && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            pt: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
            position: "relative",
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              fontWeight: "fontWeightSemiBold",
              flex: 1,
              minWidth: 0,
              fontVariantNumeric: "tabular-nums",
            }}
            noWrap
          >
            {statLine}
          </Typography>
          <Iconify
            icon="solar:arrow-right-linear"
            width={14}
            className="tile-arrow"
            sx={{
              color: "text.primary",
              opacity: 0,
              transform: "translateX(-4px)",
              transition: "opacity .15s ease, transform .15s ease",
            }}
          />
        </Stack>
      )}
    </Stack>
  );
}
TemplateTile.propTypes = {
  template: PropTypes.shape({
    name: PropTypes.string,
    tagline: PropTypes.string,
    surface: PropTypes.string,
    difficulty: PropTypes.string,
    tools: PropTypes.array,
    rules: PropTypes.array,
    seed: PropTypes.shape({
      tables: PropTypes.arrayOf(
        PropTypes.shape({ rows: PropTypes.number, note: PropTypes.string }),
      ),
    }),
  }),
  popular: PropTypes.bool,
  onClick: PropTypes.func,
};
