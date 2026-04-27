/* eslint-disable react-hooks/exhaustive-deps */
import {
  Box,
  CircularProgress,
  LinearProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import PropType from "prop-types";
import PerformanceTableCell from "./PerformanceTableCell";
import {
  chatContextColumns,
  chatEvalColumns,
  postPromptTemplateColumns,
  prePromptTemplateColumns,
} from "src/utils/constant";
import ChatEvalRow from "./ChatEvalRow";
import ContextEvalRow from "./ContextEvalRow";
import PromptTemplateEvalRow from "./PromptTemplateEvalRow";
import Lightbox from "yet-another-react-lightbox";
import "yet-another-react-lightbox/styles.css";
import PerformanceDetailDrawer from "./PerformanceDetail/PerformanceDetailDrawer";

const PerformanceTable = ({
  tableData,
  fetchNextPage,
  isLoading,
  isFetchingNextPage,
  setSelectedTags,
  selectedMetric,
}) => {
  const tableContainerRef = useRef(null);
  const [performanceDetail, setPerformanceDetail] = useState(null);

  useEffect(() => {
    const handleScroll = () => {
      if (!tableContainerRef.current || isLoading || isFetchingNextPage) return;

      const { scrollTop, scrollHeight, clientHeight } =
        tableContainerRef.current;
      if (scrollTop + clientHeight >= scrollHeight - 5) {
        fetchNextPage();
      }
    };

    const container = tableContainerRef.current;
    if (container) {
      container.addEventListener("scroll", handleScroll);
    }

    return () => {
      if (container) {
        container.removeEventListener("scroll", handleScroll);
      }
    };
  }, [tableData]);

  const columns = useMemo(() => {
    if (selectedMetric?.evaluationType === "EVALUATE_CHAT") {
      return chatEvalColumns;
    } else if (
      selectedMetric?.evaluationType === "EVALUATE_CONTEXT" ||
      selectedMetric?.evaluationType === "EVALUATE_CONTEXT_RANKING"
    ) {
      return chatContextColumns;
    } else if (selectedMetric?.evaluationType === "EVALUATE_PROMPT_TEMPLATE") {
      const cols = [...prePromptTemplateColumns];

      // eslint-disable-next-line react/prop-types
      // if (tableData?.[0]?.variables) {
      //   // eslint-disable-next-line react/prop-types
      //   Object.keys(tableData?.[0]?.variables).forEach((key) => {
      //     cols.push({ id: cols.length + 1, headerName: key });
      //   });
      // }
      postPromptTemplateColumns.forEach((k) => {
        cols.push({ id: cols.length + 1, headerName: k.headerName });
      });
      return cols;
    }
    return [];
  }, [selectedMetric?.evaluationType, tableData]);

  const [selectedImages, setSelectedImages] = useState(null);

  return (
    <Box sx={{ width: "100%" }}>
      <PerformanceDetailDrawer
        open={Boolean(performanceDetail)}
        onClose={() => setPerformanceDetail(null)}
        performanceDetails={performanceDetail}
        evalType={selectedMetric?.evaluationType}
        setSelectedImages={setSelectedImages}
      />
      <TableContainer
        sx={{ height: "calc(100vh - 170px)" }}
        ref={tableContainerRef}
      >
        <Table stickyHeader sx={{ minWidth: 960 }}>
          <TableHead>
            <TableRow>
              {columns.map((headCell) => (
                <PerformanceTableCell key={headCell.id} sx={{ flex: 1 }}>
                  {headCell.headerName}
                </PerformanceTableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableRow>
            <TableCell colSpan={columns.length} sx={{ padding: 0, margin: 0 }}>
              {isLoading && <LinearProgress />}
            </TableCell>
          </TableRow>
          <TableBody>
            {tableData?.map((row) => {
              const isProcessing = !row?.score;
              if (selectedMetric?.evaluationType === "EVALUATE_CHAT")
                return (
                  <ChatEvalRow
                    key={row.id}
                    row={row}
                    isProcessing={isProcessing}
                    setPerformanceDetail={setPerformanceDetail}
                    setSelectedTags={setSelectedTags}
                    setSelectedImages={setSelectedImages}
                  />
                );
              else if (
                selectedMetric?.evaluationType === "EVALUATE_CONTEXT" ||
                selectedMetric?.evaluationType === "EVALUATE_CONTEXT_RANKING"
              ) {
                return (
                  <ContextEvalRow
                    key={row.id}
                    row={row}
                    isProcessing={isProcessing}
                    setPerformanceDetail={setPerformanceDetail}
                    setSelectedTags={setSelectedTags}
                    setSelectedImages={setSelectedImages}
                  />
                );
              } else if (
                selectedMetric?.evaluationType === "EVALUATE_PROMPT_TEMPLATE"
              ) {
                return (
                  <PromptTemplateEvalRow
                    key={row.id}
                    row={row}
                    isProcessing={isProcessing}
                    setPerformanceDetail={setPerformanceDetail}
                    setSelectedTags={setSelectedTags}
                    setSelectedImages={setSelectedImages}
                  />
                );
              }
            })}
          </TableBody>
        </Table>
      </TableContainer>
      <Lightbox
        open={Boolean(selectedImages)}
        close={() => {
          setSelectedImages(null);
        }}
        slides={selectedImages?.images}
        index={selectedImages?.defaultIdx}
      />
      {isFetchingNextPage && (
        <Box sx={{ display: "flex", justifyContent: "center" }}>
          <CircularProgress size={30} />
        </Box>
      )}
    </Box>
  );
};

PerformanceTable.propTypes = {
  tableData: PropType.arrayOf(
    PropType.shape({
      id: PropType.string,
      modelInput: PropType.string,
      modelOutput: PropType.string,
      score: PropType.oneOfType([PropType.string, PropType.number]),
      explanation: PropType.string,
      date: PropType.string,
    }),
  ),
  fetchNextPage: PropType.func,
  isLoading: PropType.bool,
  isFetchingNextPage: PropType.bool,
  setSelectedTags: PropType.func,
  selectedMetric: PropType.object,
};

export default PerformanceTable;
