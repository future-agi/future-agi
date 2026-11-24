import PropTypes from "prop-types";
import { Box, Grid, Skeleton, Stack, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";
import { OVERVIEW_COPY } from "./overview.constants";
import { MAP_COPY } from "./sourceToSandbox.constants";
import StoppedNote from "../StoppedNote";

const WAIT = PropTypes.shape({
  stopped: PropTypes.bool,
  step: PropTypes.string,
  reason: PropTypes.string,
}).isRequired;

const Bar = ({ width, height = 12, sx }) => (
  <Skeleton variant="rounded" width={width} height={height} sx={sx} />
);

Bar.propTypes = {
  width: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  height: PropTypes.number,
  sx: PropTypes.object,
};

const rowDivider = (
  <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
);

export function FactSkeleton() {
  return <Bar width={56} height={14} sx={{ mt: 0.5 }} />;
}

export function CountsSkeleton({ wait }) {
  if (wait.stopped) return <StoppedNote wait={wait} sx={{ px: 0, mb: 1.5 }} />;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      data-testid="overview-counts-loading"
      sx={{ mb: 2.5, color: "text.disabled" }}
    >
      {[72, 88, 48, 52].map((width, index) => (
        <Stack key={width} direction="row" alignItems="center" spacing={1}>
          {index > 0 && <Box component="span">·</Box>}
          <Bar width={width} height={14} />
        </Stack>
      ))}
    </Stack>
  );
}

export function MapSkeleton({ wait }) {
  const columns = [
    MAP_COPY.groups.tools.label,
    MAP_COPY.groups.rules.label,
    MAP_COPY.groups.stores.label,
  ];
  return (
    <SectionCard
      title={MAP_COPY.title}
      subtitle={MAP_COPY.backedSubtitle}
      sx={{ mb: 3 }}
    >
      <Stack
        direction="row"
        divider={
          <Box sx={{ borderRight: "1px solid", borderColor: "divider" }} />
        }
        data-testid="overview-map-loading"
      >
        {columns.map((label, index) => (
          <Box key={label} sx={{ flex: 1, px: 1.5, py: 1.25, minWidth: 0 }}>
            <Typography sx={{ typography: "s2", fontWeight: 500 }}>
              {label}
            </Typography>
            {wait.stopped ? null : (
              <Bar width={[64, 56, 60][index]} sx={{ mt: 0.5 }} />
            )}
          </Box>
        ))}
      </Stack>
      {wait.stopped && (
        <StoppedNote
          wait={wait}
          sx={{ borderTop: "1px solid", borderColor: "divider" }}
        />
      )}
    </SectionCard>
  );
}

export function UseCasesSkeleton({ wait }) {
  return (
    <SectionCard title={OVERVIEW_COPY.useCasesTitle} sx={{ mb: 3 }}>
      {wait.stopped ? (
        <StoppedNote wait={wait} />
      ) : (
        <Stack
          spacing={1.25}
          sx={{ px: 2, py: 1.5 }}
          data-testid="overview-use-cases-loading"
        >
          {["62%", "48%", "70%", "41%"].map((width) => (
            <Stack key={width} direction="row" alignItems="center" spacing={1}>
              <Box
                sx={{
                  width: 4,
                  height: 4,
                  borderRadius: "50%",
                  bgcolor: "text.disabled",
                  flexShrink: 0,
                }}
              />
              <Bar width={width} />
            </Stack>
          ))}
        </Stack>
      )}
    </SectionCard>
  );
}

export function WorldSkeleton({ wait }) {
  return (
    <Grid
      container
      spacing={2}
      alignItems="flex-start"
      sx={{ mb: 3 }}
      data-testid="overview-world-loading"
    >
      <Grid item xs={12} md={7}>
        <SectionCard
          dense
          title={OVERVIEW_COPY.seededTitle}
          subtitle={
            wait.stopped ? null : (
              <Bar width={140} height={10} sx={{ mt: 0.5 }} />
            )
          }
        >
          {wait.stopped ? (
            <StoppedNote wait={wait} />
          ) : (
            <Stack divider={rowDivider}>
              {[
                ["32%", 40],
                ["26%", 32],
                ["38%", 44],
              ].map(([name, count]) => (
                <Stack
                  key={name}
                  direction="row"
                  alignItems="center"
                  justifyContent="space-between"
                  sx={{ px: 1.5, py: 1.25 }}
                >
                  <Bar width={name} />
                  <Bar width={count} />
                </Stack>
              ))}
            </Stack>
          )}
        </SectionCard>
      </Grid>
      <Grid item xs={12} md={5}>
        <SectionCard
          dense
          title={OVERVIEW_COPY.dependsTitle}
          subtitle={OVERVIEW_COPY.dependsBuilt}
        >
          {wait.stopped ? (
            <StoppedNote wait={wait} />
          ) : (
            <Stack divider={rowDivider}>
              {["54%", "40%"].map((width) => (
                <Box key={width} sx={{ px: 1.5, py: 1.25 }}>
                  <Bar width={width} />
                </Box>
              ))}
            </Stack>
          )}
        </SectionCard>
      </Grid>
    </Grid>
  );
}

CountsSkeleton.propTypes = { wait: WAIT };
MapSkeleton.propTypes = { wait: WAIT };
UseCasesSkeleton.propTypes = { wait: WAIT };
WorldSkeleton.propTypes = { wait: WAIT };
