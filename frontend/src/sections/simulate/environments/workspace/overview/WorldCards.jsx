import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { ROW_HEIGHT } from "src/theme/sizes";
import SectionCard from "../../components/SectionCard";
import MockBadge from "../../components/MockBadge";
import CodeBlock from "../world/CodeBlock";
import {
  ENV_SHAPE,
  OVERVIEW_COPY,
  startingCollections,
  worldTables,
} from "./overview.constants";

const MONO = "ui-monospace, Menlo, monospace";

// The seeded rows that fill the world before the agent arrives — rebuilt every
// task, so nothing carries over.

export function SeededDataCard({ env }) {
  const tables = worldTables(env);
  // A table the API sent without a row count adds nothing and shows no count.
  const hasRows = (t) => Number.isFinite(t.rows);
  const totalRows = tables.filter(hasRows).reduce((a, t) => a + t.rows, 0);
  return (
    <SectionCard
      dense
      title={OVERVIEW_COPY.seededTitle}
      subtitle={OVERVIEW_COPY.seedBlurb(totalRows)}
      action={env.provenance?.seedTables === "mock" ? <MockBadge /> : null}
    >
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {tables.map((t) => (
          <Stack
            key={t.name}
            direction="row"
            alignItems="center"
            spacing={2}
            sx={{ px: 1.5, minHeight: ROW_HEIGHT.twoLine }}
          >
            <Iconify
              icon="solar:database-linear"
              width={15}
              sx={{ color: "text.subtitle", flexShrink: 0 }}
            />
            <Box flex={1} minWidth={0}>
              <Typography
                sx={{
                  typography: "s1",
                  fontWeight: "fontWeightRegular",
                  fontFamily: MONO,
                }}
              >
                {t.name}
              </Typography>
              {t.note && (
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.subtitle" }}
                >
                  {t.note}
                </Typography>
              )}
            </Box>
            {hasRows(t) && (
              <Typography
                sx={{
                  typography: "s1",
                  fontWeight: "fontWeightMedium",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {t.rows.toLocaleString()}
              </Typography>
            )}
          </Stack>
        ))}
      </Stack>
    </SectionCard>
  );
}
SeededDataCard.propTypes = { env: ENV_SHAPE.isRequired };

const DEPENDENCY_SHAPE = PropTypes.arrayOf(
  PropTypes.shape({
    name: PropTypes.string,
    kind: PropTypes.string,
    provides: PropTypes.string,
    usedBy: PropTypes.oneOfType([
      PropTypes.string,
      PropTypes.arrayOf(PropTypes.string),
    ]),
  }),
);

export function DependsOnCard({ dependsOn, runtimeDependsOn }) {
  const groups = [
    { id: "built", label: OVERVIEW_COPY.dependsBuilt, rows: dependsOn || [] },
    {
      id: "runtime",
      label: OVERVIEW_COPY.dependsRuntime,
      rows: runtimeDependsOn || [],
    },
  ].filter((group) => group.rows.length);
  return (
    <SectionCard
      dense
      title={OVERVIEW_COPY.dependsTitle}
      subtitle={OVERVIEW_COPY.dependsSubtitle}
    >
      {groups.length === 0 ? (
        <Box sx={{ p: 1.5 }}>
          <Typography sx={{ typography: "s1", color: "text.subtitle" }}>
            {OVERVIEW_COPY.dependsEmpty}
          </Typography>
        </Box>
      ) : (
        groups.map((group, index) => (
          <Box
            key={group.id}
            role="group"
            aria-label={group.label}
            sx={{
              borderTop: index ? "1px solid" : "none",
              borderColor: "divider",
            }}
          >
            <Typography
              sx={{
                px: 1.5,
                py: 0.75,
                typography: "s3",
                fontWeight: 500,
                color: "text.tertiary",
                bgcolor: "background.neutral",
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              {group.label}
            </Typography>
            <DependencyRows rows={group.rows} />
          </Box>
        ))
      )}
    </SectionCard>
  );
}
DependsOnCard.propTypes = {
  dependsOn: DEPENDENCY_SHAPE,
  runtimeDependsOn: DEPENDENCY_SHAPE,
};

function DependencyRows({ rows }) {
  return (
    <Stack
      divider={
        <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
      }
    >
      {rows.map((d) => (
        <Box key={d.name} sx={{ px: 1.5, py: 1.5 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              sx={{
                typography: "s1",
                fontWeight: "fontWeightRegular",
                fontFamily: MONO,
              }}
            >
              {d.name}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {d.kind}
            </Typography>
          </Stack>
          <Typography
            sx={{ typography: "s1", color: "text.secondary", mt: 0.25 }}
          >
            {d.provides}
          </Typography>
          {d.usedBy?.length > 0 && (
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
          )}
        </Box>
      ))}
    </Stack>
  );
}
DependencyRows.propTypes = { rows: DEPENDENCY_SHAPE };

export function StartingDataCard({ baseEnvironment }) {
  const [open, setOpen] = useState(false);
  const collections = startingCollections(baseEnvironment);
  const code =
    typeof baseEnvironment === "string"
      ? baseEnvironment
      : JSON.stringify(baseEnvironment, null, 2);
  return (
    <SectionCard
      dense
      title={OVERVIEW_COPY.startingTitle}
      subtitle={OVERVIEW_COPY.startingSubtitle}
      action={
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          sx={{ flexShrink: 0 }}
        >
          {open ? OVERVIEW_COPY.startingHide : OVERVIEW_COPY.startingShow}
        </Button>
      }
    >
      {collections.length > 0 && (
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {collections.map((c) => (
            <Stack
              key={c.name}
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{ px: 1.5, minHeight: ROW_HEIGHT.default }}
            >
              <Iconify
                icon="solar:database-linear"
                width={15}
                sx={{ color: "text.subtitle", flexShrink: 0 }}
              />
              <Typography
                sx={{
                  typography: "s1",
                  fontFamily: MONO,
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {c.name}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {OVERVIEW_COPY.startingRows(c.rows)}
              </Typography>
            </Stack>
          ))}
        </Stack>
      )}
      <Collapse in={open} unmountOnExit>
        <Box
          sx={{
            p: 1.5,
            borderTop: collections.length ? "1px solid" : "none",
            borderColor: "divider",
          }}
        >
          <CodeBlock
            label={OVERVIEW_COPY.startingTitle}
            file={OVERVIEW_COPY.startingFile}
            code={code}
          />
        </Box>
      </Collapse>
    </SectionCard>
  );
}
StartingDataCard.propTypes = {
  baseEnvironment: PropTypes.oneOfType([
    PropTypes.object,
    PropTypes.array,
    PropTypes.string,
  ]).isRequired,
};
