// W5-7 (C3): the selection count of a results bar, with the
// `Select all N matching rows` offer when the view has more rows than the
// grid loaded (state from useSelectAllMatching).
import React from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";

const SelectionCount = ({ state }) => (
  <Stack
    direction="row"
    alignItems="center"
    gap={0.5}
    sx={{ flexShrink: 0, pl: 1 }}
    data-testid="selection-count"
  >
    <Typography
      typography="s2"
      color="primary.main"
      fontWeight={500}
      noWrap
      title={state.title ?? undefined}
      aria-label={state.title ?? state.text}
    >
      {state.text}
    </Typography>
    {state.offer && (
      <Button
        size="small"
        variant="text"
        disabled={state.busy}
        onClick={state.selectAll}
        data-testid="select-all-matching"
        title={state.offer.text}
        aria-label={state.offer.text}
        sx={{ whiteSpace: "nowrap", minWidth: 0 }}
      >
        {state.busy
          ? "Selecting…"
          : `Select all ${state.offer.total.toLocaleString("en-US")}`}
      </Button>
    )}
    {state.error && (
      <Typography typography="s3" color="error.main" noWrap role="alert">
        {state.error}
      </Typography>
    )}
  </Stack>
);

SelectionCount.propTypes = {
  state: PropTypes.shape({
    text: PropTypes.string.isRequired,
    title: PropTypes.string,
    offer: PropTypes.shape({ total: PropTypes.number, text: PropTypes.string }),
    selectAll: PropTypes.func,
    busy: PropTypes.bool,
    error: PropTypes.string,
  }).isRequired,
};

export default SelectionCount;
