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
import React, { useState } from "react";
import { useForm } from "react-hook-form";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

import VersionStyle from "./VersionStyle";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

const SaveAndCommit = ({ open, onClose, data, promptName }) => {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const [messageType, setMessageType] = useState(null);

  const {
    control,
    watch,
    reset,
    handleSubmit,
    formState: { isDirty },
  } = useForm({
    defaultValues: { message: "" },
  });
  const message = watch("message");

  const handleOnClose = () => {
    reset();
    onClose();
  };

  const payload = {
    message: message,
    set_default: data?.isDefault,
    is_draft: data?.isDraft,
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
    mutationFn: (data) => {
      return axios.post(
        `${endpoints.develop.runPrompt.commitSavePrompt(id)}`,
        data,
      );
    },
    onSuccess: () => {
      enqueueSnackbar(
        <>
          Commit for {promptName}&nbsp;
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
    <Dialog fullWidth maxWidth={"sm"} open={open} onClose={handleOnClose}>
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
            <Iconify icon="mdi:close" />
          </IconButton>
        </Stack>
      </DialogTitle>
      <Box component={"form"} onSubmit={handleSubmit(onSubmit)}>
        <DialogContent>
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
              Commit message
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
            sx={{ px: "16px", color: "text.disabled" }}
            disabled={!isDirty || isPending}
            loading={isPending}
            variant="outlined"
            type="button"
            onClick={commitOnly}
          >
            Commit
          </LoadingButton>
          <LoadingButton
            sx={{ px: "16px" }}
            loading={isPending}
            variant="contained"
            color="primary"
            type="submit"
            disabled={!isDirty}
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
};
