import { LoadingButton } from "@mui/lab";
import {
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

import VersionStyle from "./VersionStyle";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

// IT-R2 G4 P2: the message is optional (empty = the version name, `V1`);
// the first commit of a prompt asks for its name (prefilled with the
// current one, `Untitled prompt 1`) and saves it first.
const SaveAndCommit = ({ open, onClose, data, promptName, askName }) => {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const [messageType, setMessageType] = useState(null);

  const { control, watch, reset, handleSubmit, setValue } = useForm({
    defaultValues: { message: "", name: promptName ?? "" },
  });
  const message = watch("message");
  const name = watch("name");
  useEffect(() => {
    if (open) setValue("name", promptName ?? "");
  }, [open, promptName, setValue]);

  const handleOnClose = () => {
    reset();
    onClose();
  };

  const payload = {
    message: String(message ?? "").trim() || data?.version || "",
    set_default: data?.isDefault,
    is_draft: false,
    version_name: data?.version,
  };

  const onSubmit = () => {
    const newPayload = { ...payload };
    newPayload.set_default = true;
    setMessageType("saveCommit");
    // @ts-ignore
    mutate(newPayload);
  };

  const commitOnly = () => {
    const newPayload = { ...payload };
    setMessageType("commitOnly");
    // @ts-ignore
    mutate(newPayload);
  };

  const { mutate, isPending } = useMutation({
    mutationFn: async (data) => {
      const newName = String(name ?? "").trim();
      if (askName && newName && newName !== promptName) {
        await axios.post(endpoints.develop.runPrompt.getNameChange(id), {
          name: newName,
        });
      }
      return axios.post(
        `${endpoints.develop.runPrompt.commitSavePrompt(id)}`,
        data,
      );
    },
    onSuccess: () => {
      enqueueSnackbar(
        <>
          Commit for {String(name ?? "").trim() || promptName}&nbsp;
          <VersionStyle text={data?.version} />
          &nbsp;successful
          {messageType === "commitOnly" ? "" : " and set as default"}
        </>,
        { variant: "info" },
      );
      trackEvent(Events.promptCommitClicked, {
        [PropertyName.promptId]: id,
      });
      queryClient.invalidateQueries({
        queryKey: ["prompt-versions", id],
      });
      queryClient.invalidateQueries({
        queryKey: ["prompt-latest-version", id],
      });
      handleOnClose();
    },
  });

  return (
    <Dialog
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      open={open}
      onClose={handleOnClose}
    >
      <DialogTitle sx={{ pb: "0" }}>
        <Stack
          direction={"row"}
          justifyContent={"space-between"}
          alignItems={"center"}
        >
          <Typography
            typography="m3"
            fontWeight={"fontWeightBold"}
            color={"text.primary"}
          >
            Commit changes to prompt
          </Typography>
          <IconButton
            disabled={isPending}
            sx={{
              color: "text.primary",
              padding: 0,
              margin: 0,
            }}
            onClick={handleOnClose}
          >
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Stack>
      </DialogTitle>
      <Box component={"form"} onSubmit={handleSubmit(onSubmit)}>
        <DialogContent>
          {askName ? (
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: 0.5,
                padding: "12px 16px",
                mt: 2,
                backgroundColor: "background.default",
                border: (theme) => `1px solid ${theme.palette.divider}`,
                borderRadius: 0.5,
              }}
            >
              <Typography
                typography="s1"
                fontWeight={"fontWeightMedium"}
                color="text.primary"
              >
                Prompt name
              </Typography>
              <FormTextFieldV2
                control={control}
                hiddenLabel
                fieldName="name"
                fullWidth
                variant="standard"
                placeholder="Name this prompt"
                size="small"
                InputProps={{ disableUnderline: true }}
                inputProps={{ "data-testid": "commit-prompt-name" }}
                sx={{ "& .MuiInputBase-input": { padding: 0 } }}
              />
            </Box>
          ) : null}
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 0.5,
              padding: "12px 16px",
              mt: 2,
              backgroundColor: "background.default",

              border: (theme) => `1px solid ${theme.palette.divider}`,
              borderRadius: "4px",
            }}
          >
            <Typography
              typography="s1"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
            >
              Commit message{" "}
              <Box
                component="span"
                sx={{ color: "text.secondary", fontWeight: 400 }}
              >
                (optional · defaults to {data?.version ?? "the version name"})
              </Box>
            </Typography>
            <FormTextFieldV2
              control={control}
              hiddenLabel
              fieldName="message"
              fullWidth
              variant="standard"
              placeholder="Enter a commit message for this version..."
              size="small"
              multiline
              rows={5}
              InputProps={{ disableUnderline: true }}
              sx={{
                flexGrow: 1,
                fontSize: "16px",
                "& .MuiInputBase-input": {
                  padding: 0,
                },
              }}
            />
          </Box>
        </DialogContent>

        <DialogActions sx={{ pt: "32px" }}>
          <LoadingButton
            disabled={isPending}
            loading={isPending}
            variant="outlined"
            type="button"
            onClick={commitOnly}
          >
            Commit
          </LoadingButton>
          <LoadingButton
            loading={isPending}
            variant="contained"
            color="primary"
            type="submit"
            disabled={isPending}
          >
            Commit and set as a default version
          </LoadingButton>
        </DialogActions>
      </Box>
    </Dialog>
  );
};

export default SaveAndCommit;

SaveAndCommit.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  data: PropTypes.object,
  promptName: PropTypes.string,
  askName: PropTypes.bool,
};
