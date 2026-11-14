import PropTypes from "prop-types";
import { useLocation } from "react-router-dom";
import { Box, Button, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import { heldToRows, stepRows } from "./fixRunView";

const NUM = { fontVariantNumeric: "tabular-nums" };

function StateIcon({ state }) {
  if (state === "now") {
    return (
      <Box
        sx={{
          width: 11,
          height: 11,
          borderRadius: "50%",
          flexShrink: 0,
          border: "1.5px solid",
          borderColor: "primary.main",
          borderTopColor: "transparent",
          animation: "fix-step-spin 0.7s linear infinite",
          "@keyframes fix-step-spin": { to: { transform: "rotate(360deg)" } },
        }}
      />
    );
  }
  return (
    <Iconify
      icon={
        state === "done" ? "solar:check-circle-linear" : "solar:circle-linear"
      }
      width={16}
      sx={{
        color: state === "done" ? "text.primary" : "text.disabled",
        flexShrink: 0,
      }}
    />
  );
}
StateIcon.propTypes = { state: PropTypes.string };

const iconCell = {
  width: 16,
  height: 20,
  display: "grid",
  placeItems: "center",
  flexShrink: 0,
};
const labelCell = {
  typography: "s2",
  width: 128,
  flexShrink: 0,
  lineHeight: "20px",
};

// The design's step list (ImprovementStages): one row per engine step, the
// current one spinning, then what the search is held to.
export default function FixSteps({ fix, runTestId }) {
  const location = useLocation();
  const rows = stepRows(fix);
  const sourceHref =
    runTestId && fix.source_run_id
      ? paths.dashboard.simulate.environments.execution(
          fix.environment_id,
          runTestId,
          fix.source_run_id,
        )
      : null;
  const heldTo = heldToRows(fix);
  return (
    <Box
      data-testid="fix-steps"
      sx={{
        mb: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
      }}
    >
      {rows.map((row, i) => {
        const quiet = row.state === "todo";
        const [text, ...subs] = row.lines;
        return (
          <Stack
            key={row.id}
            data-testid={`step-${row.id}`}
            data-state={row.state}
            direction="row"
            alignItems="flex-start"
            spacing={1.5}
            sx={{
              px: 2,
              py: 1,
              ...(i > 0 && { borderTop: "1px solid", borderColor: "divider" }),
            }}
          >
            <Box sx={iconCell}>
              <StateIcon state={row.state} />
            </Box>
            <Typography
              sx={{
                ...labelCell,
                fontWeight: quiet ? 500 : 600,
                color: quiet ? "text.subtitle" : "text.primary",
              }}
            >
              {row.label}
            </Typography>
            <Box flex={1} minWidth={0}>
              {text && (
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: quiet ? 400 : 600,
                    color: quiet ? "text.subtitle" : "text.primary",
                    lineHeight: "20px",
                    ...NUM,
                  }}
                >
                  {text}
                </Typography>
              )}
              {!quiet &&
                subs.map((sub) => (
                  <Typography
                    key={sub}
                    sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
                  >
                    {sub}
                  </Typography>
                ))}
            </Box>
            {row.id === "reproduce" &&
              sourceHref &&
              fix.source_run_number != null && (
                <Button
                  size="small"
                  component={RouterLink}
                  href={sourceHref}
                  state={{ backTo: location.pathname }}
                  endIcon={
                    <Iconify icon="solar:arrow-right-linear" width={14} />
                  }
                  sx={{ color: "text.secondary", flexShrink: 0, minWidth: 0 }}
                >
                  {`Environment Run ${fix.source_run_number}`}
                </Button>
              )}
          </Stack>
        );
      })}
      <Stack
        data-testid="held-to"
        direction="row"
        alignItems="flex-start"
        spacing={1.5}
        sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Box sx={iconCell}>
          <Iconify
            icon="solar:shield-check-linear"
            width={16}
            sx={{ color: "text.secondary" }}
          />
        </Box>
        <Typography sx={{ ...labelCell, fontWeight: 600 }}>Held to</Typography>
        <Stack
          direction="row"
          flexWrap="wrap"
          columnGap={2}
          rowGap={0.25}
          sx={{ flex: 1, minWidth: 0 }}
        >
          {heldTo.map((c) => (
            <Typography
              key={c.label}
              sx={{
                typography: "s2",
                color: "text.secondary",
                lineHeight: "20px",
              }}
            >
              {`${c.label} · `}
              <Box
                component="span"
                sx={{ color: "text.primary", fontWeight: 600 }}
              >
                {c.value}
              </Box>
            </Typography>
          ))}
        </Stack>
      </Stack>
    </Box>
  );
}

FixSteps.propTypes = {
  fix: PropTypes.object.isRequired,
  runTestId: PropTypes.string,
};
