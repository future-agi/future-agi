import React, { useState } from "react";
import ButtonBase from "@mui/material/ButtonBase";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import useFalconStore from "../store/useFalconStore";

const CONTEXT_OPTIONS = [
  { value: "auto", label: "Auto", icon: "solar:stars-minimalistic-linear" },
  { value: "datasets", label: "Datasets", icon: "solar:database-linear" },
  {
    value: "evaluations",
    label: "Evaluations",
    icon: "solar:checklist-minimalistic-linear",
  },
  { value: "tracing", label: "Tracing", icon: "solar:chart-2-linear" },
  {
    value: "experiments",
    label: "Experiments",
    icon: "solar:test-tube-linear",
  },
  { value: "agents", label: "Agents", icon: "solar:bot-linear" },
  { value: "prompts", label: "Prompts", icon: "solar:notes-linear" },
];

export default function ContextSelector() {
  const [anchorEl, setAnchorEl] = useState(null);

  const selectedContext = useFalconStore((s) => s.selectedContext);
  const setSelectedContext = useFalconStore((s) => s.setSelectedContext);

  const open = Boolean(anchorEl);
  const current =
    CONTEXT_OPTIONS.find((o) => o.value === selectedContext) ||
    CONTEXT_OPTIONS[0];

  const handleClick = (event) => {
    setAnchorEl(event.currentTarget);
  };

  const handleSelect = (value) => {
    setSelectedContext(value);
    setAnchorEl(null);
  };

  return (
    <>
      <ButtonBase
        onClick={handleClick}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          px: 1,
          py: 0.5,
          borderRadius: "8px",
          border: 1,
          borderColor: "border.default",
          bgcolor: "background.neutral",
          transition: "all 0.15s ease",
          "&:hover": {
            borderColor: "border.default",
            bgcolor: "action.hover",
          },
        }}
      >
        <Iconify
          icon={current.icon}
          width={14}
          sx={{ color: "text.secondary" }}
        />
        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 500,
            color: "text.secondary",
            lineHeight: 1,
            whiteSpace: "nowrap",
          }}
        >
          {current.label}
        </Typography>
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={14}
          sx={{ color: "text.disabled", ml: -0.25 }}
        />
      </ButtonBase>

      <Menu
        anchorEl={anchorEl}
        open={open}
        onClose={() => setAnchorEl(null)}
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: {
              minWidth: 200,
              maxHeight: 380,
              mt: -0.5,
              borderRadius: "12px",
              border: 1,
              borderColor: "divider",
            },
          },
        }}
      >
        {CONTEXT_OPTIONS.map((option) => (
          <MenuItem
            key={option.value}
            onClick={() => handleSelect(option.value)}
            selected={selectedContext === option.value}
            sx={{
              py: 1,
              px: 2,
              borderRadius: "8px",
              mx: 0.5,
              "&.Mui-selected": {
                bgcolor: "action.selected",
              },
            }}
          >
            <ListItemIcon sx={{ minWidth: 28 }}>
              <Iconify
                icon={option.icon}
                width={16}
                sx={{ color: "text.secondary" }}
              />
            </ListItemIcon>
            <ListItemText
              primary={option.label}
              primaryTypographyProps={{ fontSize: 13 }}
            />
            {selectedContext === option.value && (
              <Iconify
                icon="solar:check-linear"
                width={16}
                sx={{ color: "primary.main", ml: 1 }}
              />
            )}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
