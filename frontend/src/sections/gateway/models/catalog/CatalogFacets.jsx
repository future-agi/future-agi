import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import LogoMark from "../../components/frame/LogoMark";

// The catalog's filters as facets, as OpenRouter's model list has them:
// named groups of checkboxes (or, for price and context, one choice), each
// option with the number of models it would show given the other groups.
// Ticked options in a group add up; groups narrow each other. An option
// with nothing to show is disabled, never hidden, so the list does not jump.

const PROVIDERS_SHOWN = 8;

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
  const [showAll, setShowAll] = useState(false);
  const selected = group.multiple ? value || [] : value;
  const nameOf = (option) => `${option.label} (${option.count})`;

  let options = group.options;
  const long = group.key === "vendor" && options.length > PROVIDERS_SHOWN;
  if (long && !showAll) {
    options = options.filter(
      (o, i) => i < PROVIDERS_SHOWN || selected.includes(o.value),
    );
  }

  const heading = (
    <Typography
      id={headingId}
      component="h3"
      typography="s2"
      fontWeight="fontWeightSemiBold"
      color="text.secondary"
      sx={{ mb: 0.25 }}
    >
      {group.label}
    </Typography>
  );

  if (!group.multiple) {
    return (
      <Box role="group" aria-labelledby={headingId}>
        {heading}
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
          {showAll ? "Show fewer" : `Show all ${group.options.length}`}
        </Button>
      )}
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

export default function CatalogFacets({ facets, filters, onChange, onClear }) {
  return (
    <Box
      component="section"
      role="region"
      aria-label="Filters"
      sx={{ display: "flex", flexDirection: "column", gap: 2 }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          minHeight: 30,
        }}
      >
        <Typography
          component="h2"
          typography="s1"
          fontWeight="fontWeightMedium"
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
};
