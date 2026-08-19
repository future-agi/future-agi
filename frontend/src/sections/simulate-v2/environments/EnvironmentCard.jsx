import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { getSurface } from "../_mock/surfaces";
import { packStats } from "../_mock/scenarios";

/**
 * One environment in the gallery.
 *
 * Deliberately quiet: icon, name, one line of what it is, and a thin line of
 * what ships with it. The gallery is a list you scan, not a set of posters —
 * the detail lives one click in, on the environment's own overview.
 */
export default function EnvironmentCard({ env, onOpen }) {
  const surface = getSurface(env.surface);
  const rows = env.seed?.tables?.reduce((a, t) => a + t.rows, 0) || 0;
  const stats = packStats(env);

  return (
    <Box
      onClick={() => onOpen?.(env)}
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        p: 2,
        borderRadius: 1.5,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        cursor: "pointer",
        transition: "border-color .16s ease, background-color .16s ease",
        "&:hover": { borderColor: "text.disabled", bgcolor: "action.hover" },
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
        <Box
          sx={{
            width: 34, height: 34, borderRadius: 1, flexShrink: 0,
            display: "grid", placeItems: "center",
            color: "text.secondary",
            bgcolor: "background.neutral",
          }}
        >
          <Iconify icon={surface.icon} width={18} />
        </Box>

        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s1", fontWeight: 600 }}>
            {env.name}
          </Typography>

          <Typography
            sx={{
              typography: "s2", color: "text.subtitle", mt: 0.25,
              display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {env.description}
          </Typography>

          {/* What ships with it — one quiet line, no chips. */}
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1.25 }}>
            {rows.toLocaleString()} seed rows · {stats.scenarios} scenarios ·{" "}
            {env.difficulty}
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}

EnvironmentCard.propTypes = {
  env: PropTypes.object.isRequired,
  onOpen: PropTypes.func,
};
