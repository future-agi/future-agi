import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Chip, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import { ENV_FILES, FILE_KIND_COLOR } from "../_mock/envConfig";

/**
 * Files inside the environment.
 *
 * The useful question is not "what files exist" but "what did the agent
 * change" — so changed files are called out, and the default filter is the
 * whole tree with those marked rather than a flat listing you have to scan.
 */
export default function FilesPanel({ env }) {
  const [filter, setFilter] = useState("all");

  const changed = ENV_FILES.filter((f) => f.changed);
  const shown = filter === "changed" ? changed : ENV_FILES;

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 3 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>Files</Typography>
        <Typography sx={{ typography: "s1", color: "text.secondary", maxWidth: 760 }}>
          The filesystem inside {env.name}. With file tracking on, anything the agent changes
          during a run shows up here as a diff on the trace.
        </Typography>
      </Box>

      <SectionCard
        title={`Files (${ENV_FILES.length})`}
        subtitle={changed.length ? `${changed.length} changed in the last run` : "No changes in the last run"}
        action={
          <Stack direction="row" spacing={0.75}>
            {[
              { id: "all", label: `All ${ENV_FILES.length}` },
              { id: "changed", label: `Changed ${changed.length}` },
            ].map((f) => (
              <Chip
                key={f.id}
                size="small"
                label={f.label}
                onClick={() => setFilter(f.id)}
                sx={{
                  height: 24, borderRadius: 0.75,
                  border: "1px solid",
                  borderColor: filter === f.id ? "primary.main" : "divider",
                  color: filter === f.id ? "primary.main" : "text.secondary",
                  bgcolor: (t) => filter === f.id ? alpha(t.palette.primary.main, 0.08) : "transparent",
                  "& .MuiChip-label": { px: 1, typography: "s3", fontWeight: 600 },
                }}
              />
            ))}
          </Stack>
        }
      >
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {shown.map((f) => (
            <Stack key={f.path} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.375 }}>
              <Box
                sx={{
                  px: 0.75, height: 19, borderRadius: 0.5, display: "grid", placeItems: "center", flexShrink: 0,
                  color: FILE_KIND_COLOR[f.kind],
                  bgcolor: (t) => alpha(FILE_KIND_COLOR[f.kind], t.palette.mode === "dark" ? 0.16 : 0.1),
                }}
              >
                <Typography sx={{ typography: "s3", fontWeight: 700 }}>{f.kind}</Typography>
              </Box>
              <Typography
                noWrap
                sx={{ flex: 1, typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" }}
              >
                {f.path}
              </Typography>
              {f.changed && (
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <Iconify icon="solar:pen-new-square-linear" width={13} sx={{ color: "#EA580C" }} />
                  <Typography sx={{ typography: "s3", color: "#EA580C", fontWeight: 600 }}>
                    {f.diff}
                  </Typography>
                </Stack>
              )}
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", width: 70, textAlign: "right", flexShrink: 0 }}
              >
                {f.size}
              </Typography>
              <Button size="small" sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}>
                {f.changed ? "View diff" : "View"}
              </Button>
            </Stack>
          ))}
        </Stack>
      </SectionCard>
    </Box>
  );
}

FilesPanel.propTypes = { env: PropTypes.object.isRequired };
