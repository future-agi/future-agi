import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  Build from scratch → Save and use (builder.md §5). The approved design is
  built and checked; the person reviews what it made and saves it as a
  private release in this workspace. Only then is an agent connected — saving
  never starts a run, and no sample agent stands in for theirs. Then the
  person chooses: "Save and connect my agent" saves and opens the Connect
  agent flow; "Save" alone leaves it for Overview (builder.md step 5).
*/
export default function SaveFirstRelease({
  env,
  envState,
  label,
  onSave,
  onSaveAndConnect,
}) {
  const tables = env?.seed?.tables || [];
  const rows = tables.reduce((n, t) => n + (t.rows || 0), 0);
  const scenarios = (envState?.scenarios || []).filter(
    (s) => !s.proposed && !s.archived,
  ).length;
  const people = (env?.actors || []).length;
  const list = [
    [
      "World",
      tables.length
        ? `${tables.length} tables · ${rows.toLocaleString()} seeded rows (${tables.map((t) => t.name).join(", ")})`
        : "No records — the description named none",
    ],
    [
      "Tools",
      (env?.tools || []).length
        ? `${env.tools.length} the world answers: ${env.tools.map((t) => t.name).join(", ")}`
        : "None yet",
    ],
    ["Rules", `${(env?.rules || []).length} graded on every run`],
    [
      "People",
      `${people || "No"} simulated ${people === 1 ? "person" : "people"} besides the caller`,
    ],
    ["Scenarios", `${scenarios} written and checked against this world`],
  ];
  return (
    <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          bgcolor: "background.paper",
          px: 2,
          py: 1.5,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
            Review and save
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            Built · not saved
          </Typography>
        </Stack>
        <Box
          component="table"
          sx={{ width: 1, borderCollapse: "collapse", mb: 1 }}
        >
          <Box component="tbody">
            {list.map(([k, v]) => (
              <Box component="tr" key={k}>
                <Box
                  component="td"
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    width: 84,
                    py: 0.25,
                    verticalAlign: "top",
                  }}
                >
                  {k}
                </Box>
                <Box
                  component="td"
                  sx={{ typography: "s2", color: "text.primary", py: 0.25 }}
                >
                  {v}
                </Box>
              </Box>
            ))}
          </Box>
        </Box>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {envState?.agent
              ? `Saving makes environment ${label}, private to this workspace. Run setup then checks it's ready for ${envState.agent.name}; nothing runs until you start it.`
              : `Saving makes environment ${label}, private to this workspace. Connect your agent from Overview to run it.`}
          </Typography>
          {onSaveAndConnect && (
            <Button
              size="small"
              variant="outlined"
              onClick={onSaveAndConnect}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              Save and connect my agent
            </Button>
          )}
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onSave}
            startIcon={<Iconify icon="solar:check-circle-linear" width={14} />}
          >
            {`Save environment ${label}`}
          </Button>
        </Stack>
      </Box>
    </Box>
  );
}
SaveFirstRelease.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  label: PropTypes.string,
  onSave: PropTypes.func.isRequired,
  /* Saves, then opens the Connect agent flow. */
  onSaveAndConnect: PropTypes.func,
};
