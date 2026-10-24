import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogTitle,
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

const DeleteKnowledgeBase = ({ open, onClose, refreshGrid, selected }) => {
  const { mutate: handleDeleteKnowledgeBase, isPending } = useMutation({
    mutationFn: () => {
      const data = { kb_ids: selected.map((item) => item.id) };
      return axios.delete(endpoints.knowledge.knowledgeBase, { data });
    },
    onSuccess: () => {
      enqueueSnackbar("Knowledge base deleted successfully", {
        variant: "success",
      });
      refreshGrid();
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
              Delete Knowledge Base
              {selected?.length > 1 && "s"}
            </Typography>
            <IconButton onClick={onClose}>
              <Iconify icon="solar:close-linear" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText
            text={`Are you sure you want to delete ${selected?.length > 1 ? `the selected ${selected?.length} knowledge base?` : "this knowledge base?"}`}
          />
        </DialogTitle>
        <Box>
          <DialogActions sx={{ padding: 0, marginTop: "32px" }}>
            <Button
              onClick={() => {
                onClose();
              }}
              variant="outlined"
              sx={{
                color: "text.disabled",
                minWidth: "19px",
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
              variant="contained"
              loading={isPending}
              onClick={handleDeleteKnowledgeBase}
              color="error"
              sx={{
                backgroundColor: "status.danger.solid",
                color: "common.white",
                minWidth: "19px",
              }}
              // sx={{ color: "#fff", backgroundColor: "rgba(219, 47, 45, 1)" }}
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

export default DeleteKnowledgeBase;

DeleteKnowledgeBase.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  selected: PropTypes.array,
};
