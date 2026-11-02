import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";

const BRANDS = {
  github: { icon: "logos:github-icon", dark: "simple-icons:github" },
  slack: { icon: "logos:slack-icon" },
  /* Coding agents. Single-colour marks follow the text colour, so they
     hold up on both themes without a dark variant. */
  claude_code: { icon: "logos:claude-icon" },
  codex: { icon: "simple-icons:openai", mono: true },
  cursor: { icon: "simple-icons:cursor", mono: true },
  /* Cloud coding agents — the same vendor marks; Devin has no common one. */
  claude_code_web: { icon: "logos:claude-icon" },
  codex_cloud: { icon: "simple-icons:openai", mono: true },
  cursor_background: { icon: "simple-icons:cursor", mono: true },
  devin: { icon: "solar:cloud-linear", mono: true, muted: true },
  "coding-agents": { icon: "solar:code-linear", mono: true, muted: true },
};

/** A workspace tool's mark on the same neutral tile the other integrations use. */
export default function BrandMark({ brand, size = 40, tile = true }) {
  const theme = useTheme();
  const b = BRANDS[brand];
  const dark = theme.palette.mode === "dark";
  const icon = dark && b?.dark ? b.dark : b?.icon;
  const mark = (
    <Iconify
      icon={icon}
      width={Math.round(size * 0.55)}
      sx={{
        color: b?.muted
          ? "text.secondary"
          : b?.mono
            ? "text.primary"
            : dark && b?.dark
              ? "#fff"
              : undefined,
        flexShrink: 0,
      }}
    />
  );
  if (!tile) return mark;
  return (
    <Box
      sx={{
        width: size,
        height: size,
        borderRadius: 1.25,
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: alpha(theme.palette.text.primary, dark ? 0.04 : 0.02),
      }}
    >
      {mark}
    </Box>
  );
}

BrandMark.propTypes = {
  brand: PropTypes.oneOf(Object.keys(BRANDS)).isRequired,
  size: PropTypes.number,
  tile: PropTypes.bool,
};
