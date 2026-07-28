import { Box, Popover } from "@mui/material";
import PropTypes from "prop-types";
import React, { forwardRef, useEffect, useMemo, useRef, useState } from "react";
import ModelOptionsItems from "./ModelOptionsItems";

const ShowModelOptionsChild = (
  {
    open,
    id,
    onClose,
    control,
    responseSchema,
    modelParams,
    modelConfig,
    voiceOptions,
  },
  ref,
) => {
  const [position, setPosition] = useState({ width: 0, height: 0 });
  const popperRef = useRef(null);
  const [disableClickOutside, setDisabledClickOutside] = useState(false);

  const fieldWidth = useMemo(() => {
    return ref?.current?.offsetWidth
      ? ref?.current?.offsetWidth
      : position?.width;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, ref, position]);

  useEffect(() => {
    if (popperRef) {
      setPosition({ width: 500, height: 500 });
    }
  }, [popperRef]);

  useEffect(() => {
    function handleClickOutside(event) {
      if (popperRef?.current && !popperRef?.current?.contains(event.target)) {
        onClose();
      }
    }
    if (!disableClickOutside) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [popperRef, open, disableClickOutside]);

  return (
    <Popover
      id={id}
      anchorEl={ref?.current}
      open={open}
      ref={popperRef}
      onClose={onClose}
      onClick={(e) => e.stopPropagation()}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "left",
      }}
      sx={{
        zIndex: 10,
        "& .MuiPaper-root": {
          bgcolor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
          p: "12px",
          marginTop: "4px",
          borderRadius: "4px !important",
        },
      }}
    >
      <Box
        sx={{
          maxHeight: position?.height,
          minWidth: fieldWidth,
          width: "360px",
          minHeight: "200px",
          height: "100%",
        }}
      >
        <ModelOptionsItems
          control={control}
          fieldNamePrefix={"config"}
          setDisabledClickOutside={setDisabledClickOutside}
          responseSchema={responseSchema}
          module="workbench"
          items={modelParams?.sliders}
          responseFormat={modelParams?.responseFormat}
          dropdowns={modelParams?.dropdowns}
          reasoning={modelParams?.reasoning}
          hideResponseFormat
          modelConfig={modelConfig}
          voiceOptions={voiceOptions}
        />
      </Box>
    </Popover>
  );
};

const showModelOptionsPropTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  id: PropTypes.string,
  control: PropTypes.any,
  responseSchema: PropTypes.array,
  modelParams: PropTypes.shape({
    sliders: PropTypes.any,
    responseFormat: PropTypes.any,
    dropdowns: PropTypes.any,
    reasoning: PropTypes.any,
  }),
  modelConfig: PropTypes.object,
  voiceOptions: PropTypes.object,
};

ShowModelOptionsChild.propTypes = showModelOptionsPropTypes;

// @ts-ignore
ShowModelOptionsChild.propTypes = showModelOptionsPropTypes;
export const ShowModelOptions = forwardRef(ShowModelOptionsChild);
ShowModelOptions.propTypes = showModelOptionsPropTypes;
