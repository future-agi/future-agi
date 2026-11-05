import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import { simSitesOf } from "../_mock/simSites";

/*
  The simulated websites this environment's browser works on (T47). We run
  them: each resets to its seed before every trial and records what the
  agent changed, so checks read the site's state. They are always allowed.
*/
export default function SimulatedSitesSection({ env }) {
  const sites = simSitesOf(env?.templateId || env?.id);
  if (!sites.length) return null;
  return (
    <SectionCard
      title={`Simulated websites (${sites.length})`}
      subtitle="We run these sites. Each resets before every trial and records what the agent changed; checks read that state."
      sx={{ mb: 2 }}
    >
      <Box>
        {sites.map((s) => (
          <Stack
            key={s.host}
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={{
              px: 2.5,
              py: 0.875,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Iconify
              icon="solar:monitor-linear"
              width={16}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
            <Typography
              sx={{
                typography: "s2",
                fontFamily: "ui-monospace, Menlo, monospace",
                width: 190,
                flexShrink: 0,
              }}
            >
              {s.host}
            </Typography>
            <Typography
              sx={{ typography: "s2", color: "text.secondary", flex: 1 }}
            >
              {`${s.name} · ${s.kind} · records ${s.records.join(", ")}`}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {`${s.pages} ${s.pages === 1 ? "page" : "pages"} · resets each trial`}
            </Typography>
          </Stack>
        ))}
      </Box>
    </SectionCard>
  );
}
SimulatedSitesSection.propTypes = { env: PropTypes.object };
