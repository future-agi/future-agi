import {
  Box,
  Checkbox,
  Chip,
  FormControl,
  InputLabel,
  ListSubheader,
  MenuItem,
  OutlinedInput,
  Select,
  TextField,
  Typography,
} from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { getPerformanceTagColor, getTabLabel } from "src/utils/utils";

const PerformanceTagSelect = ({
  selectedTags,
  setSelectedTags,
  tagSearchQuery,
  searchedTagOptions,
  setTagSearchQuery,
}) => {
  return (
    <FormControl size="small" sx={{ width: 250 }}>
      <InputLabel>Tag</InputLabel>
      <Select
        placeholder="Select Tags"
        value={selectedTags}
        onChange={(e) => {
          const {
            target: { value },
          } = e;
          setSelectedTags(typeof value === "string" ? value.split(",") : value);
          // trackEvent(Events.metricPerformanceSelectedTagChange, {
          //   "Selected Tags": value,
          // });
        }}
        label="Tag"
        multiple
        input={<OutlinedInput label="Tag" />}
        MenuProps={{
          autoFocus: false,
          PaperProps: {
            style: {
              maxHeight: 260,
              display: "flex",
              flexDirection: "column",
            },
          },
        }}
        renderValue={(selected) => (
          <Box
            sx={{
              display: "flex",
              gap: 0.5,
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {selected.slice(0, 1).map((value) => (
              <Chip
                variant="soft"
                color={getPerformanceTagColor(value)}
                key={value}
                label={getTabLabel(value)}
                size="small"
                sx={{
                  fontSize: "11px",
                }}
              />
            ))}
            {Boolean(selected.length - 1) && (
              <Typography variant="subtitle" color="text.disabled">
                + {selected.length - 1} more
              </Typography>
            )}
          </Box>
        )}
      >
        <ListSubheader
          sx={{ padding: 1, paddingBottom: 0 }}
          onClick={(e) => e.stopPropagation()}
        >
          <TextField
            value={tagSearchQuery}
            onChange={(e) => {
              e.stopPropagation();
              setTagSearchQuery(e.target.value);
            }}
            onKeyDown={(e) => e.stopPropagation()}
            size="small"
            fullWidth
            placeholder="Search Tags"
          />
        </ListSubheader>
        {searchedTagOptions
          ?.sort((a, b) => {
            if (
              selectedTags.includes(a.value) &&
              !selectedTags.includes(b.value)
            )
              return -1;
            if (
              !selectedTags.includes(a.value) &&
              selectedTags.includes(b.value)
            )
              return 1;
            return 0;
          })
          .map(({ label, value }) => {
            const color = getPerformanceTagColor(label);
            return (
              <MenuItem key={value} value={value}>
                <Checkbox checked={selectedTags.includes(value)} />
                <Chip
                  variant="soft"
                  color={color}
                  key={label}
                  label={getTabLabel(label)}
                  size="small"
                  sx={{
                    fontSize: "11px",
                  }}
                />
              </MenuItem>
            );
          })}
      </Select>
    </FormControl>
  );
};

PerformanceTagSelect.propTypes = {
  selectedTags: PropTypes.array,
  setSelectedTags: PropTypes.func,
  tagSearchQuery: PropTypes.string,
  searchedTagOptions: PropTypes.array,
  setTagSearchQuery: PropTypes.func,
};

export default PerformanceTagSelect;
