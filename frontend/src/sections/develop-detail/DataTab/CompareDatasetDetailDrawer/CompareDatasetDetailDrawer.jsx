import { Drawer } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import CompareDatasetDetailContent from "./CompareDatasetDetailContent";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

export default function CompareDatasetDetailDrawer({
  open,
  row,
  onClose,
  compareId,
  columnConfig,
}) {
  const [showDiff, setShowDiff] = useState(false);
  const [currentRowId, setCurrentRowId] = useState(null);

  const queryClient = useQueryClient();

  const { data, isPending } = useQuery({
    queryKey: ["compare-dataset-row-data", compareId, currentRowId],
    enabled: Boolean(compareId && currentRowId),
    queryFn: () =>
      axios.get(
        endpoints.dataset.getCompareDatasetRow(compareId, currentRowId),
      ),
    staleTime: 60000,
    select: (d) => d.data?.result,
  });

  const nextRowId = data?.nextRowId;
  const prevRowId = data?.prevRowId;

  useEffect(() => {
    if (row?.rowId) {
      setCurrentRowId(row?.rowId);
    }
  }, [row?.rowId]);

  const handleFetchNextRow = () => {
    if (nextRowId) {
      setCurrentRowId(nextRowId);
    }
  };

  const handleFetchPrevRow = () => {
    if (prevRowId) {
      setCurrentRowId(prevRowId);
    }
  };

  const handleToggleDiff = () => {
    setShowDiff((prev) => !prev);
  };

  useEffect(() => {
    if (!compareId) return;
    const rowIdsToPrefetch = [nextRowId, prevRowId].filter(Boolean);

    rowIdsToPrefetch.forEach((rowId) => {
      queryClient.prefetchQuery({
        queryKey: ["compare-dataset-row-data", compareId, rowId],
        queryFn: () =>
          axios.get(endpoints.dataset.getCompareDatasetRow(compareId, rowId)),
        staleTime: 60000,
      });
    });
  }, [nextRowId, prevRowId, compareId, queryClient]);

  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      sx={coveredDrawerSx(covered)}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          left: 0,
          right: 0,
          top: 0,
          bottom: 0,
          zIndex: 9999,
          borderRadius: "8px",
          backgroundColor: "background.paper",
          overflowX: "hidden",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <CompareDatasetDetailContent
        columnConfig={columnConfig}
        row={data?.table?.[0]}
        onClose={onClose}
        nextRowId={nextRowId}
        prevRowId={prevRowId}
        handleFetchNextRow={handleFetchNextRow}
        handleFetchPrevRow={handleFetchPrevRow}
        isPending={isPending}
        showDiff={showDiff}
        setShowDiff={setShowDiff}
        handleToggleDiff={handleToggleDiff}
      />
    </Drawer>
  );
}

CompareDatasetDetailDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  row: PropTypes.object,
  columnConfig: PropTypes.array,
  compareId: PropTypes.string,
};
