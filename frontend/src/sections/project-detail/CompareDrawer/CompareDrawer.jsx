import { Box, Drawer } from "@mui/material";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { ShowComponent } from "src/components/show";

import ProjectBreadCrumbs from "../ProjectBreadCrumbs";

import CompareExperimentRightSection from "./CompareExperimentRightSection";
import CompareNavigateButtons from "./CompareNavigateButtons";
import CompareDrawerSkeleton from "./Skeletons/CompareDrawerSkeleton";
import CompareSection from "./CompareSection";

const getEvalList = (compareData) => {
  if (!compareData?.traceComparison) {
    return [];
  }
  const eachRun = Object.values(compareData?.traceComparison || {})?.[0];
  if (!eachRun?.evalsMetrics) {
    return [];
  }

  return Object.entries(eachRun?.evalsMetrics || {}).map(
    ([id, evalMetric]) => ({ label: evalMetric?.name, value: id }),
  );
};

const CompareExperimentDrawerChild = ({
  onClose,
  selectedRows,
  projectDetail,
}) => {
  const [currentRow, setCurrentRow] = useState(0);

  const [selectedRuns, setSelectedRuns] = useState(() =>
    selectedRows.map((row) => row.id),
  );

  const [selectedEvals, setSelectedEvals] = useState([]);

  const [isTraceOpen] = useState(false);
  const [isAnnotateOpen] = useState(false);

  const {
    data: compareData,
    isSuccess: isSuccessCompareData,
    isLoading: isLoadingCompareData,
  } = useQuery({
    queryKey: ["project-compare-runs", currentRow, selectedRuns],
    queryFn: () =>
      axios.post(endpoints.project.compareTraces, {
        project_version_ids: selectedRuns,
        index: currentRow,
      }),
    enabled: selectedRuns.length > 0,
    select: (data) => data.data?.result,
  });

  useEffect(() => {
    if (!compareData) {
      return;
    }
    setSelectedEvals(getEvalList(compareData).map((item) => item.value));
  }, [compareData]);

  const evalList = useMemo(() => getEvalList(compareData), [compareData]);

  return (
    <Box
      sx={{
        paddingTop: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        height: "100%",
        backgroundColor: "background.paper",
        width: "100%",
        paddingX: "18px",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <ProjectBreadCrumbs
          links={[
            {
              name: projectDetail?.name || "Project",
              href: `/dashboard/prototype`,
            },
            { name: "Compare Runs", href: "/dashboard/prototype" },
          ]}
          onBack={onClose}
        />
        <CompareExperimentRightSection
          onClose={onClose}
          selectedRuns={selectedRuns}
          setSelectedRuns={setSelectedRuns}
          isLoading={isLoadingCompareData}
          evalList={evalList}
          selectedEvals={selectedEvals}
          setSelectedEvals={setSelectedEvals}
        />
      </Box>
      <ShowComponent condition={isLoadingCompareData}>
        <CompareDrawerSkeleton />
      </ShowComponent>
      <ShowComponent condition={isSuccessCompareData}>
        <Box>
          <CompareNavigateButtons
            totalCount={compareData?.total_traces}
            currentCount={currentRow + 1}
            onNext={() => {
              setCurrentRow(currentRow + 1);
            }}
            onPrevious={() => {
              setCurrentRow(currentRow - 1);
            }}
          />
        </Box>
        <Box
          sx={{
            display: "flex",
            flex: 1,
            overflowX: "auto",
          }}
        >
          {Object.entries(compareData?.traceComparison || {}).map(
            ([id, traceData]) => (
              <CompareSection
                key={id}
                traceData={traceData}
                selectedEvals={selectedEvals}
                globalTraceOpen={isTraceOpen}
                globalAnnotateOpen={isAnnotateOpen}
                totalRuns={selectedRuns.length}
              />
            ),
          )}
        </Box>
      </ShowComponent>
    </Box>
  );
};

CompareExperimentDrawerChild.propTypes = {
  onClose: PropTypes.func,
  selectedRows: PropTypes.array,
  projectDetail: PropTypes.object,
};

const CompareExperimentDrawer = ({
  open,
  onClose,
  selectedRows,
  projectDetail,
}) => {
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      sx={coveredDrawerSx(covered)}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "8px",
          backgroundColor: "background.paper",
          width: "100vw",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <CompareExperimentDrawerChild
        onClose={onClose}
        selectedRows={selectedRows}
        projectDetail={projectDetail}
      />
    </Drawer>
  );
};

CompareExperimentDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  selectedRows: PropTypes.array,
  projectDetail: PropTypes.object,
};

export default CompareExperimentDrawer;
