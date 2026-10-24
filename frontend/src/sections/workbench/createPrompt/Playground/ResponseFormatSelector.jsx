import React, { useMemo, useState } from "react";
import {
  Box,
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
  ButtonBase,
} from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import CreateResponseSchema from "src/components/custom-model-options/CreateResponseSchema";

const getIconForResponseFormat = (value) => {
  switch (value) {
    case "text":
      return (
        <SvgColor
          src="/assets/icons/prompt_outputs/ic_text.svg"
          width={20}
          height={20}
        />
      );
    case "json_object":
    case "json":
      return (
        <SvgColor
          src="/assets/icons/prompt_outputs/ic_json.svg"
          width={20}
          height={20}
        />
      );
    case "audio":
      return (
        <SvgColor
          src="/assets/icons/prompt_outputs/ic_audio.svg"
          width={20}
          height={20}
        />
      );
    case "image":
      return (
        <SvgColor
          src="/assets/icons/prompt_outputs/ic_image.svg"
          width={20}
          height={20}
        />
      );
    default:
      return (
        <Iconify icon="solar:document-text-linear" width={20} height={20} />
      );
  }
};

const ResponseFormatSelector = ({
  modelType,
  responseFormatOptions,
  responseSchema,
  selectedValue,
  onChange,
  disabled,
}) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const [showCreateSchema, setShowCreateSchema] = useState(false);
  const menuOpen = Boolean(anchorEl);

  const isChatModel = modelType === "chat";
  const isTTSModel = modelType === "tts";
  const isSTTModel = modelType === "stt";
  const isImageModel = modelType === "image_generation";

  const menuItems = useMemo(() => {
    if (isTTSModel) {
      return [{ value: "audio", label: "Audio output" }];
    }
    if (isImageModel) {
      return [{ value: "image", label: "Image output" }];
    }
    if (isSTTModel) {
      return [{ value: "text", label: "Text output" }];
    }
    if (isChatModel) {
      const items = [];
      // Add dynamic response format options from API
      if (responseFormatOptions?.length > 0) {
        responseFormatOptions.forEach((opt) => {
          items.push({
            value: opt.value,
            label:
              opt.value === "text"
                ? "Text output"
                : opt.value === "json_object" || opt.value === "json"
                  ? "JSON output"
                  : opt.label,
          });
        });
      }
      // Add custom response schemas
      if (responseSchema?.length > 0) {
        responseSchema.forEach((schema) => {
          items.push({
            value: schema.id,
            label: schema.name,
            isCustomSchema: true,
          });
        });
      }
      return items;
    }
    return [];
  }, [
    isTTSModel,
    isSTTModel,
    isImageModel,
    isChatModel,
    responseFormatOptions,
    responseSchema,
  ]);

  const selectedItem = useMemo(() => {
    return (
      menuItems.find((item) => item.value === selectedValue) || menuItems[0]
    );
  }, [menuItems, selectedValue]);

  const handleClick = (event) => {
    if (!disabled) {
      setAnchorEl(event.currentTarget);
    }
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleSelect = (value) => {
    onChange(value);
    handleClose();
  };

  const handleCreateSchema = () => {
    handleClose();
    setShowCreateSchema(true);
  };

  if (!modelType || menuItems.length === 0) {
    return null;
  }

  return (
    <>
      <ButtonBase
        component="div"
        onClick={handleClick}
        sx={{
          display: "flex",
          justifyContent: "flex-start",
          textAlign: "left",
          alignItems: "center",
          gap: 0.75,
          padding: "4px 10px",
          border: "1px solid",
          borderColor: "border.default",
          borderRadius: "4px",
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.5 : 1,
          bgcolor: menuOpen ? "action.hover" : "transparent",
          flexShrink: 0,
          "&:hover": {
            bgcolor: disabled ? "transparent" : "action.hover",
          },
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            color: "text.secondary",
          }}
        >
          {getIconForResponseFormat(selectedItem?.value)}
        </Box>
        <Typography
          variant="s2"
          fontWeight="fontWeightMedium"
          color="text.primary"
          sx={{ whiteSpace: "nowrap" }}
        >
          {selectedItem?.label || "Select format"}
        </Typography>
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={16}
          height={16}
          sx={{
            color: "text.secondary",
            transform: menuOpen ? "rotate(180deg)" : "none",
            transition: "transform 0.2s",
          }}
        />
      </ButtonBase>
      <Menu
        anchorEl={anchorEl}
        open={menuOpen}
        onClose={handleClose}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "left",
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "left",
        }}
        slotProps={{
          paper: {
            sx: {
              mt: 0.5,
              minWidth: 200,
              borderRadius: "8px",
              border: "1px solid",
              borderColor: "border.default",
            },
          },
        }}
      >
        {menuItems.map((item) => (
          <MenuItem
            key={item.value}
            selected={item.value === selectedValue}
            onClick={() => handleSelect(item.value)}
            sx={{
              py: 1,
              px: 1.5,
              "&.Mui-selected": {
                bgcolor: "action.selected",
              },
            }}
          >
            <ListItemIcon
              sx={{
                minWidth: "20px !important",
                mr: 1,
                color: "text.secondary",
              }}
            >
              {getIconForResponseFormat(item.value)}
            </ListItemIcon>
            <ListItemText
              primary={item.label}
              primaryTypographyProps={{
                typography: "s2",
                fontWeight: "fontWeightMedium",
                color: "text.primary",
              }}
            />
          </MenuItem>
        ))}
        {isChatModel && (
          <>
            <Divider sx={{ my: 0.5 }} />
            <MenuItem
              onClick={handleCreateSchema}
              sx={{
                py: 1,
                px: 1.5,
              }}
            >
              <ListItemIcon
                sx={{
                  minWidth: "20px !important",
                  mr: 1,
                  color: "primary.main",
                }}
              >
                <Iconify icon="solar:add-linear" width={16} height={20} />
              </ListItemIcon>
              <ListItemText
                primary="Create custom schema"
                primaryTypographyProps={{
                  variant: "s2",
                  fontWeight: "fontWeightMedium",
                  color: "primary.main",
                }}
              />
            </MenuItem>
          </>
        )}
      </Menu>
      <CreateResponseSchema
        open={showCreateSchema}
        onClose={() => setShowCreateSchema(false)}
        setValue={(value) => {
          onChange(value);
        }}
      />
    </>
  );
};

ResponseFormatSelector.propTypes = {
  modelType: PropTypes.string,
  responseFormatOptions: PropTypes.array,
  responseSchema: PropTypes.array,
  selectedValue: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

export default ResponseFormatSelector;
