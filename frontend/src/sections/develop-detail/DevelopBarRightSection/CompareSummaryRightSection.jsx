import { Box, Button } from "@mui/material";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import ChooseWinnerInCompareData from "../DataTab/ChooseWinnerInCompareData";

const CompareSummaryRightSection = ({
  columns,
  refreshGrid,
  evalsData,
  selectedDatasets,
  baseColumn,
  commonColumn,
  datasetInfo,
  setIsChooseWinnerSelected,
  setDataAfterChooseWinner,
  isChooseWinnerButtonVisible,
}) => {
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  return (
    <>
      <Box display="flex" alignItems="center" gap={1.5}>
        {/* Button to Open Drawer */}
        {isChooseWinnerButtonVisible && (
          <Button
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="solar:crown-linear" />}
            onClick={() => setIsDrawerOpen(true)} // Open drawer on click
            size="small"
          >
            Choose Winner
          </Button>
        )}

        {/* Vertical Divider */}
        {/* <Divider sx={{ height: "24px", marginTop: "4px", marginLeft: "6px" }} orientation="vertical" flexItem /> */}

        {/* Download Button */}
        {/* <IconButton size="small">
                    <Iconify icon="solar:download-minimalistic-linear" />
                </IconButton> */}
      </Box>

      {/* Winner Selection Drawer */}
      <ChooseWinnerInCompareData
        evalsData={evalsData}
        open={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        columns={columns}
        refreshGrid={refreshGrid}
        selectedDatasets={selectedDatasets}
        baseColumn={baseColumn}
        datasetInfo={datasetInfo}
        commonColumn={commonColumn}
        setIsChooseWinnerSelected={setIsChooseWinnerSelected}
        setDataAfterChooseWinner={setDataAfterChooseWinner}
      />
    </>
  );
};

CompareSummaryRightSection.propTypes = {
  setIsChooseWinnerSelected: PropTypes.func,
  setDataAfterChooseWinner: PropTypes.func,
  commonColumn: PropTypes.array,
  datasetInfo: PropTypes.array,
  selectedDatasets: PropTypes.array,
  baseColumn: PropTypes.string,
  evalsData: PropTypes.array,
  experimentSearch: PropTypes.string,
  setExperimentSearch: PropTypes.func,
  columns: PropTypes.array,
  refreshGrid: PropTypes.func,
  isChooseWinnerButtonVisible: PropTypes.bool,
};

export default CompareSummaryRightSection;
