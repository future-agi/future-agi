import { Box, Drawer, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import { useRefreshScenarioGrid } from "./useRefreshScenarioGrid";
import DatasetOptions from "src/sections/develop/AddRowDrawer/DatasetOptions";
import ExistingDatasetModal from "src/sections/develop/AddRowDrawer/ExistingDatasetModal";
import SetEmptyRow from "src/sections/develop/AddRowDrawer/SetEmptyRow";
import AddRowUsingAi from "./AddRowUsingAi";

const AddRowScenario = ({
  open,
  onClose,
  datasetId,
  scenarioType,
  scenarioId,
}) => {
  const handleClose = () => {
    onClose();
  };

  const options = useMemo(() => {
    const options = [];

    if (scenarioType === "dataset") {
      options.push({
        title: "Add from existing model dataset or experiment",
        subTitle:
          "Choose from the existing datasets in our system to add rows to the scenario table",
        id: "existing-dataset",
        icons: "add_existing_model",
      });
    }
    options.push(
      ...[
        {
          title: "Generate using AI",
          subTitle: "Generate rows based on a prompt",
          id: "generate-using-ai",
          icons: "create_synthetic",
        },

        {
          title: "Add empty row",
          subTitle: "Add empty rows to the scenario table",
          id: "empty-row",
          icons: "empty_rows",
        },
      ],
    );

    return options;
  }, [scenarioType]);

  const refreshGrid = useRefreshScenarioGrid(scenarioId);

  const [emptyRow, setEmptyRow] = useState(false);
  const [existingDataset, setExistingDataset] = useState(false);
  const [addRowUsingAi, setAddRowUsingAi] = useState(false);
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "row-reverse" }}>
        <Box sx={{ flex: 1, minWidth: "450px" }}>
          <IconButton
            onClick={handleClose}
            sx={{ position: "absolute", top: "10px", right: "12px" }}
          >
            <Iconify icon="mingcute:close-line" color="text.primary" />
          </IconButton>
          <Box
            sx={{
              padding: 2,
              gap: "28px",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <Box>
              <Typography
                variant="m3"
                fontWeight={"fontWeightMedium"}
                color="text.primary"
              >
                Add Rows
              </Typography>
              <Iconify />
            </Box>
            <Box sx={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              {options.map((option) => (
                <DatasetOptions
                  key={option.title}
                  {...option}
                  onClick={() => {
                    if (option.id === "empty-row") {
                      setEmptyRow(true);
                    }
                    if (option.id === "existing-dataset") {
                      setExistingDataset(true);
                    }
                    if (option.id === "generate-using-ai") {
                      setAddRowUsingAi(true);
                    }
                  }}
                />
              ))}
            </Box>
          </Box>
        </Box>
        <SetEmptyRow
          open={emptyRow}
          onClose={() => setEmptyRow(false)}
          refreshGrid={refreshGrid}
          datasetId={datasetId}
          closeDrawer={handleClose}
        />
        <AddRowUsingAi
          open={addRowUsingAi}
          onClose={() => {
            setAddRowUsingAi(false);
            onClose();
          }}
          scenarioId={scenarioId}
        />
        <Box>
          <ExistingDatasetModal
            open={existingDataset}
            onClose={() => {
              setExistingDataset(false);
              onClose();
            }}
            refreshGrid={refreshGrid}
            datasetId={datasetId}
            closeDrawer={handleClose}
          />
        </Box>
      </Box>
    </Drawer>
  );
};

AddRowScenario.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
  scenarioType: PropTypes.string,
  scenarioId: PropTypes.string,
};

export default AddRowScenario;
