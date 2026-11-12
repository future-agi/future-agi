import PropTypes from "prop-types";
import { Box, Drawer, IconButton, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import React, { useState } from "react";
import { notify } from "src/sections/common/ux/toast";
import { openAddData } from "src/sections/common/AddData/addDataStore";
import { useChangeSet } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import useRowViewUrl from "src/sections/develop-detail/DataTab/DatapointDrawerV2/useRowViewUrl";
import UploadFileModal from "./UploadFileModal";
import SetEmptyRow from "./SetEmptyRow";
import { useNavigate } from "react-router";
import AddSDKModal from "./AddSDKModal";
import DatasetOptions from "../AddDatasetDrawer/DatasetOptions";
import { useParams } from "react-router";
import ExistingDatasetModal from "./ExistingDatasetModal";
import SyntheticDataDrawer from "./CreateSyntheticData";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import {
  ADD_ROWS_OPTIONS,
  ROW_IMPORT_OPTIONS,
  rowImportOption,
} from "../importOptions";
import { PROPOSED_ADD_ROWS_COPY } from "../importAvailabilityCopy";
import { useGenerateRows } from "src/sections/common/AddData/GenerateRowsDialog";
import PasteRowsDialog from "./PasteRowsDialog";
import {
  addRowsTiles,
  isLaunchBuild,
  isSurfaceShown,
} from "src/launch/launchSwitch";
import { gridWritesClassic } from "../realBackendCaps";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

// Restore row 7: the SDK door only shows code for the classic
// develops/add_rows_sdk/ endpoint, so it needs no DSX surface. The launch
// tile filter (ADD_ROWS_TILE_SURFACE) decides every other tile; order kept.
const sheetTiles = (options) => {
  const shown = new Set(addRowsTiles(options).map((o) => o.id));
  return options.filter((o) => o.id === "addSdk" || shown.has(o.id));
};

const AddRowDrawer = ({ open, onClose }) => {
  const [uploadFileModalOpen, setUploadFileModalOpen] = useState(false);
  const [emptyRow, setEmptyRow] = useState(false);
  const [sdkModal, setSdkModal] = useState(false);
  const [existingDataset, setExistingDataset] = useState(false);
  const [syntheticDataDrawerOpen, setSyntheticDataDrawerOpen] = useState(false);
  const [pasteOpen, setPasteOpen] = useState(false);
  const { dataset } = useParams();
  const navigate = useNavigate();
  const { refreshGrid } = useDevelopDetailContext();
  // In a versioned build a blank row is a pending change: it shows as `New`
  // in the grid and opens in the Row view; classic builds use SetEmptyRow.
  const { addOp, revert } = useChangeSet(dataset);
  const { openRow } = useRowViewUrl();
  const addBlankRow = () => {
    const tempId = `new-${Date.now().toString(36)}`;
    addOp({ op: "add_row", temp_id: tempId, cells: {}, split_ids: [] });
    onClose();
    openRow(tempId);
    // v2.1 g8 (rows:blank-row): the toast offers Undo, which drops the
    // pending row (the same Undo as the toolbar's Add row).
    notify("Added a blank row · unsaved until you save a version", {
      key: "row-add-blank",
      undo: { onUndo: () => revert(tempId) },
    });
  };

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
        closeDrawer={closeDrawerAfterUpload}
      />
      <PasteRowsDialog
        open={pasteOpen}
        datasetId={dataset}
        onClose={(added) => {
          setPasteOpen(false);
          if (added) {
            onClose();
            refreshGrid?.();
          }
        }}
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
          <Box sx={{ width: 500 }}>
            <IconButton
              onClick={handleClose}
              aria-label="Close"
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
                  Add rows
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  {/* NOHIDE: a visible tile may be disabled (HF / GE-2).
                      Do not promise writes merely because the sheet opens. */}
                  {isLaunchBuild()
                    ? PROPOSED_ADD_ROWS_COPY.sheetSubtitle
                    : "New rows land as unsaved changes; Save version keeps them."}
                </Typography>
              </Box>
              <Box
                sx={{ display: "flex", flexDirection: "column", gap: "16px" }}
              >
                {/* IT-R1 G5 P3: on a dataset page, production first and
                    the blank row last; Paste added. */}
                {/* L6 launch switch: only tiles a lane made real. */}
                {sheetTiles(
                  dataset ? ADD_ROWS_OPTIONS : ROW_IMPORT_OPTIONS,
                ).map((option) => (
                  <DatasetOptions
                    key={option.id}
                    {...rowImportOption(option)}
                    onClick={() => {
                      if (option.id === "uploadFile")
                        setUploadFileModalOpen(true);
                      if (option.id === "paste") setPasteOpen(true);
                      if (option.id === "empty-row") {
                        if (
                          dataset &&
                          !gridWritesClassic() &&
                          isSurfaceShown("dataset.versions")
                        ) {
                          addBlankRow();
                        } else {
                          // v1.56.1: persist num_rows through add_empty_rows,
                          // then refresh the grid rather than staging add_row.
                          setEmptyRow(true);
                        }
                      }
                      if (option.id === "fromProduction") {
                        // Close first: this drawer sits above the sheet.
                        onClose();
                        openAddData({
                          source: { kind: "sessions" },
                          target: dataset ? { dataset_id: dataset } : null,
                        });
                      }
                      if (option.id === "importFromHuggingface") {
                        navigate("/dashboard/huggingface", {
                          state: {
                            datasetId: dataset,
                            additionalData: "hugging-face-drawer",
                          },
                        });
                      }
                      if (option.id === "generateRows" && dataset) {
                        onClose();
                        useGenerateRows
                          .getState()
                          .open({ datasetId: dataset, rowIds: [] });
                      }
                      if (option.id === "addSdk") setSdkModal(true);
                      if (option.id === "addFromExistingDataset")
                        setExistingDataset(true);
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
