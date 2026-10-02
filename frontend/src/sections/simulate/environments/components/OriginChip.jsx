import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import { ORIGIN_KINDS } from "../buildEnvironment/provenance.constants";

// A single provenance chip: the short origin tag with a tooltip that spells out
// where the fact came from and the file:line it was read at. Ported verbatim
// from the designer's primitives.jsx OriginChip.
export default function OriginChip({ origin, file, line, showPath = true }) {
  const meta = ORIGIN_KINDS[origin];
  if (!meta) return null;

  return (
    <Tooltip
      arrow
      placement="top"
      title={
        <Box sx={{ maxWidth: 300, py: 0.5 }}>
          <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold", mb: 0.25 }}>{meta.label}</Typography>
          <Typography sx={{ typography: "s3" }}>{meta.note}</Typography>
          {file && (
            <Typography sx={{ typography: "s3", mt: 0.75, fontFamily: "ui-monospace, Menlo, monospace", opacity: 0.75 }}>
              {file}:{line}
            </Typography>
          )}
        </Box>
      }
    >
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ flexShrink: 0, cursor: "default" }}>
        {showPath && file && (
          <Typography
            noWrap
            sx={{
              typography: "s3", color: "text.disabled", fontFamily: "ui-monospace, Menlo, monospace",
              display: { xs: "none", lg: "block" }, maxWidth: 300,
            }}
          >
            {file}:{line}
          </Typography>
        )}
        <Typography
          sx={{
            px: 0.75, py: 0.25, borderRadius: 0.5,
            typography: "s3", fontWeight: "fontWeightBold", color: meta.color,
            bgcolor: (t) => alpha(meta.color, t.palette.mode === "dark" ? 0.16 : 0.1),
          }}
        >
          {meta.short}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
OriginChip.propTypes = {
  origin: PropTypes.oneOf(Object.keys(ORIGIN_KINDS)),
  file: PropTypes.string,
  line: PropTypes.number,
  showPath: PropTypes.bool,
};
