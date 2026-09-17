import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box, Button, Typography } from "@mui/material";
import {
  LIST_CURSOR_CONTINUATION_NOTICE,
  createListCursorPagination,
  isListCursorContinuationLimitError,
  isListCursorProtocolError,
  loadExactListPage,
} from "src/sections/projects/LLMTracing/listCursorPagination";

/**
 * Build the AG Grid datasource used by annotation trace/span/session pickers.
 *
 * A backend transport page may be empty while its signed checkpoint proves
 * that more candidates remain. The shared exact-page loader retains those
 * rows and the checkpoint until a complete visible page can be published.
 */
export const createExactSelectorDataSource = ({
  pagination,
  targetRowCount,
  getBaseParams,
  request,
  rowsFromResponse,
  metadataFromResponse,
  rowIdentity,
  ensureQuerySignature = () => {},
  onPageLoaded = () => {},
  onPaused = () => {},
  onFailure = () => {},
  maxContinuations,
  maxElapsedMs,
}) => ({
  getRows: async (gridParams) => {
    const { request: gridRequest } = gridParams;
    ensureQuerySignature(gridRequest);
    const requestGeneration = pagination.generation();
    const blockSize = Math.max(
      1,
      Number(gridRequest?.endRow) - Number(gridRequest?.startRow),
    );
    const pageNumber = Math.floor(
      Math.max(0, Number(gridRequest?.startRow) || 0) / blockSize,
    );
    const buildParams = () =>
      pagination.requestParams(pageNumber, {
        ...getBaseParams(gridRequest),
        page_size: targetRowCount,
      });

    try {
      const exactPage = await loadExactListPage({
        pagination,
        pageNumber,
        targetRowCount,
        loadResponse: (signal) => request(buildParams(), signal),
        nextResponse: (_cursor, signal) => request(buildParams(), signal),
        rowsFromResponse,
        metadataFromResponse,
        rowIdentity,
        isCurrent: () => pagination.isCurrent(requestGeneration),
        ...(maxContinuations === undefined ? {} : { maxContinuations }),
        ...(maxElapsedMs === undefined ? {} : { maxElapsedMs }),
      });

      if (!pagination.isCurrent(requestGeneration) || exactPage.stale) {
        gridParams.fail();
        return;
      }

      onPageLoaded(exactPage, gridParams);
      const rowCount = exactPage.isLastPage
        ? gridRequest.startRow + exactPage.rows.length
        : -1;
      gridParams.success({ rowData: exactPage.rows, rowCount });
      onPaused(null);
    } catch (error) {
      if (!pagination.isCurrent(requestGeneration)) {
        gridParams.fail();
        return;
      }
      const hasRetainedCheckpoint = Boolean(
        pagination.bufferedVisiblePage(pageNumber),
      );
      if (
        isListCursorContinuationLimitError(error) ||
        (hasRetainedCheckpoint && !isListCursorProtocolError(error))
      ) {
        // A transport failure after an advancing bounded response must not
        // discard the already-proven rows/checkpoint or become a terminal
        // grid error. Keep the block retryable from that signed position.
        onPaused(() => {
          if (gridParams.api?.retryServerSideLoads) {
            gridParams.api.retryServerSideLoads();
          } else {
            gridParams.api?.refreshServerSide?.({ purge: false });
          }
        });
        gridParams.fail();
        return;
      }

      onPaused(null);
      onFailure(error);
      gridParams.fail();
    }
  },
});

export const useExactSelectorDataSource = ({
  querySignature,
  targetRowCount,
  getBaseParams,
  request,
  rowsFromResponse,
  metadataFromResponse,
  rowIdentity,
  onPageLoaded,
  onFailure,
}) => {
  const paginationRef = useRef(createListCursorPagination());
  const activeSignatureRef = useRef(null);
  const [continuationResume, setContinuationResume] = useState(null);

  useEffect(() => {
    paginationRef.current.reset();
    activeSignatureRef.current = null;
    setContinuationResume(null);
  }, [querySignature]);

  const ensureQuerySignature = useCallback(
    (gridRequest) => {
      const sortSignature = JSON.stringify(gridRequest?.sortModel || []);
      const nextSignature = `${querySignature}:${sortSignature}`;
      if (activeSignatureRef.current === nextSignature) return;
      paginationRef.current.reset();
      activeSignatureRef.current = nextSignature;
    },
    [querySignature],
  );

  const dataSource = useMemo(
    () =>
      createExactSelectorDataSource({
        pagination: paginationRef.current,
        targetRowCount,
        getBaseParams,
        request,
        rowsFromResponse,
        metadataFromResponse,
        rowIdentity,
        ensureQuerySignature,
        onPageLoaded,
        onPaused: (resume) => setContinuationResume(() => resume),
        onFailure,
      }),
    [
      ensureQuerySignature,
      getBaseParams,
      metadataFromResponse,
      onFailure,
      onPageLoaded,
      request,
      rowIdentity,
      rowsFromResponse,
      targetRowCount,
    ],
  );

  const continueSearch = useCallback(() => {
    if (!continuationResume) return;
    const resume = continuationResume;
    setContinuationResume(null);
    resume();
  }, [continuationResume]);

  return {
    dataSource,
    continuationPending: Boolean(continuationResume),
    continueSearch,
  };
};

export function ExactSelectorContinuationNotice({ pending, onContinue }) {
  if (!pending) return null;

  return (
    <Box
      role="status"
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 1,
        px: 1.5,
        py: 0.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "action.hover",
      }}
    >
      <Typography variant="caption" color="text.secondary">
        {LIST_CURSOR_CONTINUATION_NOTICE}
      </Typography>
      <Button size="small" variant="outlined" onClick={onContinue}>
        Continue search
      </Button>
    </Box>
  );
}

ExactSelectorContinuationNotice.propTypes = {
  pending: PropTypes.bool,
  onContinue: PropTypes.func.isRequired,
};
