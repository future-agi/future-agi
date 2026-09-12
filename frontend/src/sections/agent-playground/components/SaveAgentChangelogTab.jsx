import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Stack, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import { fToNow } from "src/utils/format-time";
import { NODE_TYPE_CONFIG, NODE_TYPES } from "../utils/constants";
import { CHANGE_STATUS, CHANGE_STATUS_LABEL } from "../utils/saveAgentDiff";

const STATUS_SX = {
  [CHANGE_STATUS.CREATED]: {
    color: "success.dark",
    bgcolor: "success.lighter",
  },
  [CHANGE_STATUS.UPDATED]: {
    color: "info.dark",
    bgcolor: "info.lighter",
  },
  [CHANGE_STATUS.DELETED]: {
    color: "error.dark",
    bgcolor: "error.lighter",
  },
  [CHANGE_STATUS.REROUTED]: {
    color: "primary.dark",
    bgcolor: "primary.lighter",
  },
  [CHANGE_STATUS.UNCHANGED]: {
    color: "text.secondary",
    bgcolor: "action.hover",
  },
};

function nodeIconSrc(type) {
  if (type === NODE_TYPES.AGENT || type === "subgraph") {
    return NODE_TYPE_CONFIG[NODE_TYPES.AGENT].iconSrc;
  }
  return NODE_TYPE_CONFIG[NODE_TYPES.LLM_PROMPT].iconSrc;
}

export default function SaveAgentChangelogTab({ entries = [] }) {
  if (entries.length === 0) {
    return (
      <Typography
        variant="body2"
        color="text.secondary"
        data-testid="save-changelog-empty"
      >
        Nothing to compare yet. This save will create the first version.
      </Typography>
    );
  }

  return (
    <Stack
      spacing={1.5}
      data-testid="save-changelog-list"
      sx={{ maxHeight: 320, overflowY: "auto", pr: 0.5 }}
    >
      {entries.map((entry) => {
        const statusSx =
          STATUS_SX[entry.status] || STATUS_SX[CHANGE_STATUS.UNCHANGED];
        const timeLabel = entry.occurredAt ? fToNow(entry.occurredAt) : "";
        return (
          <Stack
            key={`${entry.status}-${entry.id || entry.name}`}
            direction="row"
            spacing={1.5}
            alignItems="flex-start"
            data-testid={`save-changelog-row-${entry.name}`}
            data-status={entry.status}
          >
            <Box
              sx={{
                width: 32,
                height: 32,
                borderRadius: 1,
                bgcolor: "action.hover",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <SvgColor
                src={nodeIconSrc(entry.type)}
                sx={{ width: 18, height: 18 }}
              />
            </Box>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Stack
                direction="row"
                spacing={1}
                alignItems="center"
                flexWrap="wrap"
              >
                <Typography variant="subtitle2" noWrap>
                  {entry.name}
                </Typography>
                <Chip
                  size="small"
                  label={CHANGE_STATUS_LABEL[entry.status] || entry.status}
                  data-testid={`save-changelog-badge-${entry.name}`}
                  sx={{
                    height: 20,
                    fontSize: 11,
                    fontWeight: 600,
                    ...statusSx,
                    "& .MuiChip-label": { px: 0.75 },
                  }}
                />
                {timeLabel ? (
                  <Typography variant="caption" color="text.secondary">
                    {timeLabel}
                  </Typography>
                ) : null}
              </Stack>
              {entry.description ? (
                <Typography variant="caption" color="text.secondary">
                  {entry.description}
                </Typography>
              ) : null}
            </Box>
          </Stack>
        );
      })}
    </Stack>
  );
}

SaveAgentChangelogTab.propTypes = {
  entries: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string.isRequired,
      type: PropTypes.string,
      status: PropTypes.string.isRequired,
      description: PropTypes.string,
      occurredAt: PropTypes.string,
    }),
  ),
};
