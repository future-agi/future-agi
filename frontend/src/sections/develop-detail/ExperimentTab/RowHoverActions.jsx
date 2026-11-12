import React from "react";
import PropTypes from "prop-types";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { isGhostBaseline, useCell } from "./cellContext";
import RunChip from "src/sections/common/Runs/RunChip";
import { compareUrl, experimentUrl, runConfigLine } from "./leaderboardModel";
import { IterationNameCell } from "./LeaderboardCells";
import RunRowMenu from "./RunRowMenu";
import CompareWithPicker from "./CompareWithPicker";
import {
  sideBySideDisabledReason,
  sideBySideHref,
  sideBySidePartner,
  sideBySideLabel,
  sideBySideTitle,
} from "./sideBySide";

export { sideBySideHref, sideBySideDisabledReason } from "./sideBySide";

const btnSx = {
  height: 26,
  px: 1,
  minWidth: 0,
  typography: "s2",
  whiteSpace: "nowrap",
  bgcolor: "background.paper",
  "&:hover": { bgcolor: "background.paper" },
};

/**
 * EL#30 / MUST 5 / A2-12: row hover (and keyboard focus) shows `Open`,
 * `Side by side` and, for editors, `Pin as baseline  B` at the right edge
 * of the Experiment cell; the row's `⋯` and right-click hold the rest.
 * Critic r3 #4: they take their own space in the cell (collapsed to 0 px
 * until shown), so the name ends in an ellipsis before them and they never
 * sit on text. Hidden on group rows; the pinned baseline row adds
 * `Compare with previous (#1B)` (IT-R5 G2 P2).
 */
function RowHoverActions({ data, context, menu }) {
  const partner = sideBySidePartner(data, context);
  const side = sideBySideHref(data, context, partner);
  const sideWhy = sideBySideDisabledReason(data, context);
  const navigate = context.navigate;
  const open = experimentUrl(context.datasetId, data.experiment_id, {
    runId: data.run_id,
  });
  const isPinned = data.run_id === context.pinnedRunId;
  // IT-R5 G2 P2: the pinned baseline row is a door to its own previous run.
  const prev = isPinned ? context.numbers?.prevOf?.[data.run_id] : null;
  const prevHref = prev
    ? compareUrl(context.datasetId, {
        runIds: [prev.previous.run_id, data.run_id],
        baselineId: prev.previous.run_id,
        candId: data.run_id,
        runs: [prev.previous, data],
        from: context.fromUrl,
        tab: "data",
      })
    : null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      className={
        isPinned ? "lb-hover-actions lb-hover-base" : "lb-hover-actions"
      }
      data-row-link
      onClick={(e) => e.stopPropagation()}
      sx={{
        flexShrink: 0,
        height: "100%",
        // A narrow name cell keeps more of the name: `Side by side` shows
        // its icon only (the tooltip and aria-label keep the words).
        "@container lbname (max-width: 460px)": {
          "& .lb-act-label": { display: "none" },
          "& .lb-act-side .MuiButton-startIcon": { mr: 0, ml: 0 },
          "& .lb-act-side": { px: 0.75 },
        },
        // Collapsed until the row is hovered / focused (LeaderboardGrid
        // opens it). Still focusable, so Tab reveals it through
        // :focus-within.
        maxWidth: 0,
        overflow: "hidden",
        opacity: 0,
        pointerEvents: "none",
        transition: "opacity 120ms",
      }}
    >
      <CustomTooltip
        show
        title={`Open ${data.label}'s results`}
        size="small"
        arrow
      >
        <Button
          component={RouterLink}
          to={open}
          size="small"
          variant="outlined"
          color="inherit"
          sx={{ ...btnSx, borderColor: "divider" }}
        >
          Open
        </Button>
      </CustomTooltip>
      {prevHref ? (
        <CustomTooltip
          show
          title={`${data.label} next to its previous run ${prev.previous.label}, task by task`}
          size="small"
          arrow
        >
          <Button
            component={RouterLink}
            to={prevHref}
            size="small"
            variant="outlined"
            color="inherit"
            data-testid="baseline-compare-previous"
            sx={{ ...btnSx, borderColor: "divider" }}
          >
            {`Compare with previous (${prev.previous.label})`}
          </Button>
        </CustomTooltip>
      ) : null}
      {/* The baseline row keeps it short: side by side sits in its ⋯. */}
      {prevHref ? null : (
        <>
          <CustomTooltip
            show
            title={
              sideWhy ??
              `${sideBySideTitle(data, context, partner)}: step through both on the first task where they differ`
            }
            size="small"
            arrow
          >
            <span>
              <Button
                component={side ? RouterLink : "button"}
                to={side ?? undefined}
                disabled={!side}
                size="small"
                variant="outlined"
                color="primary"
                aria-label={
                  sideBySideTitle(data, context, partner) ??
                  `${sideBySideLabel(data)}: ${data.label}`
                }
                className="lb-act-side"
                startIcon={
                  <Iconify icon="mdi:view-split-vertical" width={14} />
                }
                sx={{ ...btnSx, "& .MuiButton-startIcon": { mr: 0.5 } }}
              >
                <span className="lb-act-label">{sideBySideLabel(data)}</span>
              </Button>
            </span>
          </CustomTooltip>
          {/* IT-R1 G2 P9: the split half — `Compare with…` any other run. */}
          {navigate ? (
            <CompareWithPicker
              run={data}
              runs={context.allRuns ?? []}
              partnerId={partner?.run_id}
              onPick={(other) => navigate(sideBySideHref(data, context, other))}
            />
          ) : null}
        </>
      )}
      {context.canEdit && !prevHref && (
        <CustomTooltip
          show
          title={
            isPinned
              ? `${data.label} is the pinned baseline`
              : `Pin ${data.label} as baseline  ·  B`
          }
          size="small"
          arrow
        >
          <span>
            <IconButton
              size="small"
              disabled={isPinned}
              aria-label={`Pin ${data.label} as baseline`}
              onClick={() => context.pinBaseline(data.run_id)}
              sx={{ bgcolor: "background.paper", width: 26, height: 26 }}
            >
              <Iconify icon="mdi:pin-outline" width={16} />
            </IconButton>
          </span>
        </CustomTooltip>
      )}
      {menu}
    </Stack>
  );
}

RowHoverActions.propTypes = {
  data: PropTypes.object,
  context: PropTypes.object.isRequired,
  menu: PropTypes.node,
};

/** EL#12: `#2B Support Agent v3 · pinned above`, dimmed, no actions. */
function GhostBaselineName({ data }) {
  return (
    <CustomTooltip
      show
      title={`${data.label} is the baseline. It is pinned at the top so every delta reads against it. Click to jump there.`}
      size="small"
      placement="top"
      arrow
      enterDelay={300}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        data-testid="lb-ghost-baseline"
        sx={{ minWidth: 0, height: "100%", opacity: 0.6 }}
      >
        <Box sx={{ flexShrink: 0, display: "inline-flex" }}>
          <RunChip run={data} showName={false} showStatus={false} />
        </Box>
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {runConfigLine(data)}
        </Typography>
        <Stack
          direction="row"
          alignItems="center"
          gap={0.25}
          sx={{ flexShrink: 0, color: "text.secondary", typography: "s3" }}
        >
          <Iconify icon="mdi:arrow-collapse-up" width={14} />
          pinned above
        </Stack>
      </Stack>
    </CustomTooltip>
  );
}

GhostBaselineName.propTypes = { data: PropTypes.object.isRequired };

/**
 * The Experiment column: the name cell + the hover actions; the row's `⋯`
 * (run or experiment menu) is the last hover action (A2-12), so the grid
 * needs no actions column.
 */
export default function NameCellWithActions(params) {
  const { node, data, context } = useCell(params);
  if (isGhostBaseline(node, data, context))
    return <GhostBaselineName data={data} />;
  // IT-R5 G2 P2: the pinned baseline row gets the same actions.
  const withActions = data && !node.group;
  const groupMenu = node.group && context.group === "experiment";
  return (
    <Box
      sx={{
        width: "100%",
        height: "100%",
        minWidth: 0,
        display: "flex",
        alignItems: "center",
        containerType: "inline-size",
        containerName: "lbname",
      }}
    >
      <Box sx={{ minWidth: 0, flex: 1, height: "100%", display: "flex" }}>
        <IterationNameCell {...params} />
      </Box>
      {withActions && (
        <RowHoverActions
          data={data}
          context={context}
          menu={<RunRowMenu {...params} />}
        />
      )}
      {groupMenu && (
        <Stack
          direction="row"
          alignItems="center"
          className="lb-hover-actions"
          data-row-link
          onClick={(e) => e.stopPropagation()}
          sx={{
            flexShrink: 0,
            height: "100%",
            maxWidth: 0,
            overflow: "hidden",
            opacity: 0,
            pointerEvents: "none",
            transition: "opacity 120ms",
          }}
        >
          <RunRowMenu {...params} />
        </Stack>
      )}
    </Box>
  );
}
