import PropTypes from "prop-types";
import {
  Box,
  Stack,
  Typography,
  Button,
  Chip,
  IconButton,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { MONO, valueSx } from "./configurationStyles";

const CHECK_LABEL = {
  accepted: "Accepted",
  rejected: "Rejected",
  not_checked: "Not checked",
};
const CHECK_COLOR = {
  accepted: "success",
  rejected: "error",
  not_checked: "warning",
};

export function KindChip({ label }) {
  return (
    <Chip
      size="small"
      label={label}
      sx={{
        height: 19,
        borderRadius: 0.5,
        color: "text.secondary",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "transparent",
        "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
      }}
    />
  );
}
KindChip.propTypes = { label: PropTypes.string.isRequired };

export function RowAction({ icon, label, onClick }) {
  return (
    <Tooltip title={label}>
      <IconButton size="small" aria-label={label} onClick={onClick}>
        <Iconify icon={icon} width={15} sx={{ color: "text.subtitle" }} />
      </IconButton>
    </Tooltip>
  );
}
RowAction.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func.isRequired,
};

export function RebuildOnlyAction({ name }) {
  return (
    <Tooltip
      arrow
      placement="left"
      title={
        <Box sx={{ p: 0.5, maxWidth: 240 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Needs a rebuild
          </Typography>
          <Typography sx={{ typography: "s3" }}>
            Changing {name} requires a rebuild of the environment, so it
            can&apos;t be edited here yet.
          </Typography>
        </Box>
      }
    >
      <Box component="span" sx={{ display: "inline-flex" }}>
        <IconButton size="small" aria-label={`Edit ${name}`} disabled>
          <Iconify
            icon="solar:pen-2-linear"
            width={15}
            sx={{ color: "text.disabled" }}
          />
        </IconButton>
      </Box>
    </Tooltip>
  );
}
RebuildOnlyAction.propTypes = { name: PropTypes.string.isRequired };

export function ValueText({ text }) {
  return (
    <Tooltip title={text} placement="bottom-start" enterDelay={400}>
      <Typography noWrap sx={valueSx}>
        {text}
      </Typography>
    </Tooltip>
  );
}
ValueText.propTypes = { text: PropTypes.string.isRequired };

function Slot({ children }) {
  return (
    <Box sx={{ width: 30, display: "flex", justifyContent: "center" }}>
      {children}
    </Box>
  );
}
Slot.propTypes = { children: PropTypes.node };

export function VariableRow({ name, children, chip, first, second }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ px: 2.5, py: 1.375, minHeight: 56 }}
    >
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          fontFamily: MONO,
          width: 220,
          flexShrink: 0,
          overflowWrap: "anywhere",
        }}
      >
        {name}
      </Typography>
      {children}
      {chip}
      <Stack direction="row" sx={{ flexShrink: 0 }}>
        <Slot>{first}</Slot>
        <Slot>{second}</Slot>
      </Stack>
    </Stack>
  );
}
VariableRow.propTypes = {
  name: PropTypes.node.isRequired,
  children: PropTypes.node,
  chip: PropTypes.node,
  first: PropTypes.node,
  second: PropTypes.node,
};

export function SaveFooter({
  canSave,
  pending,
  onSave,
  hint,
  error,
  checks = [],
}) {
  return (
    <Stack
      spacing={1}
      sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Button
          variant="contained"
          size="small"
          onClick={onSave}
          disabled={!canSave}
        >
          {pending ? "Checking and saving…" : "Save changes"}
        </Button>
        {hint && (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {hint}
          </Typography>
        )}
      </Stack>
      {error && (
        <Typography role="alert" sx={{ typography: "s2", color: "error.main" }}>
          {error}
        </Typography>
      )}
      {checks.map((check) => (
        <Stack
          key={`${check.label}:${check.aliases.join(",")}`}
          direction="row"
          alignItems="center"
          spacing={1}
        >
          <Chip
            size="small"
            color={CHECK_COLOR[check.status] || "default"}
            label={CHECK_LABEL[check.status] || check.status}
          />
          <Typography sx={{ typography: "s2" }}>{check.message}</Typography>
        </Stack>
      ))}
    </Stack>
  );
}
SaveFooter.propTypes = {
  canSave: PropTypes.bool,
  pending: PropTypes.bool,
  onSave: PropTypes.func.isRequired,
  hint: PropTypes.string,
  error: PropTypes.string,
  checks: PropTypes.array,
};
