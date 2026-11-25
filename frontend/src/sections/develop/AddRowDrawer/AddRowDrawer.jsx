import PropTypes from "prop-types";
import { Box, Drawer, IconButton, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import React, { useState } from "react";
import UploadFileModal from "./UploadFileModal";
import SetEmptyRow from "./SetEmptyRow";
import { useNavigate } from "react-router";
import AddSDKModal from "./AddSDKModal";
import DatasetOptions from "./DatasetOptions";
import { useParams } from "react-router";
import ExistingDatasetModal from "./ExistingDatasetModal";
import SyntheticDataDrawer from "./CreateSyntheticData";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const options = [
  {
    title: "Add data using SDK",
    subTitle: "Add SDK to improve your data to our system.",
    id: "sdk-modal",
    icons: "add_from_SDK",
  },
  {
    title: "Add from existing model dataset or experiment",
    subTitle:
      "Choose from the existing datasets in our system to create a new dataset",
    id: "existing-dataset",
    icons: "add_existing_model",
  },
  // {
  //   title: "Create Synthetic Data",
  //   subTitle: "Generate realistic Synthetic data to add in the dataset",
  //   id: "synthetic-data",
  //   icons: "create_synthetic",
  // },
  {
    title: "Import from Hugging Face",
    subTitle: "Import a dataset from Hugging Face",
    id: "huggin-face",
    icons: "hugging_face",
  },
  {
    title: "Add empty row",
    subTitle: "Add empty rows and columns to your dataset",
    id: "empty-row",
    icons: "empty_rows",
  },
  {
    title: "Upload a file (JSONl/ JSON/ CSV)",
    subTitle: "Upload in various file format",
    id: "uploadFile",
    icons: "upload_file",
  },
];

const AddRowDrawer = ({ open, onClose }) => {
  const [uploadFileModalOpen, setUploadFileModalOpen] = useState(false);
  const [emptyRow, setEmptyRow] = useState(false);
  const [sdkModal, setSdkModal] = useState(false);
  const [existingDataset, setExistingDataset] = useState(false);
  const [syntheticDataDrawerOpen, setSyntheticDataDrawerOpen] = useState(false);
  const { dataset } = useParams();
  const navigate = useNavigate();
  const { refreshGrid } = useDevelopDetailContext();

  const closeDrawerAfterUpload = () => {
    onClose();
  };

  const handleClose = () => {
    onClose();
    setSyntheticDataDrawerOpen(false);
    setExistingDataset(false);
  };

  const covered = useDrawerCovered(open);
  return (
    <>
      <UploadFileModal
        open={uploadFileModalOpen}
        onClose={() => setUploadFileModalOpen(false)}
        refreshGrid={refreshGrid}
        datasetId={dataset}
        closeDrawer={closeDrawerAfterUpload}
      />
      <SetEmptyRow
        open={emptyRow}
        onClose={() => setEmptyRow(false)}
        refreshGrid={refreshGrid}
        datasetId={dataset}
        closeDrawer={closeDrawerAfterUpload}
        onSuccess={() => {
          navigate(`/dashboard/develop/${dataset}?tab=data`);
        }}
      />
      <AddSDKModal
        open={sdkModal}
        onClose={() => setSdkModal(false)}
        datasetId={dataset}
      />
      <SyntheticDataDrawer
        open={syntheticDataDrawerOpen}
        onClose={() => {
          setSyntheticDataDrawerOpen(false);
          onClose();
        }}
        datasetId={dataset}
        refreshGrid={refreshGrid}
      />
      <Drawer
        anchor="right"
        open={open}
        sx={coveredDrawerSx(covered)}
        onClose={handleClose}
        PaperProps={{
          sx: {
            height: "100vh",
            // width: "480px",
            position: "fixed",
            zIndex: 9999,
            borderRadius: "8px",
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
          <Box>
            <IconButton
              onClick={handleClose}
              sx={{ position: "absolute", top: "10px", right: "12px" }}
            >
              <Iconify icon="solar:close-linear" color="text.primary" />
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
              <Box
                sx={{ display: "flex", flexDirection: "column", gap: "16px" }}
              >
                {options.map((option) => (
                  <DatasetOptions
                    key={option.title}
                    {...option}
                    onClick={() => {
                      if (option.id === "uploadFile") {
                        setUploadFileModalOpen(true);
                      }
                      if (option.id === "empty-row") {
                        setEmptyRow(true);
                      }
                      if (option.id === "huggin-face") {
                        navigate("/dashboard/huggingface", {
                          state: {
                            datasetId: dataset,
                            additionalData: "hugging-face-drawer",
                          },
                        });
                      }
                      if (option.id === "sdk-modal") {
                        setSdkModal(true);
                      }
                      if (option.id === "synthetic-data") {
                        // setSyntheticDataDrawerOpen(true); // Open the Synthetic data drawer
                      }
                      if (option.id === "existing-dataset") {
                        setExistingDataset(true);
                      }
                    }}
                  />
                ))}
              </Box>
            </Box>
          </Box>
          <Box>
            <ExistingDatasetModal
              open={existingDataset}
              onClose={() => {
                setExistingDataset(false);
                onClose();
              }}
              refreshGrid={refreshGrid}
              datasetId={dataset}
              closeDrawer={closeDrawerAfterUpload}
            />
          </Box>
        </Box>
      </Drawer>
    </>
  );
};

AddRowDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};

export default AddRowDrawer;
