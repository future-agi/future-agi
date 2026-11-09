import { Box, Drawer, IconButton, Typography, Link } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import Iconify from "src/components/iconify";
import { dsSize } from "src/theme/ds-tokens";
import DatasetOptions from "./DatasetOptions";
import UploadFileModal from "./UploadFileModal";
import ManuallyCreateDataset from "./ManuallyCreateDataset";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import ExistingDatasetModal from "../AddRowDrawer/ExistingDatasetModal";
import NewTaskSuiteDialog from "src/sections/develop-detail/TaskSuite/NewTaskSuiteDialog";
import { openAddData } from "src/sections/common/AddData/addDataStore";
import {
  DATASETS_DOCS,
  IMPORT_OPTIONS,
  createDoorShown,
  offeredImportOptions,
} from "../importOptions";

/**
 * New dataset chooser (DL#3, DL#24). A chosen path replaces the drawer
 * (the drawer hides while its dialog is open); Cancel in the dialog brings
 * the chooser back. `startWith = {path, name}` skips the chooser (⌘K
 * `Upload a file`, empty search `Create dataset named "…"`).
 * Only the doors in IMPORT_OPTIONS open (options-truth L5 G5, W0-1): each
 * previews what it creates and ends on the new dataset; a `startWith.path`
 * naming any other door leaves the chooser open.
 */
const OFFERED = new Set(IMPORT_OPTIONS.map((o) => o.id));
const AddDatasetDrawer = ({ open, onClose, refreshGrid, startWith }) => {
  const [path, setPath] = useState(null);
  const navigate = useNavigate();
  useEffect(() => {
    if (open && startWith?.path) choose(startWith.path);
    if (!open) setPath(null);
  }, [open, startWith?.path]); // eslint-disable-line react-hooks/exhaustive-deps
  // Cancel in a path dialog: back to the chooser (or out, when the path
  // was opened directly).
  const back = () => {
    setPath(null);
    if (startWith?.path) onClose();
  };
  const done = () => {
    setPath(null);
    onClose();
  };
  const uploadFileModalOpen = open && path === "uploadFile";
  const manuallyCreateDatasetModalOpen =
    open && path === "manuallyCreateDataset";
  const cloneDevelopDatasetModalOpen =
    open && path === "addFromExistingDataset";
  const taskSuiteOpen = open && path === "taskSuite";
  const startWithDisabled = offeredImportOptions().some(
    (option) => option.id === startWith?.path && option.disabled,
  );

  function choose(id) {
    if (!OFFERED.has(id) || !createDoorShown(id)) return;
    if (offeredImportOptions().find((option) => option.id === id)?.disabled)
      return;
    const method = {
      uploadFile: "add from uploadfile",
      manuallyCreateDataset: "add from manuallyCreateDataset",
      addFromExistingDataset: "add from existing dataset",
    }[id];
    if (method)
      trackEvent(Events.datasetTypeChoosed, { [PropertyName.method]: method });
    if (id === "uploadFile") trackEvent(Events.datasetFromJSONCSVClicked);
    if (id === "manuallyCreateDataset")
      trackEvent(Events.datasetManualAdditionClicked);
    if (id === "addFromExistingDataset")
      trackEvent(Events.datasetFromExistingDatasetClicked);
    if (id === "createSyntheticData") {
      // W3-8a: the synthetic page creates the dataset at v1.
      onClose();
      navigate("/dashboard/develop/create-synthetic-dataset");
      return;
    }
    if (id === "importFromHuggingface") {
      // W3-7: the Hugging Face page creates the dataset at v1.
      onClose();
      navigate("/dashboard/huggingface");
      return;
    }
    if (id === "fromProduction") {
      // Close first: this drawer sits above the sheet.
      onClose();
      openAddData({
        source: { kind: "sessions" },
        target: { new: { name: startWith?.name ?? "", kind: "conversations" } },
      });
      return;
    }
    setPath(id);
  }

  return (
    <>
      <UploadFileModal
        open={uploadFileModalOpen}
        onClose={back}
        onDone={done}
        refreshGrid={refreshGrid}
      />
      <ManuallyCreateDataset
        open={manuallyCreateDatasetModalOpen}
        initialName={startWith?.name}
        onClose={back}
        refreshGrid={refreshGrid}
      />
      {/* <CloneDevelopDataset
        open={cloneDevelopDatasetModalOpen}
        onClose={() => setCloneDevelopDatasetModalOpen(false)}
        refreshGrid={refreshGrid}
      /> */}
      <NewTaskSuiteDialog open={taskSuiteOpen} onClose={back} />
      <Drawer
        anchor="right"
        // The Copy-from pane opens inside the drawer, beside the chooser.
        open={
          open &&
          (!path || path === "addFromExistingDataset") &&
          (!startWith?.path || startWithDisabled)
        }
        onClose={onClose}
        PaperProps={{
          sx: {
            height: "100dvh",
            maxWidth: "100vw",
            overflowY: "auto",
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
        <Box
          sx={{
            display: "flex",
            // The two token-sized panes fit together only at lg and above.
            flexDirection: { xs: "column", lg: "row-reverse" },
            width: { xs: dsSize.drawer.md, lg: "auto" },
            maxWidth: "100%",
            flexShrink: 0,
          }}
        >
          <Box
            sx={{
              padding: 2,
              width: { xs: "100%", lg: dsSize.drawer.sm },
              minWidth: 0,
              boxSizing: "border-box",
              flexShrink: 0,
            }}
          >
            <Box
              sx={{
                gap: 2,
                display: "flex",
                flexDirection: "column",
                width: "100%",
                overflowY: { xs: "visible", lg: "auto" },
                height: { xs: "auto", lg: "calc(100dvh - 32px)" },
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
                      component="h2"
                    >
                      New dataset
                    </Typography>
                    <Link
                      href={DATASETS_DOCS}
                      underline="hover"
                      color="primary.main"
                      target="_blank"
                      rel="noopener noreferrer"
                      typography="s2"
                      fontWeight="fontWeightMedium"
                      sx={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 0.25,
                        mr: 4,
                      }}
                    >
                      Read the datasets guide
                      <Iconify icon="mdi:arrow-top-right" width={14} />
                    </Link>
                  </Box>
                  <Typography typography="s2" color="text.secondary">
                    A dataset holds the tasks you test on. Pick how to fill it;
                    you can add more rows later.
                  </Typography>
                </Box>

                <IconButton
                  onClick={onClose}
                  aria-label="Close"
                  sx={{ position: "absolute", top: "10px", right: "12px" }}
                >
                  <Iconify icon="mingcute:close-line" color="text.primary" />
                </IconButton>
              </Box>
              <Box
                sx={{ display: "flex", flexDirection: "column", gap: "16px" }}
              >
                {offeredImportOptions().map((option) => (
                  <DatasetOptions
                    key={option.id}
                    {...option}
                    onClick={() => choose(option.id)}
                  />
                ))}
              </Box>
            </Box>
          </Box>
          <ExistingDatasetModal
            stackBelowLg
            open={cloneDevelopDatasetModalOpen}
            onClose={back}
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
  startWith: PropTypes.shape({
    path: PropTypes.string,
    name: PropTypes.string,
  }),
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
};

export default AddDatasetDrawer;
