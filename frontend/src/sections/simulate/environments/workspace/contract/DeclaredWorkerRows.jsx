import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { WORKERS_COPY } from "./workers.constants";

const CHIP_SX = {
  px: 0.75,
  height: 18,
  borderRadius: "4px",
  color: "text.secondary",
  bgcolor: "action.selected",
  typography: "s3",
  fontWeight: 500,
  fontFamily: "ui-monospace, Menlo, monospace",
  lineHeight: "18px",
  whiteSpace: "nowrap",
};

/** The app's declared workers as served: name, channel, own process and the tools it may use. Read-only. */
export default function DeclaredWorkerRows({ workers }) {
  return (
    <Stack
      divider={
        <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
      }
      data-testid="declared-workers"
    >
      {workers.map((worker) => (
        <Stack
          key={worker.id}
          spacing={0.5}
          sx={{ px: 1.5, py: 1 }}
          data-testid="declared-worker"
        >
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Iconify
              icon="solar:users-group-rounded-linear"
              width={15}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s1", fontWeight: 500 }}>
              {worker.name || worker.id}
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}
            >
              {[
                WORKERS_COPY.binding[worker.binding],
                worker.process ? WORKERS_COPY.ownProcess : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          </Stack>
          {worker.tools_shown.length > 0 && (
            <Stack
              direction="row"
              spacing={0.5}
              flexWrap="wrap"
              useFlexGap
              sx={{ pl: 3.5 }}
            >
              {worker.tools_shown.map((tool) => (
                <Box key={tool} component="span" sx={CHIP_SX}>
                  {tool}
                </Box>
              ))}
            </Stack>
          )}
        </Stack>
      ))}
    </Stack>
  );
}

DeclaredWorkerRows.propTypes = {
  workers: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      name: PropTypes.string,
      tools_shown: PropTypes.arrayOf(PropTypes.string).isRequired,
      binding: PropTypes.oneOf(["separate", "shared", "not_declared"])
        .isRequired,
      process: PropTypes.string,
    }),
  ).isRequired,
};
