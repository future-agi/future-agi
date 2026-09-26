import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import MockBadge from "../../components/MockBadge";
import { ENV_SHAPE, OVERVIEW_COPY } from "./overview.constants";

const MONO = "ui-monospace, Menlo, monospace";

// The seeded rows that fill the world before the agent arrives — rebuilt every
// task, so nothing carries over.
export function SeededDataCard({ env }) {
  const tables = env.seed?.tables || [];
  const totalRows = tables.reduce((a, t) => a + t.rows, 0);
  return (
    <SectionCard
      title={OVERVIEW_COPY.seededTitle}
      subtitle={OVERVIEW_COPY.seedBlurb(totalRows)}
      action={env.provenance?.seedTables === "mock" ? <MockBadge /> : null}
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {tables.map((t) => (
          <Stack key={t.name} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.25 }}>
            <Iconify icon="solar:database-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0 }} />
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", fontFamily: MONO }}>{t.name}</Typography>
              {t.note && <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{t.note}</Typography>}
            </Box>
            <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", fontVariantNumeric: "tabular-nums" }}>
              {t.rows.toLocaleString()}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </SectionCard>
  );
}
SeededDataCard.propTypes = { env: ENV_SHAPE.isRequired };

// The services built and torn down with the environment.
export function DependsOnCard({ dependsOn }) {
  const rows = dependsOn || [];
  return (
    <SectionCard title={OVERVIEW_COPY.dependsTitle} subtitle={OVERVIEW_COPY.dependsSubtitle}>
      {rows.length === 0 ? (
        <Box sx={{ p: 2.5 }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {OVERVIEW_COPY.dependsEmpty}
          </Typography>
        </Box>
      ) : (
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {rows.map((d) => (
          <Box key={d.name} sx={{ px: 2.5, py: 1.5 }}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", fontFamily: MONO }}>{d.name}</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{d.kind}</Typography>
            </Stack>
            <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>{d.provides}</Typography>
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                mt: 0.5,
                fontFamily: MONO,
                overflowWrap: "anywhere",
              }}
            >
              {OVERVIEW_COPY.usedBy(d.usedBy)}
            </Typography>
          </Box>
        ))}
      </Stack>
      )}
    </SectionCard>
  );
}
DependsOnCard.propTypes = {
  dependsOn: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      kind: PropTypes.string,
      provides: PropTypes.string,
      usedBy: PropTypes.string,
    })
  ),
};
