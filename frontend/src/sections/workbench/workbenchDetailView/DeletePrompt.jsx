import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogTitle,
  Divider,
  IconButton,
  Typography,
} from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import axios, { endpoints } from "src/utils/axios";
import logger from "src/utils/logger";
import { Events, trackEvent } from "src/utils/Mixpanel";

const DeletePrompt = ({ open, onClose, refreshGrid, selected }) => {
  const { mutate: handleDelete, isPending } = useMutation({
    mutationFn: () => {
      const data = { ids: selected.map((item) => item.id) };
      return axios.post(endpoints.develop.runPrompt.promptMultiDelete, data);
    },
    onSuccess: () => {
      const message = `${selected.length > 1 ? `${selected.length} prompts` : selected[0].name} has been deleted`;
      enqueueSnackbar(message, { variant: "success" });
      refreshGrid({ purge: true });
      trackEvent(Events.deletedSavedPrompt);
      onClose();
    },
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 400 } }}
      fullWidth
    >
      <Box sx={{ padding: 2 }}>
        <DialogTitle sx={{ padding: 0, margin: 0 }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              variant="m2"
            >
              Delete {selected?.length} Prompt
              {selected?.length > 1 && "s"}
            </Typography>
            <IconButton onClick={onClose}>
              <Iconify icon="solar:close-linear" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText
            text={`Are you sure you want to delete ${selected?.length > 1 ? "these" : "this"} prompt${selected?.length > 1 ? "s" : ""}?`}
          />
        </DialogTitle>
        <Box>
          <Divider
            orientation="horizontal"
            sx={{ marginY: "12px", borderColor: "divider" }}
          />
          <Box
            sx={{
              maxHeight: "300px",
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
              gap: "12px",
            }}
          >
            {selected?.map((item, index) => {
              logger.debug(item);
              return (
                <Box
                  key={index}
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "8px",
                    padding: "12px",
                  }}
                >
                  <Typography
                    variant="s1"
                    fontWeight={"fontWeightMedium"}
                    color="text.primary"
                  >
                    {item.name}
                  </Typography>
                </Box>
              );
            })}
          </Box>
          <DialogActions sx={{ padding: 0, marginTop: "32px" }}>
            <Button
              onClick={onClose}
              variant="outlined"
              sx={{
                color: "text.disabled",
                minWidth: "90px",
              }}
            >
              <Typography
                variant="s2"
                fontWeight={"fontWeightSemiBold"}
                fontSize={"13px"}
              >
                Cancel
              </Typography>
            </Button>
            <LoadingButton
              type="button"
              variant="contained"
              loading={isPending}
              onClick={handleDelete}
              color="error"
              sx={{
                backgroundColor: "status.danger.solid",
                color: "error.contrastText",
                minWidth: "90px",
              }}
            >
              <Typography
                variant="s2"
                fontWeight={"fontWeightSemiBold"}
                fontSize={"13px"}
              >
                Delete
              </Typography>
            </LoadingButton>
          </DialogActions>
        </Box>
      </Box>
    </Dialog>
  );
};

export default DeletePrompt;

DeletePrompt.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  selected: PropTypes.array,
};
