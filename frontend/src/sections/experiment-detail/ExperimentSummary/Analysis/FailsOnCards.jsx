import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Link, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { useMatrix, useRunConfigs } from "src/api/develop/r2";
import { useRootCauses } from "src/sections/experiment-detail/matrix/useMatrixData";
import { rowsPill } from "src/sections/common/Results/filterPills";
import { fmtCount, NUM_SX } from "src/sections/common/format/fmt";
import Section from "src/sections/common/ux/oneview/Section";
import { agentShortName } from "src/sections/experiment-detail/matrix/matrixModel";
import { failsOnModel } from "./failsOnModel";
import { RULES_METHOD_LABEL } from "src/sections/experiment-detail/results/causeMethod";

const cardSx = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
  p: 1.25,
  minWidth: 0,
  display: "flex",
  flexDirection: "column",
  gap: 0.75,
};

const CountLink = ({ href, children, testId }) => (
  <Link
    component={RouterLink}
    to={href}
    underline="hover"
    typography="s2"
    sx={{ ...NUM_SX, whiteSpace: "nowrap" }}
    data-testid={testId}
  >
    {children}
  </Link>
);
CountLink.propTypes = {
  href: PropTypes.string,
  children: PropTypes.node,
  testId: PropTypes.string,
};

/**
 * 8c: `Fails on` — one card per run with the categories and
 * tasks it fails most (counts open exactly those rows in the grid) and
 * the top rule-based cause for that run (`Rule-based: …`). A section that
 * opens as one summary line.
 */
export default function FailsOnCards({
  datasetId,
  runIds,
  baselineId,
  pillHref,
}) {
  const enabled = (runIds ?? []).length > 0;
  const q = useMatrix(
    { datasetId, runIds, baselineId, measure: "pass_at_1" },
    { enabled },
  );
  const roots = useRootCauses({ datasetId, runIds, baselineId });
  const configs = useRunConfigs(datasetId, { enabled });
  const runsById = useMemo(
    () =>
      Object.fromEntries(
        (configs.data?.results ?? []).map((r) => [r.run_id, r]),
      ),
    [configs.data],
  );
  const cards = useMemo(
    () =>
      q.data
        ? failsOnModel(q.data, {
            runIds,
            causes: roots.data?.causes ?? [],
          }).filter((c) => c.failing > 0)
        : [],
    [q.data, runIds, roots.data],
  );
  if (!cards.length) return null;
  const nameOf = (id) =>
    [
      runsById[id]?.label ?? q.data?.columns?.[id]?.label,
      runsById[id] ? agentShortName(runsById[id]) : null,
    ]
      .filter(Boolean)
      .join(" ");
  const worst = [...cards].sort((a, b) => b.failing - a.failing)[0];
  const summary = `${nameOf(worst.runId)} fails the most: ${fmtCount(worst.failing, "task")} of ${worst.total}${
    worst.categories[0] ? `, mostly ${worst.categories[0].name}` : ""
  }`;
  const rowsHref = (ids, label) => pillHref(rowsPill(ids, { label }));

  return (
    <Section
      id="fails-on"
      surface="summary"
      title="Fails on"
      summary={summary}
      // IT-R4 G6 P5 (budget at 1920): folded to its one-line answer by
      // default; the viewer's choice is remembered.
      defaultOpen={false}
    >
      <Box
        data-testid="fails-on"
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
          gap: 1,
        }}
      >
        {cards.map((c) => {
          const name = nameOf(c.runId);
          return (
            <Box key={c.runId} sx={cardSx} data-testid={`fails-on-${c.runId}`}>
              <Stack direction="row" alignItems="baseline" gap={1}>
                <Typography
                  typography="s1"
                  fontWeight="fontWeightSemiBold"
                  noWrap
                  sx={{ minWidth: 0, flex: 1 }}
                  title={name}
                >
                  {name}
                </Typography>
                <CountLink
                  href={rowsHref(c.failingIds, `${name} fails`)}
                  testId="fails-on-total"
                >
                  {`${fmtCount(c.failing, "task")} of ${c.total}`}
                </CountLink>
              </Stack>
              {c.categories.length > 0 && (
                <Stack gap={0.25}>
                  {c.categories.map((cat) => (
                    <Stack
                      key={cat.name}
                      direction="row"
                      justifyContent="space-between"
                      gap={1}
                    >
                      <Typography typography="s2" color="text.secondary" noWrap>
                        {cat.name}
                      </Typography>
                      <CountLink
                        href={rowsHref(
                          cat.rowIds,
                          `${name} fails · ${cat.name}`,
                        )}
                      >
                        {fmtCount(cat.count, "task")}
                      </CountLink>
                    </Stack>
                  ))}
                </Stack>
              )}
              {c.tasks.length > 0 && (
                <Typography typography="s2" color="text.secondary" noWrap>
                  {"Passes least: "}
                  {c.tasks.map((t, i) => (
                    <React.Fragment key={t.id}>
                      {i > 0 ? " · " : ""}
                      <CountLink
                        href={rowsHref([t.id], `${name} · ${t.label}`)}
                      >
                        {`${t.label ?? "Row"} ${t.passed}/${t.tries}`}
                      </CountLink>
                    </React.Fragment>
                  ))}
                </Typography>
              )}
              {c.cause && (
                <Typography typography="s2" sx={{ minWidth: 0 }}>
                  {c.cause.title}
                  <Typography
                    component="span"
                    typography="s3"
                    color="text.disabled"
                  >
                    {` · ${c.cause.status_label ?? RULES_METHOD_LABEL}`}
                  </Typography>
                </Typography>
              )}
            </Box>
          );
        })}
      </Box>
    </Section>
  );
}

FailsOnCards.propTypes = {
  datasetId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  pillHref: PropTypes.func.isRequired,
};
