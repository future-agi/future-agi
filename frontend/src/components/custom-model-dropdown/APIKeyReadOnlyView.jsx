import React from "react";
import { Box, IconButton, Tooltip, Typography, useTheme } from "@mui/material";
import { JsonView, allExpanded } from "react-json-view-lite";
import { ShowComponent } from "../show";
import Iconify from "../iconify";
import { copyToClipboard } from "src/utils/utils";
import PropTypes from "prop-types";
import "./ApiKeyForm.css";
import { safeParseJson } from "./KeysHelper";
import SvgColor from "../svg-color";
import CloudProviderModals from "./CloudProviderModals";
import { enqueueSnackbar } from "notistack";

const APIKeyReadOnlyView = ({
  keyValue = "",
  isJsonKey = false,
  showJsonField = true,
  openModal,
  setOpenModal,
  label = "API Key",
  provider = {},
  onSubmit = () => {},
  onDeleteClick,
}) => {
  const theme = useTheme();
  const keyValueIsObject = keyValue && typeof keyValue === "object";
  const showAsJson = showJsonField && (isJsonKey || keyValueIsObject);
  const textKeyValue = keyValueIsObject
    ? JSON.stringify(keyValue, null, 2)
    : keyValue;
  const readOnlyBoxStyles = {
    border: "1px solid var(--border-default)",
    borderRadius: theme.spacing(0.5),
    padding: theme.spacing(2),
    backgroundColor: "background.default",
    position: "relative",
  };

  const readOnlyKeyTextStyles = {
    wordBreak: "break-all",
    color: "text.primary",
    fontSize: "13px",
  };

  const jsonReadOnlyBoxStyles = {
    position: "relative",
    border: "1px solid var(--border-default)",
    borderRadius: theme.spacing(0.5),
    cursor: "default",
    paddingY: theme.spacing(1.5),
    backgroundColor: "background.default",
    maxHeight: "250px",
    overflow: "auto",
    fontSize: "12px",
  };

  const copyButtonStyles = {
    position: "absolute",
    top: theme.spacing(0.5),
    right: theme.spacing(6.5),
    zIndex: 1,
  };

  const editButtonStyles = {
    position: "absolute",
    top: theme.spacing(0.5),
    right: theme.spacing(3),
    zIndex: 1,
  };
  const deleteButtonStyles = {
    position: "absolute",
    top: theme.spacing(0.5),
    right: theme.spacing(0),
    zIndex: 1,
  };

  const jsonViewStyle = {
    container: "attributesJsonContainer",
    basicChildStyle: "attributesJsonChild",
    label: "attributesLabel",
    clickableLabel: "attributesClickableLabel",
    nullValue: "attributesNullValue",
    undefinedValue: "attributesUndefinedValue",
    numberValue: "attributesNumberValue",
    stringValue: "attributesStringValue",
    booleanValue: "attributesBooleanValue",
    otherValue: "attributesOtherValue",
    punctuation: "attributesPunctuation",
    expandIcon: "customExpandIcon",
  };

  return (
    <>
      <ShowComponent condition={showJsonField && !showAsJson}>
        <Box sx={readOnlyBoxStyles}>
          <Typography
            typography="s2"
            fontWeight="fontWeightMedium"
            sx={{ color: "text.disabled" }}
          >
            {label}
          </Typography>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography
              fontWeight="fontWeightRegular"
              sx={readOnlyKeyTextStyles}
            >
              {textKeyValue || "-"}
            </Typography>

            {/* <Tooltip title="Copy">
              <IconButton
                size="small"
                sx={copyButtonStyles}
                onClick={() => {
                  if (keyValue) {
                    copyToClipboard(keyValue);
                    enqueueSnackbar("Copied to clipboard", {
                      variant: "success",
                    });
                  }
                }}
              >
                <Iconify icon="basil:copy-outline" sx={{ color: "text.disabled" }} />
              </IconButton>
            </Tooltip>

            <Tooltip title="Edit">
              <IconButton
                size="small"
                sx={editButtonStyles}
                onClick={() => {
                  if (keyValue) setOpenModal(true);
                }}
              >
                <SvgColor
                  src="/assets/prompt/editPencil.svg"
                  sx={{
                    color: "text.disabled",
                    width: theme.spacing(2.5),
                    height: theme.spacing(2.5),
                  }}
                />
              </IconButton>
            </Tooltip> */}
          </Box>
        </Box>
      </ShowComponent>

      <ShowComponent condition={showAsJson}>
        <Box sx={jsonReadOnlyBoxStyles}>
          <Tooltip title="Copy">
            <IconButton
              size="small"
              sx={copyButtonStyles}
              onClick={() => {
                if (textKeyValue) {
                  copyToClipboard(textKeyValue);
                  enqueueSnackbar("Copied to clipboard", {
                    variant: "success",
                  });
                }
              }}
            >
              <Iconify
                icon="basil:copy-outline"
                sx={{ color: "text.disabled" }}
              />
            </IconButton>
          </Tooltip>
          <Tooltip title="Edit">
            <IconButton
              size="small"
              sx={editButtonStyles}
              onClick={() => {
                if (keyValue) setOpenModal(true);
              }}
            >
              <SvgColor
                src="/assets/prompt/editPencil.svg"
                sx={{
                  color: "text.disabled",
                  width: theme.spacing(2.5),
                  height: theme.spacing(2.5),
                }}
              />
            </IconButton>
          </Tooltip>
          <Tooltip title="Delete">
            <IconButton
              size="small"
              sx={deleteButtonStyles}
              onClick={onDeleteClick}
            >
              <SvgColor
                src="/assets/icons/ic_delete.svg"
                sx={{
                  color: "text.disabled",
                  width: theme.spacing(2.5),
                  height: theme.spacing(2.5),
                }}
              />
            </IconButton>
          </Tooltip>
          <JsonView
            data={safeParseJson(keyValue)}
            shouldExpandNode={allExpanded}
            style={jsonViewStyle}
          />
        </Box>
      </ShowComponent>
      <ShowComponent condition={openModal}>
        <CloudProviderModals
          provider={provider}
          onClose={() => {
            setOpenModal(false);
          }}
          onSubmit={onSubmit}
        />
      </ShowComponent>
    </>
  );
};

APIKeyReadOnlyView.propTypes = {
  keyValue: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  isJsonKey: PropTypes.bool,
  showJsonField: PropTypes.bool,
  label: PropTypes.string,
  setOpenModal: PropTypes.func,
  openModal: PropTypes.bool,
  provider: PropTypes.object,
  onSubmit: PropTypes.func,
  onDeleteClick: PropTypes.func,
};

export default APIKeyReadOnlyView;
