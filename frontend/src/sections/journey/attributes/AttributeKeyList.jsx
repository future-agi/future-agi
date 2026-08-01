import { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  List,
  ListItemButton,
  ListItemText,
  TextField,
  InputAdornment,
  Chip,
  Button,
} from "@mui/material";
import Iconify from "src/components/iconify";

const TYPE_COLORS = {
  string: "info",
  number: "warning",
  boolean: "success",
};

const AttributeKeyList = ({
  keys,
  selectedKey,
  onSelectKey,
  allowManualEntry,
}) => {
  const [search, setSearch] = useState("");
  const manualKey = search.trim();

  const filtered = keys.filter((k) =>
    k.key.toLowerCase().includes(search.toLowerCase()),
  );
  const hasExactMatch = keys.some(
    (key) => key.key.toLowerCase() === manualKey.toLowerCase(),
  );

  return (
    <Box
      sx={{
        width: 300,
        borderRight: 1,
        borderColor: "divider",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box sx={{ p: 1.5, borderBottom: 1, borderColor: "divider" }}>
        <TextField
          size="small"
          fullWidth
          placeholder="Search attributes..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="eva:search-fill"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
        />
        {allowManualEntry && manualKey && !hasExactMatch && (
          <Button
            size="small"
            fullWidth
            sx={{ mt: 1, justifyContent: "flex-start" }}
            onClick={() => onSelectKey(manualKey, undefined)}
          >
            Use &quot;{manualKey}&quot;
          </Button>
        )}
      </Box>
      <List sx={{ overflow: "auto", flex: 1, p: 1 }} dense>
        {filtered.map(({ key, type, count }) => (
          <ListItemButton
            key={`${key}:${type}`}
            selected={selectedKey === key}
            onClick={() => onSelectKey(key, type)}
            sx={{ borderRadius: 1, py: 0.75 }}
          >
            <ListItemText
              primary={key}
              secondary={
                typeof count === "number"
                  ? count.toLocaleString() + " spans"
                  : "Count unavailable"
              }
              primaryTypographyProps={{
                variant: "body2",
                fontWeight: selectedKey === key ? 600 : 400,
                noWrap: true,
              }}
              secondaryTypographyProps={{ variant: "caption" }}
            />
            <Chip
              label={type}
              size="small"
              color={TYPE_COLORS[type] || "default"}
              variant="outlined"
              sx={{ ml: 1, height: 20, fontSize: "0.65rem" }}
            />
          </ListItemButton>
        ))}
        {filtered.length === 0 && (
          <Box sx={{ p: 2, textAlign: "center", color: "text.secondary" }}>
            No attributes found
          </Box>
        )}
      </List>
    </Box>
  );
};

AttributeKeyList.propTypes = {
  keys: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string,
      type: PropTypes.string,
      count: PropTypes.number,
    }),
  ).isRequired,
  selectedKey: PropTypes.string,
  onSelectKey: PropTypes.func.isRequired,
  allowManualEntry: PropTypes.bool,
};

export default AttributeKeyList;
