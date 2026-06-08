import React, { useCallback, useMemo, useState } from "react";
import {
  Box,
  Chip,
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
import { useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { createDraftPayload } from "src/sections/workbench/constant";
import { usePromptStore } from "../store/usePromptStore";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import {
  buildPromptCreatedHref,
  buildPromptCreatedPayload,
  getPromptOnboardingRouteParams,
} from "src/sections/workbench/createPrompt/promptActions/promptOnboardingRoute";

const GUIDED_PROMPT_STEPS = [
  "Create prompt",
  "Run test",
  "Save baseline",
  "Compare version",
];

const guidedPromptOptionOrder = (option) =>
  option.id === "start_from_scratch"
    ? 0
    : option.id === "start_with_template"
      ? 1
      : 2;

function PromptItem({
  desc,
  helperLabel,
  icon,
  isRecommended = false,
  name,
  onClick,
  tourAnchor,
}) {
  const theme = useTheme();
  return (
    <Stack
      component={"div"}
      data-tour-anchor={tourAnchor || undefined}
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
      <Stack spacing={0.5}>
        <Stack direction="row" spacing={0.75} alignItems="center">
          <Typography
            variant="m3"
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            {name}
          </Typography>
          {isRecommended ? (
            <Chip size="small" color="primary" label="Recommended" />
          ) : null}
        </Stack>
        <Typography
          variant="s2"
          fontWeight={"fontWeightRegular"}
          color={"text.primary"}
        >
          {desc}
        </Typography>
        {helperLabel ? (
          <Typography
            variant="caption"
            fontWeight={"fontWeightRegular"}
            color="text.secondary"
          >
            {helperLabel}
          </Typography>
        ) : null}
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
  desc: PropTypes.string,
  helperLabel: PropTypes.string,
  icon: PropTypes.string,
  isRecommended: PropTypes.bool,
  name: PropTypes.string,
  onClick: PropTypes.func,
  tourAnchor: PropTypes.string,
};
export default function CreateNewPrompt({ open, onClose, isLoading }) {
  const theme = useTheme();
  const { folder } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [selectedOption, setSelectedOption] = useState(null);
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const { setSelectTemplateDrawerOpen, selectTemplateDrawerOpen } =
    usePromptStore();
  const promptOnboardingParams = useMemo(
    () => getPromptOnboardingRouteParams(searchParams),
    [searchParams],
  );
  const isGuidedPromptCreate = promptOnboardingParams.isOnboarding;
  const promptOptions = useMemo(() => {
    const options = CREATE_PROMPT_OPTIONS.filter((option) => {
      if (option?.id === "start_with_template" && selectTemplateDrawerOpen) {
        return false;
      }
      return true;
    });
    if (!isGuidedPromptCreate) return options;
    return [...options].sort(
      (left, right) =>
        guidedPromptOptionOrder(left) - guidedPromptOptionOrder(right),
    );
  }, [isGuidedPromptCreate, selectTemplateDrawerOpen]);

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
      const promptId = data?.data?.result?.rootTemplate;
      if (promptOnboardingParams.isOnboarding && promptId) {
        recordActivationEvent(
          buildPromptCreatedPayload({
            promptId,
            search: searchParams,
          }),
        );
      }
      navigate(buildPromptCreatedHref({ promptId, search: searchParams }), {
        state: { fromOption: selectedOption },
      });
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
            {isGuidedPromptCreate
              ? "Create a prompt to test"
              : "Create a new prompt"}
          </Typography>
          {isGuidedPromptCreate ? (
            <Typography
              typography="s2"
              color="text.secondary"
              fontWeight={"fontWeightRegular"}
              sx={{ mt: 0.5, maxWidth: 480, lineHeight: 1.4 }}
            >
              Start with one prompt, run it with a real example, save the
              baseline, then compare the next version.
            </Typography>
          ) : null}
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
          {isGuidedPromptCreate ? (
            <Box
              data-testid="prompt-create-setup-guide"
              sx={{
                border: "1px solid",
                borderColor: "primary.main",
                borderRadius: 1,
                bgcolor: "action.hover",
                p: 1.25,
              }}
            >
              <Stack spacing={1}>
                <Typography variant="subtitle2">Prompt setup path</Typography>
                <Stack
                  direction="row"
                  spacing={0.75}
                  flexWrap="wrap"
                  useFlexGap
                >
                  {GUIDED_PROMPT_STEPS.map((step, index) => (
                    <Chip
                      key={step}
                      size="small"
                      color={index === 0 ? "primary" : "default"}
                      label={`${index + 1}. ${step}`}
                      variant={index === 0 ? "filled" : "outlined"}
                    />
                  ))}
                </Stack>
              </Stack>
            </Box>
          ) : null}
          {promptOptions.map((option, index) => (
            <PromptItem
              key={index}
              desc={
                isGuidedPromptCreate && option.id === "start_from_scratch"
                  ? "Write one prompt manually, pick a model, and run it."
                  : option.desc
              }
              helperLabel={
                isGuidedPromptCreate && option.id === "start_from_scratch"
                  ? "This starts with a prompt you can run and version immediately."
                  : null
              }
              icon={option.icon}
              isRecommended={
                isGuidedPromptCreate && option.id === "start_from_scratch"
              }
              name={option.name}
              onClick={() => {
                if (isLoadingCreate) return;
                handleAction(option.id);
              }}
              tourAnchor={
                promptOnboardingParams.isOnboarding &&
                option.id === "start_from_scratch"
                  ? promptOnboardingParams.tourAnchor
                  : null
              }
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
