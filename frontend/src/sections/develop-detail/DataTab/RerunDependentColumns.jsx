import React from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  IconButton,
  Box,
  List,
  ListItem,
  ListItemText,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useRerunDependentColumnsStore } from "../states";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";

const RerunDependentColumns = () => {
  const { rerunDependentColumns, setRerunDependentColumns } = useRerunDependentColumnsStore();
  const { refreshGrid } = useDevelopDetailContext();

  const { mutate: rerunDependentsMutate, isPending: isLoading } = useMutation({
    mutationFn: async (dependents) => {
      const promises = dependents.map((dep) =>
        axios.post(endpoints.develop.addColumns.updateDynamicColumn(dep.id), {
          operation_type: dep.operation_type,
        })
      );
      return Promise.all(promises);
    },
    onSuccess: () => {
      setRerunDependentColumns(null);
      refreshGrid();
    },
  });

  const onConfirm = () => {
    if (rerunDependentColumns?.dependents) {
      rerunDependentsMutate(rerunDependentColumns.dependents);
    }
  };

  const onClose = () => {
    setRerunDependentColumns(null);
  };

  if (!rerunDependentColumns) return null;

  return (
    <Dialog
      open={Boolean(rerunDependentColumns)}
      onClose={onClose}
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-description"
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle
        sx={{
          gap: "10px",
          display: "flex",
          flexDirection: "column",
          padding: 2,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Typography variant="h6">
            Rerun dependent columns?
          </Typography>
          <IconButton onClick={onClose} disabled={isLoading}>
            <Iconify icon="mdi:close" />
          </IconButton>
        </Box>
      </DialogTitle>
      
      <DialogContent sx={{ padding: 2 }}>
        <Typography variant="body1" sx={{ mb: 2 }}>
          The following columns depend on the column you just reran. Would you like to rerun them as well to keep data in sync?
        </Typography>
        <List dense sx={{ bgcolor: "background.paper", borderRadius: 1, border: "1px solid", borderColor: "divider" }}>
          {rerunDependentColumns.dependents?.map((col) => (
            <ListItem key={col.id}>
              <ListItemText 
                primary={col.name} 
                secondary={`Type: ${col.operation_type}`}
              />
            </ListItem>
          ))}
        </List>
      </DialogContent>

      <DialogActions sx={{ padding: 2 }}>
        <Button onClick={onClose} variant="outlined" size="small" disabled={isLoading}>
          No
        </Button>
        <LoadingButton
          onClick={onConfirm}
          variant="contained"
          autoFocus
          size="small"
          loading={isLoading}
        >
          Yes
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

export default RerunDependentColumns;
