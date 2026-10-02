import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Divider,
  FormControlLabel,
  IconButton,
  Link,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { paths } from "src/routes/paths";
import {
  COLUMNS_POPOVER_ID,
  PICKER_SEARCH_THRESHOLD,
  customNameFromId,
} from "./columnModel";

function matches(label, query) {
  return label.toLowerCase().includes(query);
}

const ColumnEntryRow = ({ entry, onToggle, onMove }) => (
  <Stack
    direction="row"
    alignItems="center"
    sx={{
      px: 1,
      py: 0.25,
      borderRadius: "4px",
      "&:hover": { backgroundColor: "action.hover" },
      opacity: entry.locked ? 0.6 : 1,
    }}
  >
    <FormControlLabel
      sx={{ mx: 0, flex: 1, minWidth: 0 }}
      control={
        <Checkbox
          size="small"
          checked={entry.checked}
          disabled={entry.locked}
          onChange={() => onToggle(entry.id)}
          inputProps={{
            "aria-label": entry.locked
              ? `${entry.label} (always visible)`
              : entry.label,
          }}
          checkedIcon={<Iconify icon="mdi:checkbox-marked" width={20} />}
          icon={
            <Iconify
              icon="mdi:checkbox-blank-outline"
              width={20}
              sx={{ color: "text.disabled" }}
            />
          }
          sx={{ p: 0.5 }}
        />
      }
      label={
        <Typography
          variant="body2"
          noWrap
          sx={{ fontSize: "13px", ml: 0.5 }}
          title={entry.label}
        >
          {entry.label}
          {entry.locked && (
            <Iconify
              icon="mdi:lock-outline"
              width={12}
              sx={{
                ml: 0.5,
                verticalAlign: "text-bottom",
                color: "text.disabled",
              }}
            />
          )}
        </Typography>
      }
    />
    {!entry.locked && (
      <Stack direction="row" spacing={0}>
        <IconButton
          size="small"
          aria-label={`Move ${entry.label} up`}
          disabled={!entry.canMoveUp}
          onClick={() => onMove(entry.id, -1)}
        >
          <Iconify icon="mdi:chevron-up" width={18} />
        </IconButton>
        <IconButton
          size="small"
          aria-label={`Move ${entry.label} down`}
          disabled={!entry.canMoveDown}
          onClick={() => onMove(entry.id, 1)}
        >
          <Iconify icon="mdi:chevron-down" width={18} />
        </IconButton>
      </Stack>
    )}
  </Stack>
);

ColumnEntryRow.propTypes = {
  entry: PropTypes.shape({
    id: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
    checked: PropTypes.bool.isRequired,
    locked: PropTypes.bool,
    canMoveUp: PropTypes.bool,
    canMoveDown: PropTypes.bool,
  }).isRequired,
  onToggle: PropTypes.func.isRequired,
  onMove: PropTypes.func.isRequired,
};

const GroupHeading = ({ children }) => (
  <Typography
    variant="caption"
    color="text.secondary"
    sx={{ px: 1, pt: 1, pb: 0.5, display: "block", fontSize: "11px" }}
  >
    {children}
  </Typography>
);

GroupHeading.propTypes = { children: PropTypes.node.isRequired };

/**
 * Columns picker for the Request Logs table. Clone of the house
 * PersonasColumnsPopover pattern with reorder, a custom-properties group,
 * declaration states and a stale group (D6, D11, D13, R34-R42).
 */
const ColumnsPickerPopover = ({
  anchorEl,
  open,
  onClose,
  entries,
  stale,
  visibleCount,
  totalCount,
  declarationStatus,
  unavailableCustomCount,
  onToggle,
  onMove,
  onRemove,
  onReset,
  onRetryDeclarations,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const query = searchQuery.trim().toLowerCase();
  const showSearch = totalCount > PICKER_SEARCH_THRESHOLD;

  const filteredBuiltin = useMemo(
    () =>
      query
        ? entries.builtin.filter((e) => matches(e.label, query))
        : entries.builtin,
    [entries.builtin, query],
  );
  const filteredCustom = useMemo(
    () =>
      query
        ? entries.custom.filter((e) => matches(e.label, query))
        : entries.custom,
    [entries.custom, query],
  );

  return (
    <Popover
      id={COLUMNS_POPOVER_ID}
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: -8, horizontal: "left" }}
      slotProps={{
        paper: {
          role: "dialog",
          "aria-label": "Choose columns",
          sx: { p: 0, width: 320, maxHeight: 480, borderRadius: "8px" },
        },
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column" }}>
        <Box
          sx={{
            position: "sticky",
            top: 0,
            zIndex: 10,
            backgroundColor: "background.paper",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            sx={{ pl: 2, pr: 1, pt: 1 }}
          >
            <Typography variant="subtitle2">Choose columns</Typography>
            <IconButton size="small" aria-label="Close" onClick={onClose}>
              <Iconify icon="mdi:close" width={18} />
            </IconButton>
          </Stack>
          {showSearch && (
            <Box sx={{ px: 1.5, pt: 1 }}>
              <FormSearchField
                size="small"
                fullWidth
                placeholder="Search columns"
                inputProps={{ "aria-label": "Search columns" }}
                searchQuery={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </Box>
          )}
          <Typography
            variant="caption"
            color="text.secondary"
            aria-live="polite"
            sx={{ display: "block", px: 2, py: 0.75, fontSize: "11px" }}
          >
            {visibleCount} of {totalCount} visible
          </Typography>
          <Divider />
        </Box>

        <Box sx={{ p: 1, display: "flex", flexDirection: "column" }}>
          <GroupHeading>Built-in</GroupHeading>
          {filteredBuiltin.map((entry) => (
            <ColumnEntryRow
              key={entry.id}
              entry={entry}
              onToggle={onToggle}
              onMove={onMove}
            />
          ))}

          <GroupHeading>Custom properties</GroupHeading>
          {declarationStatus === "pending" && (
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              sx={{ px: 1, py: 0.5 }}
            >
              <CircularProgress size={14} />
              <Typography variant="caption" color="text.secondary">
                Loading custom properties…
                {unavailableCustomCount > 0 &&
                  ` ${unavailableCustomCount} saved custom column${
                    unavailableCustomCount === 1 ? "" : "s"
                  } unavailable.`}
              </Typography>
            </Stack>
          )}
          {declarationStatus === "error" && (
            <Alert
              severity="error"
              sx={{ mx: 1, my: 0.5 }}
              action={
                <Button
                  color="inherit"
                  size="small"
                  aria-label="Retry loading custom properties"
                  onClick={onRetryDeclarations}
                >
                  Retry
                </Button>
              }
            >
              Couldn&apos;t load custom properties.
              {unavailableCustomCount > 0 &&
                ` ${unavailableCustomCount} saved custom column${
                  unavailableCustomCount === 1 ? "" : "s"
                } unavailable.`}
            </Alert>
          )}
          {declarationStatus === "success" && entries.custom.length === 0 && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ px: 1, py: 0.5, display: "block" }}
            >
              No custom properties declared.{" "}
              <Link
                component={RouterLink}
                to={paths.dashboard.gateway.customProperties}
              >
                Declare one in Custom Properties
              </Link>
            </Typography>
          )}
          {declarationStatus === "success" &&
            filteredCustom.map((entry) => (
              <ColumnEntryRow
                key={entry.id}
                entry={entry}
                onToggle={onToggle}
                onMove={onMove}
              />
            ))}

          {stale.length > 0 && (
            <>
              <GroupHeading>No longer declared</GroupHeading>
              {stale.map((id) => {
                const name = customNameFromId(id) ?? id;
                return (
                  <Stack
                    key={id}
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    sx={{ px: 1, py: 0.25 }}
                  >
                    <Typography
                      variant="body2"
                      noWrap
                      sx={{ fontSize: "13px", color: "text.disabled" }}
                      title={name}
                    >
                      {name}
                    </Typography>
                    <IconButton
                      size="small"
                      aria-label={`Remove ${name}`}
                      onClick={() => onRemove(id)}
                    >
                      <Iconify icon="mdi:close-circle-outline" width={18} />
                    </IconButton>
                  </Stack>
                );
              })}
            </>
          )}
        </Box>

        <Divider />
        <Box sx={{ p: 1 }}>
          <Button size="small" onClick={onReset}>
            Reset to default
          </Button>
        </Box>
      </Box>
    </Popover>
  );
};

ColumnsPickerPopover.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  entries: PropTypes.shape({
    builtin: PropTypes.array.isRequired,
    custom: PropTypes.array.isRequired,
  }).isRequired,
  stale: PropTypes.arrayOf(PropTypes.string).isRequired,
  visibleCount: PropTypes.number.isRequired,
  totalCount: PropTypes.number.isRequired,
  declarationStatus: PropTypes.oneOf(["pending", "error", "success"])
    .isRequired,
  unavailableCustomCount: PropTypes.number,
  onToggle: PropTypes.func.isRequired,
  onMove: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  onReset: PropTypes.func.isRequired,
  onRetryDeclarations: PropTypes.func.isRequired,
};

ColumnsPickerPopover.defaultProps = {
  unavailableCustomCount: 0,
};

export default ColumnsPickerPopover;
