import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import ButtonBase from "@mui/material/ButtonBase";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Tooltip from "@mui/material/Tooltip";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import LogoMark from "../../components/frame/LogoMark";

// The catalog's filters as facets, as OpenRouter's model list has them:
// named groups of checkboxes (or, for price and context, one choice), each
// option with the number of models it would show given the other groups.
// Ticked options in a group add up; groups narrow each other. An option
// with nothing to show is disabled, never hidden, so the list does not jump.
//
// The panel scrolls by itself (the page places it): its title and Clear
// filters stay at the top while the groups scroll under them, and each group
// folds away from its heading, which also says how many options it has on.

const PROVIDERS_SHOWN = 8;

const VISUALLY_HIDDEN = {
  position: "absolute",
  width: 1,
  height: 1,
  p: 0,
  m: "-1px",
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
};

function Chevron({ open }) {
  return (
    <Box
      component="svg"
      viewBox="0 0 16 16"
      aria-hidden
      sx={{
        width: 16,
        height: 16,
        flexShrink: 0,
        color: "text.secondary",
        transition: (theme) =>
          theme.transitions.create("transform", {
            duration: theme.transitions.duration.shortest,
          }),
        transform: open ? "rotate(0deg)" : "rotate(-90deg)",
      }}
    >
      <path
        d="M4 6l4 4 4-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Box>
  );
}

Chevron.propTypes = { open: PropTypes.bool };

function CloseMark() {
  return (
    <Box
      component="svg"
      viewBox="0 0 16 16"
      aria-hidden
      sx={{ width: 16, height: 16 }}
    >
      <path
        d="M4 4l8 8M12 4l-8 8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </Box>
  );
}

function OptionLabel({ option }) {
  return (
    <Box
      component="span"
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        minWidth: 0,
        width: "100%",
      }}
    >
      {option.logo !== undefined && (
        <LogoMark name={option.label} src={option.logo} size={16} />
      )}
      <Typography
        component="span"
        typography="s2"
        noWrap
        sx={{ flex: 1, minWidth: 0 }}
      >
        {option.label}
      </Typography>
      <Typography
        component="span"
        typography="s2"
        color="text.secondary"
        aria-hidden
        sx={{ fontVariantNumeric: "tabular-nums", flexShrink: 0 }}
      >
        {option.count}
      </Typography>
    </Box>
  );
}

OptionLabel.propTypes = { option: PropTypes.object.isRequired };

const LABEL_SX = {
  mx: 0,
  width: "100%",
  "& .MuiFormControlLabel-label": { flex: 1, minWidth: 0 },
};

function Group({ group, value, onChange }) {
  const headingId = useId();
  const bodyId = useId();
  const [open, setOpen] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [search, setSearch] = useState("");
  const selected = group.multiple ? value || [] : value;
  const nameOf = (option) => `${option.label} (${option.count})`;
  const onCount = group.multiple
    ? selected.length
    : value !== undefined && value !== null && value !== "" && value !== 0
      ? 1
      : 0;

  let options = group.searchable
    ? group.options.filter(
        (option) =>
          selected.includes(option.value) ||
          option.label.toLowerCase().includes(search.trim().toLowerCase()),
      )
    : group.options;
  const matchingCount = options.length;
  const long =
    (group.key === "vendor" || group.searchable) &&
    options.length > PROVIDERS_SHOWN;
  if (long && !showAll) {
    options = options.filter(
      (o, i) => i < PROVIDERS_SHOWN || selected.includes(o.value),
    );
  }

  // An accordion heading: the h3 holds a button that folds the group. The
  // group is labelled by the name alone; the count of options on and the
  // chevron sit beside it in the button, hidden from the name.
  const heading = (
    <Typography
      component="h3"
      typography="s2"
      fontWeight="fontWeightSemiBold"
      color="text.secondary"
      sx={{ m: 0, mb: 0.25 }}
    >
      <ButtonBase
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
        sx={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          py: 0.5,
          borderRadius: 0.75,
          justifyContent: "flex-start",
          textAlign: "left",
          font: "inherit",
          color: "inherit",
          "&:hover": { color: "text.primary" },
          // Drawn inside the button: the panel scrolls (overflow clips) and
          // has no side padding on wide screens, so an outer ring is cut off.
          "&.Mui-focusVisible": {
            outline: (theme) => `2px solid ${theme.palette.primary.main}`,
            outlineOffset: -2,
          },
        }}
      >
        <Box component="span" id={headingId} sx={{ flex: 1, minWidth: 0 }}>
          {group.label}
        </Box>
        {onCount > 0 && (
          // Heard by screen readers; the group itself stays named by its label.
          <Box component="span" sx={VISUALLY_HIDDEN}>
            {`(${onCount} selected)`}
          </Box>
        )}
        {onCount > 0 && (
          <Box
            component="span"
            aria-hidden
            sx={{
              minWidth: 18,
              height: 18,
              px: 0.5,
              borderRadius: 9,
              bgcolor: "primary.main",
              color: "primary.contrastText",
              typography: "caption",
              lineHeight: "18px",
              textAlign: "center",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {onCount}
          </Box>
        )}
        <Chevron open={open} />
      </ButtonBase>
    </Typography>
  );

  if (!group.multiple) {
    return (
      <Box role="group" aria-labelledby={headingId}>
        {heading}
        <Box id={bodyId} hidden={!open}>
          <RadioGroup
            aria-labelledby={headingId}
            value={String(value ?? "")}
            onChange={(event) => {
              const picked = group.options.find(
                (o) => String(o.value) === event.target.value,
              );
              onChange(picked ? picked.value : "");
            }}
          >
            {group.options.map((option) => (
              <FormControlLabel
                key={String(option.value)}
                value={String(option.value)}
                disabled={option.count === 0 && value !== option.value}
                sx={LABEL_SX}
                control={
                  <Radio
                    size="small"
                    sx={{ p: 0.5 }}
                    inputProps={{ "aria-label": nameOf(option) }}
                  />
                }
                label={<OptionLabel option={option} />}
              />
            ))}
          </RadioGroup>
        </Box>
      </Box>
    );
  }

  const toggle = (option) =>
    onChange(
      selected.includes(option.value)
        ? selected.filter((v) => v !== option.value)
        : [...selected, option.value],
    );

  return (
    <Box role="group" aria-labelledby={headingId}>
      {heading}
      <Box id={bodyId} hidden={!open}>
        {group.description && (
          <Typography
            typography="s3"
            color="text.secondary"
            component="p"
            sx={{ mb: 1 }}
          >
            {group.description}
          </Typography>
        )}
        {group.searchable && (
          <TextField
            size="small"
            fullWidth
            label={`Search ${group.label.toLowerCase()}`}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setShowAll(false);
            }}
            sx={{ mb: 0.5 }}
          />
        )}
        {!options.length && (
          <Typography typography="s2" color="text.secondary">
            No matching facts
          </Typography>
        )}
        {options.map((option) => {
          const checked = selected.includes(option.value);
          const control = (
            <FormControlLabel
              key={String(option.value)}
              disabled={option.count === 0 && !checked}
              sx={LABEL_SX}
              control={
                <Checkbox
                  size="small"
                  sx={{ p: 0.5 }}
                  checked={checked}
                  onChange={() => toggle(option)}
                  inputProps={{ "aria-label": nameOf(option) }}
                />
              }
              label={<OptionLabel option={option} />}
            />
          );
          return option.description ? (
            <Tooltip
              key={String(option.value)}
              title={option.description}
              placement="right"
              arrow
            >
              <Box>{control}</Box>
            </Tooltip>
          ) : (
            control
          );
        })}
        {long && (
          <Button
            size="small"
            variant="text"
            onClick={() => setShowAll((v) => !v)}
            sx={{ px: 0.5, minWidth: 0, typography: "s2" }}
          >
            {showAll ? "Show fewer" : `Show all ${matchingCount}`}
          </Button>
        )}
      </Box>
    </Box>
  );
}

Group.propTypes = {
  group: PropTypes.object.isRequired,
  value: PropTypes.oneOfType([
    PropTypes.array,
    PropTypes.string,
    PropTypes.number,
  ]),
  onChange: PropTypes.func.isRequired,
};

export default function CatalogFacets({
  facets,
  filters,
  onChange,
  onClear,
  onClose,
}) {
  return (
    <Box
      component="section"
      role="region"
      aria-label="Filters"
      sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      <Box
        sx={{
          // Stays at the top of whatever scrolls the panel, above the
          // groups' own layers (an outlined field's label sits at z-index 1
          // and would otherwise paint over the title as it scrolls under).
          position: "sticky",
          top: 0,
          zIndex: 3,
          bgcolor: "background.paper",
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          minHeight: 36,
          // In the drawer the scroll box has no top padding (the title would
          // stick below its edge and show the list above it), so the title
          // row carries that room itself.
          pt: onClose ? 1.5 : 0,
          pb: 0.5,
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Typography
          component="h2"
          typography="s1"
          fontWeight="fontWeightMedium"
          sx={{ flex: 1, minWidth: 0 }}
        >
          Filters
        </Typography>
        {onClear && (
          <Button
            size="small"
            variant="text"
            onClick={onClear}
            sx={{ minWidth: 0, typography: "s2" }}
          >
            Clear filters
          </Button>
        )}
        {onClose && (
          <IconButton size="small" aria-label="Close filters" onClick={onClose}>
            <CloseMark />
          </IconButton>
        )}
      </Box>
      {facets.map((group) => (
        <Group
          key={group.key}
          group={group}
          value={filters[group.key]}
          onChange={(value) => onChange(group.key, value)}
        />
      ))}
    </Box>
  );
}

CatalogFacets.propTypes = {
  facets: PropTypes.array.isRequired,
  filters: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  // Shown while any filter is set.
  onClear: PropTypes.func,
  // Given where the panel opens as a drawer (narrow screens).
  onClose: PropTypes.func,
};
