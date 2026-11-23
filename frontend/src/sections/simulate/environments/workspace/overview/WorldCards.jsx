import PropTypes from "prop-types";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import { useState } from "react";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
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

function RowCount({ rows }) {
  const count = Number(rows) || 0;
  return (
    <Typography
      sx={{
        typography: "s2",
        fontWeight: count ? 600 : 400,
        color: count ? "text.primary" : "text.tertiary",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {count ? count.toLocaleString() : OVERVIEW_COPY.emptyTable}
    </Typography>
  );
}

RowCount.propTypes = {
  rows: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};

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
            sx={{ px: 2.5, py: 1 }}
          >
            <Iconify
              icon="solar:database-linear"
              width={16}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
            <Box flex={1} minWidth={0}>
              <Typography
                sx={{ typography: "s2", fontWeight: 500, fontFamily: MONO }}
              >
                {t.name}
              </Typography>
              {t.note && (
                <OverflowTooltip title={t.note}>
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {t.note}
                  </Typography>
                </OverflowTooltip>
              )}
            </Box>
            {hasRows(t) && <RowCount rows={t.rows} />}
          </Stack>
        ))}
        {tables.length === 0 && (
          <Typography
            sx={{ typography: "s2", color: "text.tertiary", px: 2.5, py: 1.25 }}
          >
            {OVERVIEW_COPY.noSeededData}
          </Typography>
        )}
      </Stack>
    </SectionCard>
  );
}
SeededDataCard.propTypes = { env: ENV_SHAPE.isRequired };

const USED_BY_SHOWN = 6;

function UsedBy({ names }) {
  const list = Array.isArray(names) ? names : [names];
  const shown = list.slice(0, USED_BY_SHOWN);
  const rest = list.slice(USED_BY_SHOWN);
  return (
    <>
      {OVERVIEW_COPY.usedBy(shown)}
      {rest.length > 0 && (
        <CustomTooltip show arrow size="small" title={rest.join(", ")}>
          <Box
            component="span"
            tabIndex={0}
            sx={{
              ml: 0.5,
              color: "text.secondary",
              textDecoration: "underline",
              textDecorationStyle: "dotted",
              cursor: "default",
            }}
          >
            {OVERVIEW_COPY.moreNames(rest.length)}
          </Box>
        </CustomTooltip>
      )}
    </>
  );
}

UsedBy.propTypes = {
  names: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.arrayOf(PropTypes.string),
  ]).isRequired,
};

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
  const split = groups.length > 1;
  return (
    <SectionCard
      dense
      title={OVERVIEW_COPY.dependsTitle}
      subtitle={
        split
          ? OVERVIEW_COPY.dependsSubtitle
          : groups[0]?.label || OVERVIEW_COPY.dependsBuilt
      }
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
            {split && (
              <Typography
                sx={{
                  px: 2.5,
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
            )}
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
        <Box key={d.name} sx={{ px: 2.5, py: 1 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              sx={{ typography: "s2", fontWeight: 500, fontFamily: MONO }}
            >
              {d.name}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {d.kind}
            </Typography>
          </Stack>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
          >
            {d.provides}
          </Typography>
          {d.usedBy?.length > 0 && (
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                mt: 0.5,
                fontFamily: MONO,
                overflowWrap: "anywhere",
              }}
            >
              <UsedBy names={d.usedBy} />
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
  const totalRows = collections.reduce(
    (total, c) => total + (Number(c.rows) || 0),
    0,
  );
  const code =
    typeof baseEnvironment === "string"
      ? baseEnvironment
      : JSON.stringify(baseEnvironment, null, 2);
  return (
    <SectionCard
      dense
      title={OVERVIEW_COPY.seededTitle}
      subtitle={OVERVIEW_COPY.seedBlurb(totalRows)}
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
              sx={{ px: 2.5, py: 1 }}
            >
              <Iconify
                icon="solar:database-linear"
                width={16}
                sx={{ color: "text.tertiary", flexShrink: 0 }}
              />
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 500,
                  fontFamily: MONO,
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {c.name}
              </Typography>
              <RowCount rows={c.rows} />
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
