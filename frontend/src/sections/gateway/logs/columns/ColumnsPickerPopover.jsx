import React, { forwardRef, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
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
import { LoadingButton } from "@mui/lab";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { paths } from "src/routes/paths";
import { COLUMNS_POPOVER_ID, customNameFromId } from "./columnModel";
import ClippedColumnLabel from "./ClippedColumnLabel";

const MOVE_HINT_ID = `${COLUMNS_POPOVER_ID}-move-hint`;

function matches(label, query) {
  return label.toLowerCase().includes(query);
}

const ColumnEntryRow = ({
  entry,
  onToggle,
  onMove,
  atCap = false,
  searching = false,
}) => {
  const upRef = useRef(null);
  const downRef = useRef(null);
  // At the cap a column not yet in the config cannot be added (toggleColumn).
  const disabled = entry.locked || (atCap && !entry.inConfig);

  // Keep focus on this column's arrow after the move commits; once the column
  // reaches the end of its group that arrow is disabled, so use the other.
  const handleMove = (delta) => {
    flushSync(() => onMove(entry.id, delta));
    const [pressed, opposite] = delta < 0 ? [upRef, downRef] : [downRef, upRef];
    (pressed.current.disabled ? opposite : pressed).current.focus();
  };

  return (
    <Stack
      direction="row"
      alignItems="center"
      sx={{
        px: 1,
        py: 0.25,
        borderRadius: "4px",
        "&:hover": { backgroundColor: "action.hover" },
        opacity: disabled ? 0.6 : 1,
      }}
    >
      <FormControlLabel
        disableTypography
        sx={{ mx: 0, flex: 1, minWidth: 0 }}
        control={
          <Checkbox
            size="small"
            checked={entry.checked}
            disabled={disabled}
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
          <ClippedColumnLabel
            label={entry.label}
            sx={{ fontSize: "13px", ml: 0.5 }}
          >
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
          </ClippedColumnLabel>
        }
      />
      {!entry.locked && (
        <Stack direction="row" spacing={0}>
          <IconButton
            ref={upRef}
            size="small"
            aria-label={`Move ${entry.label} up`}
            aria-describedby={searching ? MOVE_HINT_ID : undefined}
            disabled={searching || !entry.canMoveUp}
            onClick={() => handleMove(-1)}
          >
            <Iconify icon="mdi:chevron-up" width={18} />
          </IconButton>
          <IconButton
            ref={downRef}
            size="small"
            aria-label={`Move ${entry.label} down`}
            aria-describedby={searching ? MOVE_HINT_ID : undefined}
            disabled={searching || !entry.canMoveDown}
            onClick={() => handleMove(1)}
          >
            <Iconify icon="mdi:chevron-down" width={18} />
          </IconButton>
        </Stack>
      )}
    </Stack>
  );
};

ColumnEntryRow.propTypes = {
  entry: PropTypes.shape({
    id: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
    checked: PropTypes.bool.isRequired,
    inConfig: PropTypes.bool,
    locked: PropTypes.bool,
    canMoveUp: PropTypes.bool,
    canMoveDown: PropTypes.bool,
  }).isRequired,
  onToggle: PropTypes.func.isRequired,
  onMove: PropTypes.func.isRequired,
  atCap: PropTypes.bool,
  searching: PropTypes.bool,
};

const GroupHeading = forwardRef(({ children, ...other }, ref) => (
  <Typography
    ref={ref}
    variant="caption"
    color="text.secondary"
    sx={{ px: 1, pt: 1, pb: 0.5, display: "block", fontSize: "11px" }}
    {...other}
  >
    {children}
  </Typography>
));

GroupHeading.displayName = "GroupHeading";
GroupHeading.propTypes = { children: PropTypes.node.isRequired };

/**
 * Columns picker for the Request Logs table. Clone of the house
 * PersonasColumnsPopover pattern with reorder, a custom-properties group,
 * declaration states and a stale group.
 */
const ColumnsPickerPopover = ({
  anchorEl,
  open,
  onClose,
  entries,
  stale,
  visibleCount,
  totalCount,
  atCap = false,
  declarationStatus,
  declarationsAvailable,
  declarationsFetching = false,
  reservedDeclared = false,
  unavailableCustomCount = 0,
  onToggle,
  onMove,
  onRemove,
  onReset,
  onRetryDeclarations,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const searchRef = useRef(null);
  const customHeadingRef = useRef(null);
  const removeButtons = useRef(new Map());
  const query = searchQuery.trim().toLowerCase();

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
  const filteredStale = useMemo(
    () =>
      query
        ? stale.filter((id) => matches(customNameFromId(id) ?? id, query))
        : stale,
    [stale, query],
  );
  // A search that matches no loaded custom row hides that group; its loading
  // and error rows stay, since unloaded properties might match.
  const customFilteredOut =
    Boolean(query) &&
    declarationsAvailable &&
    declarationStatus !== "error" &&
    filteredCustom.length === 0;
  const noMatches =
    filteredBuiltin.length === 0 &&
    customFilteredOut &&
    filteredStale.length === 0;

  // Keep focus on the next stale row, else the previous one, else the search,
  // once the removed row unmounts.
  const handleRemove = (id, index) => {
    const nextId = filteredStale[index + 1] ?? filteredStale[index - 1];
    flushSync(() => onRemove(id));
    (removeButtons.current.get(nextId) ?? searchRef.current)?.focus();
  };

  // Keep Retry focus on a control that survives the successful reload. When a
  // search hides every custom declaration, the group heading unmounts, so the
  // search field is the stable focus target; otherwise the heading preserves
  // the useful context while the error row becomes a loading row.
  const handleRetry = () => {
    (query ? searchRef.current : customHeadingRef.current)?.focus();
    onRetryDeclarations();
  };

  return (
    <Popover
      id={COLUMNS_POPOVER_ID}
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      // Stays mounted while closed: clear the search on every open, which
      // (unlike on exit) also covers reopening mid-close.
      onTransitionEnter={() => setSearchQuery("")}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: -8, horizontal: "left" }}
      slotProps={{
        paper: {
          role: "dialog",
          "aria-label": "Choose columns",
          sx: {
            p: 0,
            width: 320,
            borderRadius: "8px",
            display: "flex",
            flexDirection: "column",
          },
        },
      }}
    >
      {/* Only the column list scrolls, so the header and Reset never cover a
          focused control. The paper keeps MUI's viewport limit; below 480px
          this wrapper, and so the list, shrinks to fit it. */}
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          maxHeight: 480,
          minHeight: 0,
        }}
      >
        <Box>
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
          <Box sx={{ px: 1.5, pt: 1 }}>
            <FormSearchField
              size="small"
              fullWidth
              placeholder="Search columns"
              inputProps={{ "aria-label": "Search columns" }}
              inputRef={searchRef}
              searchQuery={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {query && (
              <Typography
                id={MOVE_HINT_ID}
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", px: 0.5, pt: 0.5, fontSize: "11px" }}
              >
                Clear the search to reorder columns.
              </Typography>
            )}
          </Box>
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

        <Box
          sx={{
            p: 1,
            display: "flex",
            flexDirection: "column",
            overflowY: "auto",
          }}
        >
          {/* Always mounted, so screen readers announce the change. */}
          <Typography
            role="status"
            variant="caption"
            color="text.secondary"
            sx={{ px: 1, py: noMatches ? 0.5 : 0, display: "block" }}
          >
            {noMatches && "No matching columns"}
          </Typography>
          {filteredBuiltin.length > 0 && <GroupHeading>Built-in</GroupHeading>}
          {filteredBuiltin.map((entry) => (
            <ColumnEntryRow
              key={entry.id}
              entry={entry}
              onToggle={onToggle}
              onMove={onMove}
              searching={Boolean(query)}
            />
          ))}

          {!customFilteredOut && (
            <GroupHeading ref={customHeadingRef} tabIndex={-1}>
              Custom properties
            </GroupHeading>
          )}
          {!declarationsAvailable && declarationStatus !== "error" && (
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
              severity={declarationsAvailable ? "warning" : "error"}
              sx={{ mx: 1, my: 0.5 }}
              action={
                <LoadingButton
                  color="inherit"
                  size="small"
                  aria-label="Retry loading custom properties"
                  loading={declarationsFetching}
                  onClick={handleRetry}
                >
                  Retry
                </LoadingButton>
              }
            >
              {declarationsAvailable
                ? "Couldn't refresh custom properties. Showing previously loaded properties."
                : "Couldn't load custom properties."}
              {unavailableCustomCount > 0 &&
                ` ${unavailableCustomCount} saved custom column${
                  unavailableCustomCount === 1 ? "" : "s"
                } unavailable.`}
            </Alert>
          )}
          {declarationsAvailable &&
            entries.custom.length === 0 &&
            !customFilteredOut && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ px: 1, py: 0.5, display: "block" }}
              >
                {reservedDeclared
                  ? "No other custom properties declared (Application and Service are already built-in columns)."
                  : "No custom properties declared."}{" "}
                <Link
                  component={RouterLink}
                  to={paths.dashboard.gateway.customProperties}
                >
                  Declare one in Custom Properties
                </Link>
              </Typography>
            )}
          {declarationsAvailable && atCap && !customFilteredOut && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ px: 1, py: 0.5, display: "block" }}
            >
              Column limit reached. Reset to default to add more.
            </Typography>
          )}
          {declarationsAvailable &&
            filteredCustom.map((entry) => (
              <ColumnEntryRow
                key={entry.id}
                entry={entry}
                onToggle={onToggle}
                onMove={onMove}
                atCap={atCap}
                searching={Boolean(query)}
              />
            ))}

          {filteredStale.length > 0 && (
            <>
              <GroupHeading>No longer declared</GroupHeading>
              {filteredStale.map((id, index) => {
                const name = customNameFromId(id) ?? id;
                return (
                  <Stack
                    key={id}
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    sx={{ px: 1, py: 0.25 }}
                  >
                    <ClippedColumnLabel
                      label={name}
                      sx={{ fontSize: "13px", color: "text.disabled" }}
                    />
                    <IconButton
                      ref={(el) => {
                        if (el) removeButtons.current.set(id, el);
                        else removeButtons.current.delete(id);
                      }}
                      size="small"
                      aria-label={`Remove ${name}`}
                      onClick={() => handleRemove(id, index)}
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
  atCap: PropTypes.bool,
  declarationStatus: PropTypes.oneOf(["pending", "error", "success"])
    .isRequired,
  declarationsAvailable: PropTypes.bool.isRequired,
  declarationsFetching: PropTypes.bool,
  reservedDeclared: PropTypes.bool,
  unavailableCustomCount: PropTypes.number,
  onToggle: PropTypes.func.isRequired,
  onMove: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  onReset: PropTypes.func.isRequired,
  onRetryDeclarations: PropTypes.func.isRequired,
};

export default ColumnsPickerPopover;
