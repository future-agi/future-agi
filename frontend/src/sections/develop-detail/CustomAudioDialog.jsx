import React, { useEffect, useState } from "react";
import { Alert, Box, Divider, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "../../utils/axios";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import SvgColor from "src/components/svg-color/svg-color";
import { ShowComponent } from "src/components/show/ShowComponent";
import { enqueueSnackbar } from "notistack";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";

const SCREEN_STATES = {
  FORM: "form",
  LOADING: "loading",
  SUCCESS: "success",
};
const formScheama = z.object({
  voiceName: z.string().min(1, "Name is Required"),
  voiceId: z.string().min(1, "Description is Required"),
  voiceDescription: z.string().optional(),
});

const CustomAudioDialog = ({ open, onClose, selectedModel, onSuccess }) => {
  const [screenState, setScreenState] = useState(SCREEN_STATES.LOADING);

  const queryClient = useQueryClient();

  const {
    control: customVoiceControl,
    handleSubmit,
    formState: { isValid },
    reset: resetForm,
    watch,
  } = useForm({
    mode: "onChange",
    resolver: zodResolver(formScheama),
    defaultValues: {
      voiceName: "",
      voiceId: "",
      voiceDescription: "",
    },
  });

  const voiceName = watch("voiceName");

  const {
    mutate: CustomAudioCreation,
    error,
    isError,
    isPending: _isPending,
    reset: resetMutation,
  } = useMutation({
    mutationKey: ["create-custom-voice", selectedModel?.value],
    mutationFn: async (data) => {
      return axios.post(endpoints.develop.runPrompt.createCustomVoice, data);
    },
    meta: {
      errorHandled: true,
    },
    onSuccess: (response) => {
      setScreenState(SCREEN_STATES.SUCCESS);
      queryClient.invalidateQueries(["voice-options", selectedModel?.value]);
      onSuccess?.(response);
    },
    onError: (_err) => {
      setScreenState(SCREEN_STATES.FORM);
    },
  });

  const onSubmitCustomAudio = (data) => {
    resetMutation({ error: null });
    setScreenState(SCREEN_STATES.LOADING);

    const payload = {
      voice_id: data?.voiceId,
      name: data?.voiceName,
      provider: selectedModel?.providers,
      model: selectedModel?.value,
      description: data?.voiceDescription,
    };

    CustomAudioCreation(payload);
  };

  const handleModalClose = () => {
    if (screenState === SCREEN_STATES.LOADING) {
      enqueueSnackbar("Please wait until the custom audio is added.", {
        variant: "warning",
      });
      return;
    }

    resetMutation({ error: null });
    resetForm();
    onClose();

    setTimeout(() => {
      onClose?.();
    }, 50);
  };

  // Reset on modal close with cleanup
  useEffect(() => {
    if (!open) {
      const timeoutId = setTimeout(() => {
        resetMutation({ error: null });
        resetForm();
        setScreenState(SCREEN_STATES.FORM);
      }, 300);
      return () => clearTimeout(timeoutId);
    }
  }, [open, resetMutation, resetForm]);

  // Reset error state when modal opens
  useEffect(() => {
    if (open) {
      resetMutation({ error: null });
      setScreenState(SCREEN_STATES.FORM);
    }
  }, [open, resetMutation]);

  const providerName = selectedModel?.providers
    ? selectedModel.providers.charAt(0).toUpperCase() +
      selectedModel.providers.slice(1).toLowerCase()
    : "";

  return (
    <ModalWrapper
      open={open}
      isValid={
        screenState === SCREEN_STATES.SUCCESS ||
        (screenState === SCREEN_STATES.FORM && isValid)
      }
      onClose={handleModalClose}
      modalWidth="560px"
      actionBtnTitle={screenState === SCREEN_STATES.SUCCESS ? "OK" : "Save"}
      onSubmit={
        screenState === SCREEN_STATES.SUCCESS
          ? handleModalClose
          : handleSubmit(onSubmitCustomAudio)
      }
      hideCancelBtn={true}
      actionBtnSx={{
        width: "100%",
      }}
      dialogActionSx={{
        marginTop: "8px",
      }}
      disableActionBtn={screenState === SCREEN_STATES.LOADING}
    >
      <Box>
        <Typography variant="m2" fontWeight={"fontWeightSemiBold"}>
          Add Custom Audio
        </Typography>
      </Box>
      <Divider />

      {/* Screen 1: Form Input */}
      <ShowComponent condition={screenState === SCREEN_STATES.FORM}>
        <Stack spacing={2}>
          <Typography
            variant="s2_1"
            fontWeight={"fontWeightRegular"}
            sx={{
              padding: 1,
              borderRadius: "4px",
              border: "solid 1px",
              bgcolor: "background.neutral",
              borderColor: "divider",
            }}
          >
            Visit your {providerName} dashboard to get the Voice ID. Paste it
            here and give your voice a name.
          </Typography>

          <FormTextFieldV2
            fullWidth
            label={"Voice Name"}
            fieldName={"voiceName"}
            required
            control={customVoiceControl}
            size="small"
          />

          <FormTextFieldV2
            fullWidth
            label={"ID"}
            fieldName={"voiceId"}
            required
            control={customVoiceControl}
            size="small"
          />

          <FormTextFieldV2
            control={customVoiceControl}
            fieldName="voiceDescription"
            label="Description"
            multiline
            minRows={4}
            maxRows={10}
            inputProps={{
              style: {
                minHeight: "82px",
                overflowY: "auto",
                whiteSpace: "pre-wrap",
              },
            }}
            fullWidth
          />

          {isError && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {error?.result ||
                "We couldn't validate the voice—please double-check the ID."}
            </Alert>
          )}
        </Stack>
      </ShowComponent>

      {/* Screen 2: Loading State */}
      <ShowComponent condition={screenState === SCREEN_STATES.LOADING}>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 2,
          }}
        >
          <Box
            component="img"
            src="/assets/icons/ic_new_loading.svg"
            alt="loading"
            sx={{
              width: 50,
              height: 50,
              animation: "spin 1s linear infinite",
              "@keyframes spin": {
                from: { transform: "rotate(0deg)" },
                to: { transform: "rotate(360deg)" },
              },
            }}
          />
          <Typography
            variant="s1_2"
            fontWeight={"fontWeightMedium"}
            color="text.primary"
          >
            Validating voice
          </Typography>
          <Typography
            variant="s2_1"
            fontWeight={"fontWeightRegular"}
            color={"text.secondary"}
          >
            Please don&apos;t go back. You&apos;ll lose your progress and the
            voice won&apos;t be added
          </Typography>
        </Box>
      </ShowComponent>

      {/* Screen 3: Success State */}
      <ShowComponent condition={screenState === SCREEN_STATES.SUCCESS}>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 2,
            py: 4,
          }}
        >
          <Box
            sx={{
              width: 64,
              height: 64,
              borderRadius: "50%",
              bgcolor: "status.success.solid",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <SvgColor
              sx={{ height: 36, width: 36, color: "primary.contrastText" }}
              src={"/assets/icons/ic_tick.svg"}
            />
          </Box>
          <Typography
            variant="s1_2"
            fontWeight={"fontWeightMedium"}
            textAlign="center"
            color="text.primary"
          >
            Yay! The {voiceName} has been validated
          </Typography>
        </Box>
      </ShowComponent>
    </ModalWrapper>
  );
};

CustomAudioDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  selectedModel: PropTypes.shape({
    value: PropTypes.string,
    providers: PropTypes.string,
  }).isRequired,
  onSuccess: PropTypes.func,
};

export default CustomAudioDialog;
