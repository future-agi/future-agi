import { Box, Collapse, IconButton, MenuItem, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import Iconify from "src/components/iconify";
import TextLabelField from "./LabelFields/TextLabelField";
import NumericLabelField from "./LabelFields/NumericLabelField";
import StarLabelField from "./LabelFields/StarLabelField";
import ThumbsUpDownLabelField from "./LabelFields/ThumbsUpDownLabelField";
import CategoricalLabelField from "./LabelFields/CategoricalLabelField";
import { ShowComponent } from "src/components/show";
import CustomPopover from "src/components/custom-popover";
import SvgColor from "../../../../components/svg-color";

const LabelBgColorMapper = {
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  numeric: "#007AFF26",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  text: "#7857FC26",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  categorical: "#4CAF5033",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  thumbs_up_down: "#FF98004D",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  star: "#CF6BE84D",
};

const AnnotationFieldWrapper = ({
  index,
  labelName,
  type,
  settings,
  control,
  fieldName,
  disableHotkeys = false,
  defaultOpen = false,
  collapsable = true,
  error,
  showActionButtons = false,
  onEditLabelClick = null,
  onDeleteLabelClick = null,
}) => {
  const actions = [
    {
      label: "Edit",
      icon: "/assets/icons/ic_edit.svg",
      action: onEditLabelClick,
      color: "text.primary",
    },
    {
      label: "Delete",
      icon: "/assets/icons/ic_delete.svg",
      action: onDeleteLabelClick,
      color: "status.danger.fg",
    },
  ];
  const [open, setOpen] = useState(defaultOpen);
  const [actionButtonsOpen, setActionsButtonsOpen] = useState(false);
  useHotkeys(
    [`meta+${index + 1}`, `cmd+${index + 1}`, `ctrl+${index + 1}`],
    (e) => {
      e.preventDefault();
      setOpen((prev) => !prev);
    },
    {
      preventDefault: true,
      enableOnFormTags: true,
      enabled: !disableHotkeys,
    },
  );

  const renderLabelPreview = () => {
    switch (type) {
      case "text":
        return (
          <TextLabelField
            label={labelName}
            settings={settings}
            control={control}
            fieldName={fieldName}
            error={error}
          />
        );
      case "numeric":
        return (
          <NumericLabelField
            label={labelName}
            settings={settings}
            control={control}
            fieldName={fieldName}
          />
        );
      case "star":
        return (
          <StarLabelField
            label={labelName}
            settings={settings}
            control={control}
            fieldName={fieldName}
          />
        );
      case "thumbs_up_down":
        return (
          <ThumbsUpDownLabelField
            label={labelName}
            settings={settings}
            control={control}
            fieldName={fieldName}
          />
        );
      case "categorical":
        return (
          <CategoricalLabelField
            label={labelName}
            settings={settings}
            control={control}
            fieldName={fieldName}
          />
        );
      default:
        return null;
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        width: "100%", // Full width
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            width: "50px", // Fixed width for the ID box
            height: "50px", // Height for the ID box
            backgroundColor: LabelBgColorMapper[type] || "action.selected",
            zIndex: 1,
          }}
        >
          <Typography
            variant="body2"
            sx={{ color: "text.primary", fontWeight: "600" }}
          >
            {index + 1}
          </Typography>
        </Box>
        <Box
          sx={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            paddingLeft: 1,
            marginRight: 1,
          }}
        >
          <Box sx={{ flex: 1 }}>
            <Typography variant="subtitle2" fontWeight={400}>
              {labelName}
            </Typography>
          </Box>
          <ShowComponent condition={showActionButtons}>
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                setActionsButtonsOpen(e.currentTarget);
              }}
            >
              <SvgColor
                src="/assets/icons/ic_ellipsis.svg"
                sx={{
                  color: "text.secondary",
                  height: 24,
                  width: 24,
                  rotate: "90deg",
                }}
              />
            </IconButton>
            <CustomPopover
              open={actionButtonsOpen}
              onClose={() => setActionsButtonsOpen(null)}
              arrow="top-right"
            >
              {actions.map((action) => (
                <MenuItem
                  key={action.label}
                  onClick={() => {
                    action.action?.();
                    setActionsButtonsOpen(null);
                  }}
                  sx={{
                    display: "flex",
                    flexDirection: "row",
                    alignItems: "center",
                    gap: 0.5,
                    // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
                    color: action.color,
                  }}
                >
                  <SvgColor src={action.icon} sx={{ width: 16, height: 16 }} />
                  <Typography
                    typography={"s2_1"}
                    fontWeight={"fontweightMedium"}
                  >
                    {action?.label}
                  </Typography>
                </MenuItem>
              ))}
            </CustomPopover>
          </ShowComponent>
          {!disableHotkeys && (
            <Typography
              variant="body2"
              sx={{
                display: "flex", // Use flex to align items horizontally
                alignItems: "center", // Vertically center align the icon and text
                border: "1px solid",
                borderColor: "divider",
                padding: "1px 5px",
                borderRadius: "6px",
                marginRight: 1,
              }}
            >
              <Iconify
                icon="solar:code-linear"
                color="text.primary"
                width={16}
                style={{ marginRight: "5px" }}
              />
              {index + 1}
            </Typography>
          )}
          <ShowComponent condition={collapsable}>
            <Box>
              <IconButton onClick={() => setOpen(!open)}>
                <Iconify
                  icon="solar:alt-arrow-down-linear"
                  color="text.primary"
                  width={20}
                  style={{
                    transform: open ? "rotate(180deg)" : "rotate(0deg)",
                  }}
                />
              </IconButton>
            </Box>
          </ShowComponent>
        </Box>
      </Box>
      <Collapse in={open}>
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            padding: 1,
            paddingX: 2,
          }}
        >
          {renderLabelPreview()}
        </Box>
      </Collapse>
    </Box>
  );
};

AnnotationFieldWrapper.propTypes = {
  index: PropTypes.number,
  labelName: PropTypes.string,
  type: PropTypes.string,
  settings: PropTypes.object,
  control: PropTypes.object,
  fieldName: PropTypes.string,
  disableHotkeys: PropTypes.bool,
  defaultOpen: PropTypes.bool,
  collapsable: PropTypes.bool,
  error: PropTypes.object,
  showActionButtons: PropTypes.bool,
  onEditLabelClick: PropTypes.func,
  onDeleteLabelClick: PropTypes.func,
};

export default AnnotationFieldWrapper;
