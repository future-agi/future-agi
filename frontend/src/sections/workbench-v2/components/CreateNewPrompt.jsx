import React, { useCallback, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { CREATE_PROMPT_OPTIONS } from "../common";
import { useNavigate, useParams } from "react-router";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { createDraftPayload } from "src/sections/workbench/constant";
import { usePromptStore } from "../store/usePromptStore";

function PromptItem({ name, desc, icon, onClick }) {
  const theme = useTheme();
  return (
    <Stack
      component={"div"}
      onClick={onClick}
      sx={{
        padding: theme.spacing(2, 1.5),
        bgcolor: "background.default",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        cursor: "pointer",
        "&:hover": {
          bgcolor: "background.neutral",
        },
      }}
      direction={"row"}
      justifyContent={"space-between"}
      alignItems={"center"}
    >
      <Stack>
        <Typography
          variant="m3"
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
        >
          {name}
        </Typography>
        <Typography
          variant="s2"
          fontWeight={"fontWeightRegular"}
          color={"text.primary"}
        >
          {desc}
        </Typography>
      </Stack>
      <SvgColor
        src={icon}
        sx={{
          color: "text.secondary",
          height: 30,
          width: 30,
        }}
      />
    </Stack>
  );
}

PromptItem.propTypes = {
  name: PropTypes.string,
  desc: PropTypes.string,
  icon: PropTypes.string,
  onClick: PropTypes.func,
};
export default function CreateNewPrompt({ open, onClose, isLoading }) {
  const theme = useTheme();
  const { folder } = useParams();
  const navigate = useNavigate();
  const [selectedOption, setSelectedOption] = useState(null);
  const { setSelectTemplateDrawerOpen, selectTemplateDrawerOpen } =
    usePromptStore();

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
        `/dashboard/workbench/create/${data?.data?.result?.rootTemplate}`,
        {
          state: { fromOption: selectedOption },
        },
      );
      onClose();
      setSelectTemplateDrawerOpen(false);
    },
  });

  const handleWritePrompt = useCallback(() => {
    if (!folder) return;
    createDraft({
      ...createDraftPayload,
      ...(folder !== "all" && folder !== "my-templates"
        ? { prompt_folder: folder }
        : {}),
    });
  }, [createDraft, folder]);

  const handleAction = (itemId) => {
    setSelectedOption(itemId);
    trackEvent(Events.promptNewPromptModeSelected, {
      [PropertyName.type]: itemId,
    });
    switch (itemId) {
      case "gen_ai":
        handleWritePrompt();
        break;
      case "start_from_scratch":
        handleWritePrompt();
        break;
      case "start_with_template":
        onClose();
        setSelectTemplateDrawerOpen(true);
        break;

      default:
        break;
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: "570px",
          borderRadius: theme.spacing(1),
          padding: theme.spacing(2),
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
        },
      }}
    >
      <DialogTitle sx={{ padding: 0, lineHeight: 0 }}>
        <Stack>
          <Typography
            typography={"m3"}
            color={"text.primary"}
            fontWeight={"fontWeightSemiBold"}
          >
            Create a new prompt
          </Typography>
          <IconButton
            disabled={isLoading}
            onClick={onClose}
            sx={{
              position: "absolute",
              top: "12px",
              right: "12px",
              color: "text.primary",
            }}
          >
            <Iconify icon="akar-icons:cross" />
          </IconButton>
        </Stack>
      </DialogTitle>
      <DialogContent sx={{ padding: 0, lineHeight: 0 }}>
        <Stack direction={"column"} gap={1.5}>
          {CREATE_PROMPT_OPTIONS.filter((option) => {
            if (
              option?.id === "start_with_template" &&
              selectTemplateDrawerOpen
            ) {
              return false;
            }
            return true;
          }).map((option, index) => (
            <PromptItem
              key={index}
              desc={option.desc}
              icon={option.icon}
              name={option.name}
              onClick={() => {
                if (isLoadingCreate) return;
                handleAction(option.id);
              }}
            />
          ))}
        </Stack>
      </DialogContent>
    </Dialog>
  );
}

CreateNewPrompt.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  isLoading: PropTypes.bool,
};
