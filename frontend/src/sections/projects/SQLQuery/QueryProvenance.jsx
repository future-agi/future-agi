import React from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import { describeInstant } from "src/utils/format-time";
import { ACTION_BUTTON_PROPS, ACTION_BUTTON_SX } from "./queryUiConfig";

export default function QueryProvenance({
  finishedAt,
  reused,
  onRefresh,
  disabled,
}) {
  const finished = describeInstant(finishedAt);
  if (!finished) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      flexWrap="wrap"
      gap={1}
      sx={{ px: 1.5, py: 0.75 }}
    >
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        {reused ? "Reused result · " : ""}Completed{" "}
        <time dateTime={finishedAt} title={`${finished.zone} · ${finishedAt}`}>
          {finished.local} {finished.offset}
        </time>
      </Typography>
      {reused && (
        <Button
          {...ACTION_BUTTON_PROPS}
          sx={ACTION_BUTTON_SX}
          onClick={onRefresh}
          disabled={disabled}
        >
          Run fresh
        </Button>
      )}
    </Stack>
  );
}

QueryProvenance.propTypes = {
  finishedAt: PropTypes.string,
  reused: PropTypes.bool,
  onRefresh: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
