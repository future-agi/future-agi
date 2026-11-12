import {
  Box,
  Checkbox,
  InputAdornment,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Popover,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import useListSearch from "src/hooks/use-list-search";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

const SelectEvalPopover = ({
  open,
  onClose,
  anchorElement,
  selectedEvals,
  setSelectedEvals,
  evalList,
}) => {
  const { onListSearch, listSearchQuery, filteredList } =
    useListSearch(evalList);

  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorEl={anchorElement}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "right",
      }}
      transformOrigin={{
        vertical: "top",
        horizontal: "right",
      }}
      PaperProps={{
        sx: {
          minWidth: anchorElement?.clientWidth,
        },
      }}
    >
      <Box sx={{ gap: "6px", display: "flex", flexDirection: "column" }}>
        <TextField
          placeholder="Search Evals to show"
          size="small"
          fullWidth
          value={listSearchQuery}
          onChange={(e) => onListSearch(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifier-linear" width={16}
                  sx={{ color: "divider" }}
                />
              </InputAdornment>
            ),
          }}
        />
        <Typography fontWeight={600} fontSize="12px" color="text.tertiary">
          All Evals
        </Typography>
        <Box
          sx={{
            display: "flex",
            maxHeight: "226px",
            overflowY: "auto",
            flexDirection: "column",
          }}
        >
          {filteredList.map((item) => (
            <EvalItem
              item={item}
              key={item.id}
              selected={selectedEvals.includes(item.value)}
              onChange={(newValue) => {
                if (newValue) {
                  trackEvent(Events.evalsSelected, {
                    [PropertyName.click]: item,
                  });
                }
                setSelectedEvals((prev) => {
                  if (newValue) {
                    return prev.includes(item.value)
                      ? prev
                      : [...prev, item.value];
                  } else {
                    return prev.filter((v) => v !== item.value);
                  }
                });
              }}
            />
          ))}
        </Box>
      </Box>
    </Popover>
  );
};

const EvalItem = ({ item, selected, onChange }) => {
  return (
    <Box sx={{ display: "flex", alignItems: "center" }}>
      <ListItem key={item.id} disablePadding sx={{ borderRadius: "6px" }} dense>
        <ListItemButton
          dense
          sx={{ paddingY: 0, paddingX: 1.5 }}
          onClick={() => onChange(!selected)}
        >
          <ListItemIcon sx={{ marginRight: 0 }}>
            <Checkbox
              edge="start"
              checked={selected}
              onChange={(e, checked) => onChange(checked)}
              tabIndex={-1}
              disableRipple
              inputProps={{ "aria-labelledby": item.id }}
            />
          </ListItemIcon>
          <ListItemText
            primaryTypographyProps={{
              fontWeight: 500,
              fontSize: "13px",
              borderRadius: "6px",
              maxWidth: "170px",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            id={item.value}
            primary={item.label}
          />
        </ListItemButton>
      </ListItem>
    </Box>
  );
};

EvalItem.propTypes = {
  item: PropTypes.object,
  selected: PropTypes.bool,
  onChange: PropTypes.func,
};

SelectEvalPopover.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  anchorElement: PropTypes.object,
  selectedEvals: PropTypes.array,
  setSelectedEvals: PropTypes.func,
  evalList: PropTypes.array,
};

export default SelectEvalPopover;
