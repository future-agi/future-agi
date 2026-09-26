import PropTypes from "prop-types";
import { Box, Stack, Typography, Select, MenuItem } from "@mui/material";
import { COUNTRY_OPTIONS, COUNTRY_BY_ISO } from "./countryCodes";

const MONO = "ui-monospace, Menlo, monospace";

// Dial-code picker: shows flag + "+dial" collapsed; flag + name + dial per row.
export default function CountryCodeSelect({ value, onChange }) {
  const selected = COUNTRY_BY_ISO[value] || COUNTRY_BY_ISO.US;
  return (
    <Select
      fullWidth
      size="small"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      MenuProps={{ PaperProps: { sx: { maxHeight: 320 } } }}
      renderValue={() => (
        <Stack direction="row" alignItems="center" spacing={1}>
          <Box component="span" sx={{ fontSize: 18, lineHeight: 1 }}>{selected?.flag}</Box>
          <Typography sx={{ typography: "s2", fontFamily: MONO }}>{selected?.dial}</Typography>
        </Stack>
      )}
      sx={{ "& .MuiSelect-select": { display: "flex", alignItems: "center", py: 0.75 } }}
    >
      {COUNTRY_OPTIONS.map((c, idx) => (
        <MenuItem
          key={c.iso}
          value={c.iso}
          sx={{
            typography: "s2",
            py: 0.75,
            borderTop:
              idx > 0 && !c.suggested && COUNTRY_OPTIONS[idx - 1]?.suggested ? "1px solid" : "none",
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
        </MenuItem>
      ))}
    </Select>
  );
}
CountryCodeSelect.propTypes = { value: PropTypes.string, onChange: PropTypes.func };
