import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { REVIEW_COPY } from "./designReview.constants";

function LineAction({ icon, label, onClick, disabled }) {
  return (
    <Tooltip arrow title={label}>
      <span>
        <IconButton
          size="small"
          aria-label={label}
          onClick={onClick}
          disabled={disabled}
          sx={{
            p: 0.5,
            color: "text.tertiary",
            "&:hover": { color: "text.primary" },
          }}
        >
          <Iconify icon={icon} width={14} />
        </IconButton>
      </span>
    </Tooltip>
  );
}

LineAction.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
};

export default function ReviewLine({
  primary,
  secondary,
  aside,
  removed = false,
  edited = false,
  removeLabel,
  onRemove,
  onEdit,
  onUndo,
  disabled = false,
}) {
  const changed = removed || edited;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 1.5,
        py: 0.75,
        minHeight: 36,
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          noWrap
          sx={{
            typography: "s1",
            color: removed ? "text.tertiary" : "text.primary",
            textDecoration: removed ? "line-through" : "none",
          }}
        >
          {primary}
        </Typography>
        {secondary && (
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {secondary}
          </Typography>
        )}
      </Box>
      {aside && !removed && (
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flexShrink: 0,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {aside}
        </Typography>
      )}
      {changed && (
        <Typography
          sx={{ typography: "s3", color: "text.secondary", flexShrink: 0 }}
        >
          {removed ? REVIEW_COPY.removed : REVIEW_COPY.edited}
        </Typography>
      )}
      {changed && onUndo && (
        <Button
          size="small"
          variant="text"
          onClick={onUndo}
          disabled={disabled}
          sx={{ color: "text.primary", flexShrink: 0 }}
        >
          {REVIEW_COPY.undo}
        </Button>
      )}
      {!removed && onEdit && (
        <LineAction
          icon="solar:pen-2-linear"
          label={REVIEW_COPY.edit}
          onClick={onEdit}
          disabled={disabled}
        />
      )}
      {!removed && onRemove && (
        <LineAction
          icon="solar:trash-bin-minimalistic-linear"
          label={removeLabel}
          onClick={onRemove}
          disabled={disabled}
        />
      )}
    </Stack>
  );
}

ReviewLine.propTypes = {
  primary: PropTypes.node.isRequired,
  secondary: PropTypes.node,
  aside: PropTypes.node,
  removed: PropTypes.bool,
  edited: PropTypes.bool,
  removeLabel: PropTypes.string,
  onRemove: PropTypes.func,
  onEdit: PropTypes.func,
  onUndo: PropTypes.func,
  disabled: PropTypes.bool,
};
