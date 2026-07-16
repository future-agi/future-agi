import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { ShowModelOptions } from "./ShowModelOptions";
import CustomTooltip from "../tooltip";
import ToolHoverState from "./ToolHoverState";
import SvgColor from "../svg-color";

const CustomModelOptions = ({
  isModalContainer = false,
  control,
  handleApply,
  reset,
  setValue,
  responseSchema,
  modelConfig,
  disabledHover = false,
  disabledClick = false,
  viewOnly = false,
  hoverPlacement = "bottom",
  isDirty,
  onClick = () => {},
  modelParams,
  ...rest
}) => {
  const btnRef = useRef(null);
  const [openDropdown, setOpenDropdown] = useState(false);
  const [toolConfig, setToolConfig] = useState({});

  const handleOnClose = () => {
    if (isDirty) {
      handleApply();
    }
    setOpenDropdown(false);
    setTimeout(() => {
      setConfigData();
      reset();
    }, 300);
  };
  const setToolValue = useCallback(() => {
    const configData = {
      ...modelConfig,
      ...(modelConfig.responseFormat && {
        responseFormat:
          typeof modelConfig?.responseFormat === "string"
            ? modelConfig?.responseFormat
            : modelConfig?.responseFormat?.id,
      }),
    };
    return configData;
  }, [modelConfig]);

  const handleOpenDropdown = () => {
    // View-only (e.g. version history): the button stays enabled so the
    // read-only params hover renders, but clicking must not open the editor.
    if (viewOnly) return;
    if (!openDropdown) {
      onClick?.();
      setValue("config", setToolValue());
    }
    setOpenDropdown((pre) => !pre);
  };
  const setConfigData = useCallback(() => {
    const configData = setToolValue();
    setToolConfig({
      ...configData,
      ...(modelConfig.responseFormat && {
        responseFormat:
          typeof modelConfig?.responseFormat === "string"
            ? modelConfig?.responseFormat
            : modelConfig?.responseFormat?.id,
      }),
    });
  }, [modelConfig?.responseFormat, setToolValue]);

  useEffect(() => {
    if (!disabledHover) {
      setConfigData();
    }
  }, [disabledHover, setConfigData]);

  const id = useMemo(
    () => (openDropdown ? `model-popper` : undefined),
    [openDropdown],
  );
  return (
    <Box
      sx={{
        ...(isModalContainer
          ? {
              flexShrink: -1,
              paddingRight: "6px",
              height: "24px",
            }
          : { height: "32px" }),
      }}
    >
      <CustomTooltip
        show={!openDropdown}
        placement={!disabledHover ? hoverPlacement : "bottom"}
        title={
          <ToolHoverState config={toolConfig} disabledHover={disabledHover} />
        }
        arrow={disabledHover}
        enterDelay={100}
        size="small"
        enterNextDelay={100}
        slotProps={{
          popper: {
            modifiers: [
              {
                name: "offset",
                options: {
                  offset: disabledHover ? [0, 0] : [0, -10],
                },
              },
            ],
          },
        }}
        sx={{
          ...(!disabledHover && {
            "& .MuiTooltip-tooltip": {
              padding: 0,
              minWidth: "300px",
            },
          }),
        }}
      >
        <IconButton
          disabled={disabledClick}
          disableRipple={viewOnly}
          sx={{
            height: "24px",
            color: "text.primary",
            ...(viewOnly && { cursor: "default" }),
            ...(isModalContainer
              ? {
                  borderRadius: "4px",

                  height: "24px",
                  padding: "2px",
                  marginTop: "-4px",
                  gap: "4px",
                }
              : {
                  borderRadius: "4px",
                  backgroundColor: "background.paper",
                  border: "1px solid",
                  borderColor: "divider",
                  height: "32px",
                  paddingX: 1.5,
                  paddingY: 0.5,
                  marginTop: "4px",
                }),
          }}
          onClick={handleOpenDropdown}
        >
          <SvgColor
            src="/assets/prompt/slider-options.svg"
            sx={{ height: "16px", width: "16px" }}
          />
          <Typography sx={{ fontSize: "14px", fontWeight: 500 }}>
            Params
          </Typography>
        </IconButton>
      </CustomTooltip>
      <Box aria-describedby={id} ref={btnRef} height={0} />
      <ShowModelOptions
        open={openDropdown && !disabledClick}
        onClose={handleOnClose}
        id={id}
        ref={btnRef}
        control={control}
        responseSchema={responseSchema}
        modelParams={modelParams}
        modelConfig={modelConfig}
        {...rest}
      />
    </Box>
  );
};

export default CustomModelOptions;

CustomModelOptions.propTypes = {
  isModalContainer: PropTypes.bool,
  control: PropTypes.any,
  handleApply: PropTypes.func,
  reset: PropTypes.func,
  responseSchema: PropTypes.array,
  setValue: PropTypes.func,
  modelConfig: PropTypes.object,
  disabledHover: PropTypes.bool,
  disabledClick: PropTypes.bool,
  viewOnly: PropTypes.bool,
  hoverPlacement: PropTypes.string,
  isDirty: PropTypes.bool,
  onClick: PropTypes.func,
  modelParams: PropTypes.object,
};
