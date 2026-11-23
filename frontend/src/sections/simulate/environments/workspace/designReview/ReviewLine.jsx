import PropTypes from "prop-types";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { REVIEW_COPY } from "./designReview.constants";

function LineAction({ icon, label, onClick, disabled = false }) {
  return (
    <Tooltip arrow title={label}>
      <span>
        <IconButton
          size="small"
          aria-label={label}
          onClick={onClick}
          disabled={disabled}
          sx={{
            p: 0.375,
            color: "text.tertiary",
            "&:hover": { color: "text.primary" },
          }}
        >
          <Iconify icon={icon} width={12} />
        </IconButton>
      </span>
    </Tooltip>
  );
}

LineAction.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

export default function ReviewLine({
  children,
  source = null,
  empty = false,
  onEdit,
  onRemove,
  removeLabel = REVIEW_COPY.remove,
  disabled = false,
}) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ px: 1.5, minHeight: 36, "&:hover": { bgcolor: "action.hover" } }}
    >
      <OverflowTooltip title={empty ? "" : children}>
        <Typography
          component="div"
          noWrap={!empty}
          sx={{
            typography: "s1",
            color: empty ? "text.tertiary" : "text.primary",
            flex: "0 1 auto",
            minWidth: 0,
          }}
        >
          {children}
        </Typography>
      </OverflowTooltip>
      <Box sx={{ flex: 1 }} />
      {source && (
        <OverflowTooltip title={source}>
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.tertiary",
              flexShrink: 0,
              pl: 1.5,
            }}
          >
            {source}
          </Typography>
        </OverflowTooltip>
      )}
      {(onEdit || onRemove) && (
        <Stack direction="row" sx={{ flexShrink: 0, ml: 1 }}>
          {onEdit && (
            <LineAction
              icon="solar:pen-linear"
              label={REVIEW_COPY.edit}
              onClick={onEdit}
              disabled={disabled}
            />
          )}
          {onRemove && (
            <LineAction
              icon="solar:trash-bin-minimalistic-linear"
              label={removeLabel}
              onClick={onRemove}
              disabled={disabled}
            />
          )}
        </Stack>
      )}
    </Stack>
  );
}

ReviewLine.propTypes = {
  children: PropTypes.node,
  source: PropTypes.string,
  empty: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  removeLabel: PropTypes.string,
  disabled: PropTypes.bool,
};
