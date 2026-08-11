import React, {
  forwardRef,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Box, Popper, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { getPopperDimensions } from "src/sections/develop-detail/DataTab/DoubleClickEditCell/editHelper";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { useNavigate } from "react-router";

import { createDraftPayload } from "../constant";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

const menuItemProps = {
  display: "flex",
  flexDirection: "column",
  justifyContent: "center",
  height: "26px",
  p: "4px",
};

const ShowPromptsComponents = forwardRef(({ open, onClose, id }, ref) => {
  const [position, setPosition] = useState();
  const popperRef = useRef(null);
  const navigate = useNavigate();

  const fieldWidth = useMemo(() => {
    return ref?.current?.offsetWidth
      ? ref?.current?.offsetWidth
      : position?.width;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, ref, position]);

  useEffect(() => {
    if (popperRef) {
      setPosition(getPopperDimensions);
    }
  }, [popperRef]);

  useEffect(() => {
    function handleClickOutside(event) {
      if (popperRef?.current && !popperRef?.current?.contains(event.target)) {
        onClose();
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [popperRef, open, onClose]);

  const { mutate: createDraft, isPending: isLoadingCreate } = useMutation({
    mutationFn: (body) =>
      axios.post(endpoints.develop.runPrompt.createPromptDraft, body),
    onSuccess: (data) => {
      enqueueSnackbar("Prompt created successfully.", {
        variant: "success",
      });
      trackEvent(Events.promptCreateClicked, {
        [PropertyName.click]: true,
      });
      navigate(
        `/dashboard/workbench/create/${data?.data?.result?.root_template}`,
      );
    },
  });

  const handleWritePrompt = useCallback(() => {
    createDraft(createDraftPayload);
  }, [createDraft]);

  const handleGeneratePrompt = () => {};

  const handleExistingPrompt = () => {};

  const prompts = useMemo(() => {
    return [
      {
        title: isLoadingCreate ? "Creating..." : "Write a prompt from scratch",
        onClick: handleWritePrompt,
        availableFeature: true,
      },
      {
        title: "Generate a prompt",
        onClick: handleGeneratePrompt,
        availableFeature: false,
      },
      {
        title: "Improve an existing prompt",
        onClick: handleExistingPrompt,
        availableFeature: false,
      },
    ];
  }, [isLoadingCreate, handleWritePrompt]);

  return (
    <Popper
      id={id}
      anchorEl={ref?.current}
      open={open}
      ref={popperRef}
      placement="bottom-end"
      modifiers={modifier}
      onClick={(e) => e.stopPropagation()}
    >
      <Box
        sx={{
          maxHeight: position?.height,
          minWidth: fieldWidth,
          bgcolor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
          p: "8px",
          top: "2px",
          overflowY: "auto",
          borderRadius: "4px",
          position: "relative",
        }}
      >
        <Box display={"flex"} flexDirection={"column"} gap={0.5}>
          {prompts.map((item, index) => (
            <Box
              key={index}
              sx={{
                ...menuItemProps,
                cursor: item.availableFeature ? "pointer" : "not-allowed",
              }}
              onClick={() => {
                if (!item.availableFeature) {
                  return;
                }
                item?.onClick?.();
              }}
            >
              <Typography
                variant="s2"
                fontWeight={"fontWeightRegular"}
                color={item.availableFeature ? "text.primary" : "text.disabled"}
              >
                {item?.title}
              </Typography>
            </Box>
          ))}
        </Box>
      </Box>
    </Popper>
  );
});

export default ShowPromptsComponents;

ShowPromptsComponents.displayName = "ShowPromptsComponents";

ShowPromptsComponents.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  id: PropTypes.string,
};

const modifier = [
  {
    name: "flip",
    enabled: true,
    options: {
      altBoundary: true,
      rootBoundary: "document",
    },
  },
  {
    name: "preventOverflow",
    enabled: true,
    options: {
      altAxis: true,
      altBoundary: true,
      tether: true,
      rootBoundary: "document",
    },
  },
];
