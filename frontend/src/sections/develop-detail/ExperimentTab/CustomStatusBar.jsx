import { Box, Button } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import Iconify from "src/components/iconify";
import ConfirmDeleteDatasets from "./ConfirmDeleteDatasets";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";

const CustomStatusBar = ({ api }) => {
  const [count, setCount] = useState(0);
  const [selectedRows, setSelectedRows] = useState([]);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

  const { mutate: deleteExperiment, isPending: isLoading } = useMutation({
    mutationFn: (d) => {
      return axios.delete(endpoints.develop.experiment.deleteExperiment(), {
        data: {
          experiment_ids: d.experiment_ids,
        },
      });
    },
    onSuccess: () => {
      enqueueSnackbar("Experiment deleted successfully", {
        variant: "success",
      });
      api.refreshServerSide({ prune: true });
      api.deselectAll();
      setSelectedRows([]);
      setConfirmDeleteOpen(false);
    },
  });

  const updateStatusBar = () => {
    setCount(api.paginationGetRowCount());
  };

  const updateSelectedRows = () => {
    setSelectedRows(api.getSelectedRows());
  };

  useEffect(() => {
    api.addEventListener("modelUpdated", updateStatusBar);
    api.addEventListener("selectionChanged", updateSelectedRows);

    // Remove event listener when destroyed
    return () => {
      if (!api.isDestroyed()) {
        api.removeEventListener("modelUpdated", updateStatusBar);
        api.removeEventListener("selectionChanged", updateSelectedRows);
      }
    };
  }, []);

  const getComponent = () => {
    if (selectedRows?.length > 0) {
      return (
        <>
          <ConfirmDeleteDatasets
            open={confirmDeleteOpen}
            onClose={() => setConfirmDeleteOpen(false)}
            onConfirm={() => {
              // @ts-ignore
              deleteExperiment({
                experiment_ids: selectedRows?.map((r) => r.id),
              });
            }}
            isLoading={isLoading}
          />
          <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
            <Box>
              <b>{selectedRows?.length}</b> Selected
            </Box>
            <Button
              size="small"
              startIcon={
                <Iconify
                  icon="solar:trash-bin-trash-bold"
                  sx={{ color: "error.main" }}
                />
              }
              onClick={() => setConfirmDeleteOpen(true)}
            >
              Delete
            </Button>
          </Box>
        </>
      );
    }

    return <Box>Total Rows : {count}</Box>;
  };

  return <Box sx={{ padding: 1 }}>{getComponent()}</Box>;
};

CustomStatusBar.displayName = "CustomStatusBar";

CustomStatusBar.propTypes = {
  api: PropTypes.object,
};

export default CustomStatusBar;
