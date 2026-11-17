// The dataset list's Filter and Group by controls and the chips row (W9-
// SCOPE §5 A2, A3): the generic ListFilter with the dataset fields the
// list answers (plus one per metadata key, typed by `metadataTypes`, the
// query's `metadata_types`), facet counts in value menus,
// and the left-out sentence under the chips. The host owns the state
// (URL keys `f` and `group`) and the query. Group by is a compact menu
// button (`Group by: None`) like Filter and View, so the toolbar reads as
// one row of same-height controls (no select with a floating label).
import React, { useId, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ListFilterButton from "src/sections/common/ListFilter/ListFilterButton";
import FilterChips from "src/sections/common/ListFilter/FilterChips";
import { metadataTypesOf } from "./datasetFields";
import {
  GROUP_HINT,
  entryOffReason,
  groupOptions,
  labelOfFor,
  leftOutNotes,
  listFields,
} from "./datasetListView";

/**
 * `Group by: <value>`: a text button with the toolbar's look (icon, label,
 * chevron) that opens a menu of radio items, the current one checked and
 * focused on open. The hint is the button's description.
 */
function GroupByMenu({ options, value, onChange }) {
  const [anchor, setAnchor] = useState(null);
  const buttonId = useId();
  const menuId = useId();
  const open = Boolean(anchor);
  const current = options.find((o) => o.id === value) ?? options[0];
  const close = () => setAnchor(null);
  return (
    <>
      <CustomTooltip show title={GROUP_HINT} size="small" describeChild>
        <Button
          id={buttonId}
          size="small"
          color="inherit"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          startIcon={<Iconify icon="mdi:format-list-group" width={16} />}
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{
            typography: "s2",
            color: "text.secondary",
            minWidth: 0,
            flexShrink: 0,
            whiteSpace: "nowrap",
          }}
        >
          Group by:{" "}
          <Box
            component="span"
            sx={{
              fontWeight: "fontWeightMedium",
              color: current?.id ? "text.primary" : "inherit",
            }}
          >
            {current?.label}
          </Box>
        </Button>
      </CustomTooltip>
      <Menu
        id={menuId}
        anchorEl={anchor}
        open={open}
        onClose={close}
        MenuListProps={{ "aria-labelledby": buttonId }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
      >
        {options.map((o) => {
          const on = o.id === current?.id;
          return (
            <MenuItem
              key={o.id || "none"}
              role="menuitemradio"
              aria-checked={on}
              selected={on}
              onClick={() => {
                close();
                if (!on) onChange(o.id);
              }}
              sx={{ typography: "s2" }}
            >
              <ListItemIcon sx={{ minWidth: 24 }}>
                {on ? (
                  <Iconify
                    icon="mdi:check"
                    width={16}
                    sx={{ color: "primary.main" }}
                  />
                ) : null}
              </ListItemIcon>
              {o.label}
            </MenuItem>
          );
        })}
      </Menu>
    </>
  );
}

GroupByMenu.propTypes = {
  options: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, label: PropTypes.string }),
  ).isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};

// The toolbar's text-button look (as View), for the turned-off pair.
const offSx = {
  typography: "s2",
  color: "text.secondary",
  minWidth: 0,
  flexShrink: 0,
  whiteSpace: "nowrap",
};

/**
 * One turned-off entry: named by its label, its reason as a visible second
 * line and as its description. MUI fades a disabled item to 38%, which took
 * the reason below a readable contrast; the entry keeps full strength and
 * reads as off by its quieter label and the missing hover. No click handler,
 * so choosing it changes nothing.
 */
function OffEntry({ label, reason, lineId, radio = false }) {
  return (
    <MenuItem
      disabled
      role={radio ? "menuitemradio" : "menuitem"}
      {...(radio ? { "aria-checked": false } : {})}
      aria-label={label}
      aria-describedby={lineId}
      sx={{
        typography: "s2",
        alignItems: "flex-start",
        whiteSpace: "normal",
        maxWidth: 320,
        "&.Mui-disabled": { opacity: 1 },
      }}
    >
      {radio ? <ListItemIcon sx={{ minWidth: 24 }} /> : null}
      <ListItemText
        sx={{ my: 0 }}
        primary={label}
        secondary={reason}
        primaryTypographyProps={{ typography: "s2", color: "text.secondary" }}
        secondaryTypographyProps={{
          id: lineId,
          typography: "s3",
          color: "text.secondary",
        }}
      />
    </MenuItem>
  );
}

OffEntry.propTypes = {
  label: PropTypes.string.isRequired,
  reason: PropTypes.string.isRequired,
  lineId: PropTypes.string.isRequired,
  radio: PropTypes.bool,
};

/** `Filter` where the list applies none: every field, each turned off. */
function OffFilterMenu({ fields }) {
  const [anchor, setAnchor] = useState(null);
  const buttonId = useId();
  const menuId = useId();
  const lineId = useId();
  const open = Boolean(anchor);
  return (
    <>
      <Button
        id={buttonId}
        size="small"
        color="inherit"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        startIcon={<Iconify icon="mdi:filter-variant" width={16} />}
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={offSx}
      >
        Filter
      </Button>
      <Menu
        id={menuId}
        anchorEl={anchor}
        open={open}
        onClose={() => setAnchor(null)}
        MenuListProps={{ "aria-label": "Filter datasets" }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
      >
        {fields.map((f, i) => (
          <OffEntry
            key={f.id}
            label={f.label}
            reason={entryOffReason(f.id)}
            lineId={`${lineId}-${i}`}
          />
        ))}
      </Menu>
    </>
  );
}

OffFilterMenu.propTypes = { fields: PropTypes.array.isRequired };

/**
 * `Group by: None` where the list groups nothing: None stays checked and
 * every other option is turned off.
 */
function OffGroupByMenu({ options }) {
  const [anchor, setAnchor] = useState(null);
  const buttonId = useId();
  const menuId = useId();
  const lineId = useId();
  const open = Boolean(anchor);
  const close = () => setAnchor(null);
  return (
    <>
      <Button
        id={buttonId}
        size="small"
        color="inherit"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        startIcon={<Iconify icon="mdi:format-list-group" width={16} />}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={offSx}
      >
        Group by:{" "}
        <Box component="span" sx={{ fontWeight: "fontWeightMedium" }}>
          None
        </Box>
      </Button>
      <Menu
        id={menuId}
        anchorEl={anchor}
        open={open}
        onClose={close}
        MenuListProps={{ "aria-labelledby": buttonId }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
      >
        {options.map((o, i) =>
          o.id ? (
            <OffEntry
              key={o.id}
              radio
              label={o.label}
              reason={entryOffReason(o.id)}
              lineId={`${lineId}-${i}`}
            />
          ) : (
            <MenuItem
              key="none"
              role="menuitemradio"
              aria-checked="true"
              selected
              onClick={close}
              sx={{ typography: "s2" }}
            >
              <ListItemIcon sx={{ minWidth: 24 }}>
                <Iconify
                  icon="mdi:check"
                  width={16}
                  sx={{ color: "primary.main" }}
                />
              </ListItemIcon>
              {o.label}
            </MenuItem>
          ),
        )}
      </Menu>
    </>
  );
}

OffGroupByMenu.propTypes = {
  options: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, label: PropTypes.string }),
  ).isRequired,
};

/**
 * `available={false}`: the list behind them applies no filter or grouping.
 * Both stay on screen and list every entry, each turned off with its
 * reason; one field per metadata key the loaded rows carry (`items`) is
 * listed too. Nothing is applied or sent.
 */
export function DatasetListControls({
  filters,
  onFilters,
  group,
  onGroup,
  facets,
  metadataTypes,
  items,
  available = true,
}) {
  // Where the list answers no key types, the loaded rows name the keys.
  const rowTypes = useMemo(
    () => (available ? null : metadataTypesOf(items)),
    [available, items],
  );
  const fields = useMemo(
    () => listFields(facets, metadataTypes ?? rowTypes ?? undefined),
    [facets, metadataTypes, rowTypes],
  );
  const labelOf = useMemo(() => labelOfFor(facets), [facets]);
  const options = groupOptions(fields);
  if (!available)
    return (
      <>
        <OffFilterMenu fields={fields} />
        <OffGroupByMenu options={options} />
      </>
    );
  return (
    <>
      <ListFilterButton
        title="Filter datasets"
        fields={fields}
        filters={filters}
        onChange={onFilters}
        facets={facets}
        labelOf={labelOf}
      />
      <GroupByMenu options={options} value={group} onChange={onGroup} />
    </>
  );
}

DatasetListControls.propTypes = {
  filters: PropTypes.array.isRequired,
  onFilters: PropTypes.func.isRequired,
  group: PropTypes.string,
  onGroup: PropTypes.func.isRequired,
  facets: PropTypes.object,
  metadataTypes: PropTypes.object,
  items: PropTypes.array,
  available: PropTypes.bool,
};

export function DatasetFilterChips({
  filters,
  onFilters,
  facets,
  metadataTypes,
  leftOut,
}) {
  const fields = useMemo(
    () => listFields(facets, metadataTypes),
    [facets, metadataTypes],
  );
  const labelOf = useMemo(() => labelOfFor(facets), [facets]);
  if (!filters?.length) return null;
  return (
    <FilterChips
      filters={filters}
      fields={fields}
      onChange={onFilters}
      labelOf={labelOf}
      notes={leftOutNotes(leftOut, fields)}
    />
  );
}

DatasetFilterChips.propTypes = {
  filters: PropTypes.array,
  onFilters: PropTypes.func.isRequired,
  facets: PropTypes.object,
  metadataTypes: PropTypes.object,
  leftOut: PropTypes.object,
};
