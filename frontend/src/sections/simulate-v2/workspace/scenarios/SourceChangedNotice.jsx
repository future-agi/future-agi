import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useEnvState } from "../../store";
import { changedFileCiteOf } from "../../_mock/scenarioCitations";

/**
 * "Source changed", at the top of the scenario drawer: a file this case cites
 * has a newer version or Knowledge base revision waiting (roadmap builder.md
 * Week 2, "source-stale cases stay visible with repair actions").
 *
 * Only the headline lives here. The citation itself — old text next to new,
 * and Move to the new version / Keep this case on the old one / Review
 * refresh — is the Changed since row under Cites; Compare opens it.
 */
export default function SourceChangedNotice({ row, env, envState, onCompare }) {
  const store = useEnvState(env.id);
  const cite = changedFileCiteOf(row, {
    env,
    envState: envState || store.envState,
  });
  if (!cite) return null;
  return (
    <Box
      sx={{
        px: 2.5,
        py: 1.25,
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" spacing={0.75} alignItems="center">
        <Iconify
          icon="solar:danger-triangle-linear"
          width={14}
          sx={{ color: "warning.main", flexShrink: 0 }}
        />
        <Typography
          sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}
        >
          {`Source changed · ${cite.name}${cite.compare?.now ? ` ${cite.compare.now}` : ""}`}
        </Typography>
        {onCompare && (
          <Box
            component="button"
            type="button"
            onClick={() => onCompare(cite.key)}
            sx={{
              p: 0,
              border: 0,
              bgcolor: "transparent",
              cursor: "pointer",
              font: "inherit",
              typography: "s2",
              fontWeight: 600,
              color: "text.primary",
              whiteSpace: "nowrap",
              "&:hover": { textDecoration: "underline" },
            }}
          >
            Compare
          </Box>
        )}
      </Stack>
      {cite.note && (
        <Typography
          sx={{ typography: "s3", color: "text.secondary", mt: 0.25, pl: 2.6 }}
        >
          {cite.note}
        </Typography>
      )}
    </Box>
  );
}

SourceChangedNotice.propTypes = {
  row: PropTypes.object.isRequired,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  onCompare: PropTypes.func,
};
