import PropTypes from "prop-types";
import { Autocomplete, Box, InputAdornment, Stack, TextField, Typography, createFilterOptions } from "@mui/material";
import { COUNTRY_OPTIONS, COUNTRY_BY_ISO } from "./countryCodes";

const MONO = "ui-monospace, Menlo, monospace";
const FIRST_REST_ISO = COUNTRY_OPTIONS.find((c, idx) => idx > 0 && !c.suggested && COUNTRY_OPTIONS[idx - 1]?.suggested)?.iso;
const filterOptions = createFilterOptions({ stringify: (c) => `${c.name} ${c.dial} ${c.iso}` });

// Dial-code picker: shows flag + "+dial" collapsed; flag + name + dial per row.
export default function CountryCodeSelect({ value, onChange }) {
  const selected = COUNTRY_BY_ISO[value] || COUNTRY_BY_ISO.US;
  return (
    <Autocomplete
      fullWidth
      size="small"
      disableClearable
      options={COUNTRY_OPTIONS}
      value={selected}
      onChange={(_, c) => c && onChange(c.iso)}
      getOptionLabel={(c) => c.dial}
      isOptionEqualToValue={(a, b) => a.iso === b.iso}
      filterOptions={filterOptions}
      slotProps={{
        popper: { placement: "bottom-start", sx: { minWidth: 280 } },
        paper: { sx: { "& .MuiAutocomplete-listbox": { maxHeight: 320 } } },
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          placeholder="Search country"
          InputProps={{
            ...params.InputProps,
            startAdornment: (
              <InputAdornment position="start" sx={{ ml: 0.5, mr: 0 }}>
                <Box component="span" sx={{ fontSize: 18, lineHeight: 1 }}>{selected?.flag}</Box>
              </InputAdornment>
            ),
          }}
          sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: MONO } }}
        />
      )}
      renderOption={(optionProps, c, { inputValue }) => {
        return (
          <Box
            component="li"
            {...optionProps}
            key={c.iso}
            sx={{
              typography: "s2",
              py: 0.75,
              borderTop: !inputValue && c.iso === FIRST_REST_ISO ? "1px solid" : "none",
              borderColor: "divider",
            }}
          >
            <Stack direction="row" alignItems="center" spacing={1.25} sx={{ width: "100%" }}>
              <Box component="span" sx={{ fontSize: 18, lineHeight: 1, flexShrink: 0 }}>{c.flag}</Box>
              <Typography
                sx={{ typography: "s2", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              >
                {c.name}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.subtitle", fontFamily: MONO, flexShrink: 0 }}>
                {c.dial}
              </Typography>
            </Stack>
          </Box>
        );
      }}
    />
  );
}
CountryCodeSelect.propTypes = { value: PropTypes.string, onChange: PropTypes.func };
