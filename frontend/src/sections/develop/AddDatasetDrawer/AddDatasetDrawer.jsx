import { Box, Drawer, IconButton, Typography, Link } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import DatasetOptions from "./DatasetOptions";
import UploadFileModal from "./UploadFileModal";
import ManuallyCreateDataset from "./ManuallyCreateDataset";
import ImportFromHuggingFace from "./ImportFromHuggingFace";
import { useNavigate } from "react-router";
import { paths } from "src/routes/paths";
import AddSDKModal from "./AddSDKModal";
import SyntheticDataDrawer from "../AddRowDrawer/CreateSyntheticData";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import ExistingDatasetModal from "../AddRowDrawer/ExistingDatasetModal";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

const options = [
  {
    title: "Add data using SDK",
    subTitle: "Add SDK to import your data to our system",
    id: "addSdk",
    disabled: false,
    icons: "add_from_SDK",
  },
  {
    title: "Upload a file (JSON, CSV)",
    subTitle: "Upload in various file format",
    id: "uploadFile",
    disabled: false,
    icons: "upload_file",
  },
  {
    title: "Create Synthetic Data",
    subTitle: "Generate realistic Synthetic data to add in the dataset",
    id: "synthetic-data",
    icons: "create_synthetic",
  },
  {
    title: "Add datasets Manually",
    subTitle: "Add SDK to import your data to our system",
    disabled: false,
    icons: "manual_dataset",
    id: "manuallyCreateDataset",
  },
  {
    title: "Import from HuggingFace",
    subTitle: "Add SDK to import your data to our system",
    disabled: false,
    icons: "hugging_face",
    id: "importFromHuggingface",
  },
  {
    title: "Add from existing model dataset or experiment",
    subTitle:
      "Choose from the existing datasets in our system to create a new dataset",
    disabled: false,
    icons: "add_existing_model",
    id: "addFromExistingDataset",
  },
  // {
  //   title: "Add dataset using SDK",
  //   subTitle: "Add SDK to import your data to our system",
  //   disabled: true,
  //   id: "addDatasetUsingSDK",
  // },
  // {
  //   title: "Import from LLM logs",
  //   subTitle: "Import LLM logged inferences",
  //   disabled: true,
  //   id: "importFromLLMLogs",
  // },
  // {
  //   title: "Add dataset from Knowledge Base",
  //   subTitle: "Import a dataset from Knowledge Base",
  //   disabled: true,
  //   id: "addFromKnowledgeBase",
  // },
];

const AddDatasetDrawer = ({ open, onClose, refreshGrid }) => {
  const [uploadFileModalOpen, setUploadFileModalOpen] = useState(false);
  const [addSDK, setAddSDK] = useState(false);
  const [manuallyCreateDatasetModalOpen, setManuallyCreateDatasetModalOpen] =
    useState(false);
  const [importFromHuggingFaceModalOpen, setImportFromHuggingFaceModalOpen] =
    useState(false);
  const [cloneDevelopDatasetModalOpen, setCloneDevelopDatasetModalOpen] =
    useState(false);
  const [syntheticDataDrawerOpen, setSyntheticDataDrawerOpen] = useState(false);

  const navigate = useNavigate();
  const { isOSS } = useDeploymentMode();
  const filteredOptions = isOSS
    ? options.filter((o) => o.id !== "synthetic-data")
    : options;

  return (
    <>
      <AddSDKModal
        open={addSDK}
        onClose={() => setAddSDK(false)}
        refreshGrid={refreshGrid}
      />
      <UploadFileModal
        open={uploadFileModalOpen}
        onClose={() => {
          setUploadFileModalOpen(false);
          onClose();
        }}
        refreshGrid={refreshGrid}
      />
      <ManuallyCreateDataset
        open={manuallyCreateDatasetModalOpen}
        onClose={() => setManuallyCreateDatasetModalOpen(false)}
        refreshGrid={refreshGrid}
      />
      <ImportFromHuggingFace
        open={importFromHuggingFaceModalOpen}
        onClose={() => setImportFromHuggingFaceModalOpen(false)}
        refreshGrid={refreshGrid}
      />
      {/* <CloneDevelopDataset
        open={cloneDevelopDatasetModalOpen}
        onClose={() => setCloneDevelopDatasetModalOpen(false)}
        refreshGrid={refreshGrid}
      /> */}
      <SyntheticDataDrawer
        open={syntheticDataDrawerOpen}
        onClose={() => {
          setSyntheticDataDrawerOpen(false);
          onClose();
        }}
        datasetId={null}
        refreshGrid={refreshGrid}
      />
      <Drawer
        anchor="right"
        open={open}
        onClose={onClose}
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
          <Box sx={{ padding: 2 }}>
            <Box
              sx={{
                gap: 2,
                display: "flex",
                flexDirection: "column",
                width: "500px",
                overflowY: "auto",
                height: "calc(100vh - 32px)",
              }}
            >
              <Box sx={{ display: "flex" }}>
                <Box width="91%">
                  <Box
                    display="flex"
                    alignItems="center"
                    justifyContent="space-between"
                  >
                    <Typography
                      fontWeight={600}
                      color="text.primary"
                      variant="m3"
                    >
                      Add dataset
                    </Typography>
                    <Link
                      href="https://docs.futureagi.com/docs/dataset/overview"
                      underline="always"
                      color="blue.500"
                      target="_blank"
                      rel="noopener noreferrer"
                      fontWeight="fontWeightMedium"
                    >
                      Learn more
                    </Link>
                  </Box>
                  <Typography typography="s1">
                    Provide a dataset to experiment, evaluate, and optimize.
                  </Typography>
                </Box>

                <IconButton
                  onClick={onClose}
                  sx={{ position: "absolute", top: "10px", right: "12px" }}
                >
                  <Iconify icon="mingcute:close-line" color="text.primary" />
                </IconButton>
              </Box>
              <Box
                sx={{ display: "flex", flexDirection: "column", gap: "16px" }}
              >
                {filteredOptions.map((option) => (
                  <DatasetOptions
                    key={option.title}
                    {...option}
                    onClick={() => {
                      if (option.id === "addSdk") {
                        trackEvent(Events.datasetTypeChoosed, {
                          [PropertyName.method]: "add from Sdk",
                        });
                        trackEvent(Events.datasetFromSDKClicked);
                        setAddSDK(true);
                      }
                      if (option.id === "uploadFile") {
                        trackEvent(Events.datasetTypeChoosed, {
                          [PropertyName.method]: "add from uploadfile",
                        });
                        trackEvent(Events.datasetFromJSONCSVClicked);
                        setUploadFileModalOpen(true);
                      }
                      if (option.id === "manuallyCreateDataset") {
                        trackEvent(Events.datasetTypeChoosed, {
                          [PropertyName.method]:
                            "add from manuallyCreateDataset",
                        });
                        trackEvent(Events.datasetManualAdditionClicked);
                        setManuallyCreateDatasetModalOpen(true);
                      }
                      if (option.id === "importFromHuggingface") {
                        trackEvent(Events.datasetTypeChoosed, {
                          [PropertyName.method]:
                            "add from importFromHuggingFace",
                        });
                        trackEvent(Events.datasetFromHuggingFaceClicked);
                        navigate(paths.dashboard.huggingface);
                      }
                      if (option.id === "addFromExistingDataset") {
                        trackEvent(Events.datasetTypeChoosed, {
                          [PropertyName.method]: "add from existing dataset",
                        });
                        trackEvent(Events.datasetFromExistingDatasetClicked);
                        setCloneDevelopDatasetModalOpen(true);
                      }
                      if (option.id === "synthetic-data") {
                        trackEvent(Events.datasetTypeChoosed, {
                          [PropertyName.method]: "add from synthetic-data",
                        });
                        trackEvent(Events.syntheticDatasetCreationClicked);
                        // setSyntheticDataDrawerOpen(true);
                        navigate("/dashboard/develop/create-synthetic-dataset");
                      }
                    }}
                  />
                ))}
              </Box>
            </Box>
          </Box>
          <ExistingDatasetModal
            open={cloneDevelopDatasetModalOpen}
            onClose={() => {
              setCloneDevelopDatasetModalOpen(false);
            }}
            refreshGrid={refreshGrid}
            datasetId={null}
            closeDrawer={onClose}
          />
        </Box>
      </Drawer>
    </>
  );
};

AddDatasetDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
};

export default AddDatasetDrawer;
