import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Divider,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { splitChipColors } from "src/sections/common/Datasets/splitColors";

/** `Split: All ▾` — multi-select of the dataset's splits for the Summary. */
const SplitSelect = ({ splits, value, onChange }) => {
  const theme = useTheme();
  const [anchor, setAnchor] = useState(null);
  const selected = splits.filter((s) => value.includes(s.id));
  const label = selected.length
    ? selected.length <= 2
      ? selected.map((s) => s.name).join(", ")
      : `${selected.length} splits`
    : "All";
  const toggle = (id) =>
    onChange(
      value.includes(id) ? value.filter((v) => v !== id) : [...value, id],
    );

  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        disabled={!splits.length}
        onClick={(e) => setAnchor(e.currentTarget)}
        endIcon={<Iconify icon="mingcute:down-line" width={16} />}
        sx={{
          height: 32,
          borderColor: "divider",
          typography: "s2",
          fontWeight: 600,
          borderRadius: "4px",
        }}
      >
        Split: {label}
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 220 } } }}
      >
        <MenuItem
          dense
          selected={!value.length}
          onClick={() => onChange([])}
          sx={{ typography: "s2" }}
        >
          All rows
        </MenuItem>
        <Divider />
        {splits.map((s) => (
          <MenuItem key={s.id} dense onClick={() => toggle(s.id)}>
            <Checkbox
              size="small"
              checked={value.includes(s.id)}
              sx={{ p: 0.5, mr: 1 }}
            />
            <Box
              sx={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                bgcolor: splitChipColors(s.color, theme).dot,
                mr: 1,
              }}
            />
            <ListItemText primaryTypographyProps={{ typography: "s2" }}>
              {s.name}
            </ListItemText>
            {s.row_count != null && (
              <Typography typography="s3" color="text.secondary" sx={{ ml: 2 }}>
                {s.row_count}
              </Typography>
            )}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
};

SplitSelect.propTypes = {
  splits: PropTypes.array.isRequired,
  value: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
};

export default SplitSelect;
