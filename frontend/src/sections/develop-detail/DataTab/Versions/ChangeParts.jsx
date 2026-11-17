import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { SplitChip } from "src/sections/common/Datasets/SplitChips";

// Shared rows of the Save version list and the Changes dialog, so a change
// reads the same before and after it is saved.

/** One change inside a row: label column, content, optional action. */
export const ChangeItem = ({
  label,
  children,
  onRevert,
  revertLabel = "Revert",
}) => (
  <Stack direction="row" gap={1.5} alignItems="flex-start" sx={{ py: 0.75 }}>
    <Typography
      typography="s2"
      color="text.secondary"
      sx={{ width: 148, flexShrink: 0, pt: 0.25, wordBreak: "break-word" }}
    >
      {label}
    </Typography>
    <Box sx={{ flex: 1, minWidth: 0, typography: "s2" }}>{children}</Box>
    {onRevert && (
      <Button
        size="small"
        color="inherit"
        onClick={onRevert}
        startIcon={<Iconify icon="mdi:undo-variant" width={16} />}
        sx={{ flexShrink: 0, color: "text.secondary", minWidth: 0, px: 1 }}
      >
        {revertLabel}
      </Button>
    )}
  </Stack>
);

ChangeItem.propTypes = {
  label: PropTypes.node,
  children: PropTypes.node,
  onRevert: PropTypes.func,
  revertLabel: PropTypes.string,
};

/** `+ hard  − test` with the split chips. */
export const SplitDelta = ({ add = [], remove = [], splitOf }) => (
  <Stack direction="row" gap={0.75} flexWrap="wrap" alignItems="center">
    {add.map((id) => (
      <Stack key={`a-${id}`} direction="row" gap={0.25} alignItems="center">
        <Typography typography="s2" color="green.600" fontWeight={600}>
          +
        </Typography>
        <SplitChip split={splitOf(id)} />
      </Stack>
    ))}
    {remove.map((id) => (
      <Stack key={`r-${id}`} direction="row" gap={0.25} alignItems="center">
        <Typography typography="s2" color="red.600" fontWeight={600}>
          −
        </Typography>
        <Box sx={{ textDecoration: "line-through", opacity: 0.7 }}>
          <SplitChip split={splitOf(id)} />
        </Box>
      </Stack>
    ))}
  </Stack>
);

SplitDelta.propTypes = {
  add: PropTypes.array,
  remove: PropTypes.array,
  splitOf: PropTypes.func.isRequired,
};

const KIND_STYLE = {
  added: { label: "Added", color: "green" },
  modified: { label: "Edited", color: "blue" },
  deleted: { label: "Deleted", color: "red" },
  new: { label: "New", color: "green" },
  splits: { label: "Splits", color: "orange" },
};

/** `Added` / `Edited` / `Deleted` tag in the change colours. */
export const KindBadge = ({ kind }) => {
  const s = KIND_STYLE[kind];
  if (!s) return null;
  return (
    <Box
      component="span"
      sx={{
        typography: "s3",
        fontWeight: 500,
        px: 0.75,
        lineHeight: "18px",
        borderRadius: 0.5,
        bgcolor: (t) => alpha(t.palette[s.color][500], 0.12),
        color: (t) =>
          t.palette.mode === "dark"
            ? t.palette[s.color][400]
            : t.palette[s.color][700],
        flexShrink: 0,
      }}
    >
      {s.label}
    </Box>
  );
};

KindBadge.propTypes = { kind: PropTypes.string };

/** Bordered scroll list of row groups. */
export const ChangeList = ({ children, maxHeight = 320 }) => (
  <Box
    sx={{
      border: "1px solid",
      borderColor: "divider",
      borderRadius: 1,
      maxHeight,
      overflowY: "auto",
      "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
    }}
  >
    {children}
  </Box>
);

ChangeList.propTypes = {
  children: PropTypes.node,
  maxHeight: PropTypes.number,
};

export const sectionLabelSx = {
  typography: "s3",
  fontWeight: "fontWeightMedium",
  color: "text.secondary",
  textTransform: "uppercase",
  letterSpacing: 0.4,
};
