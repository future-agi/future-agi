import PropTypes from "prop-types";
import { Box, Tooltip } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";

/**
 * The release-blocker triangle, with the reason it was set.
 *
 * Read-only unless `onToggle` is given. Toggleable rows show a hollow triangle
 * on hover so a scenario can be marked without opening the editor; one that
 * was un-marked keeps its hollow triangle, so the change is still visible.
 */
/* The must-pass badge (design: 24px accent-subtle circle, linear
   shield-check in accent-fg, optional "Must pass" label). Optional
   scenarios show nothing; they're marked from the row menu, the selection
   bar or the editor. */
const SUBTLE = { light: "#f5f3ff", dark: "rgba(120,87,252,0.14)" };
const FG = { light: "#573fcc", dark: "#b8afff" };
const MUST_PASS_TIP =
  "Must pass: reported on its own in every run, and an Epoch can't ship a change that breaks it. A run never fails or stops because of it.";

export default function MustPassBadge({
  row,
  onToggle,
  locked = false,
  label = false,
}) {
  const theme = useTheme();
  const mode = theme.palette.mode === "dark" ? "dark" : "light";
  if (!row?.critical) return null;
  const canToggle = !!onToggle && !locked;
  return (
    <Tooltip arrow title={MUST_PASS_TIP}>
      <Box
        component={canToggle ? "button" : "span"}
        type={canToggle ? "button" : undefined}
        aria-label={canToggle ? "Must pass — click to remove" : "Must pass"}
        onClick={
          canToggle
            ? (e) => {
                e.stopPropagation();
                onToggle(row.id);
              }
            : undefined
        }
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: "6px",
          flexShrink: 0,
          p: 0,
          m: 0,
          border: 0,
          bgcolor: "transparent",
          font: "inherit",
          cursor: canToggle ? "pointer" : "default",
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 2,
            borderRadius: "12px",
          },
        }}
      >
        <Box
          component="span"
          sx={{
            width: 24,
            height: 24,
            borderRadius: "50%",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: SUBTLE[mode],
            color: FG[mode],
            flexShrink: 0,
          }}
        >
          <Iconify icon="solar:shield-check-linear" width={14} />
        </Box>
        {label && (
          <Box
            component="span"
            sx={{
              fontSize: 12,
              fontWeight: 500,
              lineHeight: 1,
              color: FG[mode],
              whiteSpace: "nowrap",
            }}
          >
            Must pass
          </Box>
        )}
      </Box>
    </Tooltip>
  );
}

MustPassBadge.propTypes = {
  row: PropTypes.object.isRequired,
  onToggle: PropTypes.func,
  locked: PropTypes.bool,
  label: PropTypes.bool,
};
