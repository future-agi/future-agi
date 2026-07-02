import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  Modal,
  Skeleton,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import ErrorLocalizeCard from "src/sections/common/ErrorLocalizeCard";
import CompositeResultView from "src/sections/evals/components/CompositeResultView";
import {
  getLabel,
  getStatusColor,
  normalizeEvalResult,
} from "src/sections/develop-detail/DataTab/common";

const ViewDetailsModal = ({
  open,
  onClose,
  data,
  refreshGrid,
  handleRefetchRowData,
  isRefetching,
  onAddFeedbackClick,
}) => {
  const theme = useTheme();
  const [isRefreshing, setIsRefreshing] = useState(false);

  const evalData = data;
  // Detect composite eval results from value_infos
  const compositeResult = useMemo(() => {
    const vi = data?.value_infos;
    const parsed =
      typeof vi === "string"
        ? (() => {
            try {
              return JSON.parse(vi);
            } catch {
              return null;
            }
          })()
        : vi;
    if (!parsed) return null;
    const isComposite =
      parsed.composite_id ||
      (Array.isArray(parsed.children) &&
        parsed.children.length > 0 &&
        parsed.children[0]?.child_id);
    if (!isComposite) return null;
    return {
      aggregation_enabled: parsed.aggregation_enabled,
      aggregation_function: parsed.aggregation_function,
      aggregate_score: parsed.aggregate_score,
      aggregate_pass: parsed.aggregate_pass,
      summary: parsed.summary,
      children: parsed.children || [],
      total_children: parsed.children?.length ?? 0,
      completed_children: (parsed.children || []).filter(
        (c) => c?.status === "completed",
      ).length,
      failed_children: (parsed.children || []).filter(
        (c) => c?.status === "failed",
      ).length,
    };
  }, [data]);

  const cellValue = evalData?.cell_value;
  const cellMetadata = evalData?.metadata?.cell_metadata;
  const errorAnalysis = cellMetadata?.error_analysis;
  const normalizedResult = normalizeEvalResult(cellValue);
  const input1 = Array.isArray(errorAnalysis?.input1)
    ? errorAnalysis.input1
    : errorAnalysis?.input1
      ? [errorAnalysis.input1]
      : [];

  const handleTryAgain = () => {
    setIsRefreshing(true);

    if (handleRefetchRowData) {
      handleRefetchRowData();
    }
    refreshGrid?.({ purge: true });

    setTimeout(() => {
      setIsRefreshing(false);
    }, 5000);
  };

  if (!evalData) {
    return null;
  }

  return (
    <Modal open={open} onClose={onClose}>
      <Box
        sx={{
          position: "absolute",
          height: "85%",
          overflowY: "auto",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: "30%",
          bgcolor: "background.paper",
          boxShadow: 24,
          borderRadius: theme.spacing(1.5),
          padding: theme.spacing(2.5),
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          "&::-webkit-scrollbar": {
            width: "6px !important",
          },
          "&::-webkit-scrollbar-thumb": {
            backgroundColor: "rgba(0, 0, 0, 0.3)",
            borderRadius: "3px",
          },
          "&::-webkit-scrollbar-track": {
            backgroundColor: "transparent",
          },
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: theme.spacing(2),
          }}
        >
          <IconButton
            aria-label="close-eval-detail"
            onClick={() => onClose()}
            sx={{
              position: "absolute",
              top: theme.spacing(1.5),
              right: theme.spacing(1.5),
            }}
          >
            <Iconify icon="mingcute:close-line" />
          </IconButton>

          <Typography fontWeight={500} fontSize={16} color="text.primary">
            {evalData?.group?.name}
          </Typography>
          <Box>
            <Typography
              sx={{
                color: "text.primary",
                fontSize: "14px",
                fontWeight: 500,
                marginBottom: 0.5,
              }}
            >
              Score
            </Typography>
            {evalData?.status === "running" ? (
              <Skeleton
                variant="rounded"
                width={80}
                height={24}
                sx={{ borderRadius: 1 }}
              />
            ) : evalData?.status === "error" ? (
              <Typography sx={{ color: "red.700", margin: "8px" }}>
                error
              </Typography>
            ) : normalizedResult.kind === "choices" ? (
              normalizedResult.items.map((item, idx) => (
                <Chip
                  key={idx}
                  variant="soft"
                  label={item}
                  size="small"
                  sx={{
                    margin: "5px",
                    backgroundColor: "action.hover",
                    color: "primary.main",
                    fontWeight: "400",
                    pointerEvents: "none",
                    "&:hover": {
                      color: "primary.main",
                      backgroundColor: "action.hover",
                    },
                  }}
                />
              ))
            ) : (
              <Chip
                variant="soft"
                label={getLabel(cellValue)}
                size="small"
                sx={{
                  pointerEvents: "none",
                  ...getStatusColor(cellValue, theme),
                }}
              />
            )}
          </Box>
          <Box>
            <Typography
              fontWeight={500}
              fontSize={14}
              color="text.primary"
              marginBottom={0.5}
            >
              Explanation{" "}
            </Typography>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: theme.spacing(1),
              }}
            >
              {cellMetadata?.explanation ? (
                <ul>
                  <li>{cellMetadata?.explanation}</li>
                </ul>
              ) : (
                <Box
                  sx={{
                    padding: theme.spacing(2),
                  }}
                >
                  <Typography
                    fontSize="14px"
                    fontWeight={400}
                    color="text.primary"
                  >
                    Unable to fetch explanation
                  </Typography>
                </Box>
              )}
            </Box>
          </Box>
          {compositeResult && (
            <Box>
              <Typography
                fontWeight={500}
                fontSize={14}
                color="text.primary"
                marginBottom={0.5}
              >
                Composite Breakdown
              </Typography>
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: theme.spacing(1),
                  overflow: "hidden",
                }}
              >
                <CompositeResultView compositeResult={compositeResult} />
              </Box>
            </Box>
          )}
          {!compositeResult && (
          <Box>
            <Typography
              fontWeight={500}
              fontSize={14}
              color="text.primary"
              marginBottom={0.5}
            >
              Possible Error
            </Typography>

            {cellValue === "error" ? (
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 1,
                  bgcolor: "red.o5",
                  border: "1px solid",
                  borderColor: "red.200",
                  borderRadius: theme.spacing(0.5),
                  padding: 2,
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    gap: theme.spacing(1),
                    alignItems: "center",
                    justifyContent: "space-between",
                  }}
                >
                  <Box
                    sx={{
                      display: "flex",
                      gap: theme.spacing(1),
                      alignItems: "center",
                    }}
                  >
                    <Iconify
                      icon="uil:exclamation-triangle"
                      color="red.500"
                      width={20}
                    />
                    <Typography color="red.500" fontSize={14} fontWeight={500}>
                      Evaluation Failed
                    </Typography>
                  </Box>
                  {isRefreshing || isRefetching ? (
                    <Box
                      display="flex"
                      alignItems="center"
                      justifyContent="center"
                    >
                      <CircularProgress size={18} color="error" />
                    </Box>
                  ) : (
                    <Button
                      aria-label="fetch-error"
                      style={{
                        fontSize: "14px",
                        textDecoration: "underline",
                        padding: 0,
                        fontWeight: 500,
                        color: "var(--text-primary)",
                      }}
                      onClick={handleTryAgain}
                    >
                      Try again
                    </Button>
                  )}
                </Box>
                {evalData?.metadata?.reason && (
                  <Typography
                    color="red.700"
                    fontSize={13}
                    fontWeight={400}
                    sx={{
                      wordBreak: "break-word",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {evalData.metadata.reason}
                  </Typography>
                )}
              </Box>
            ) : Array.isArray(input1) && input1.length > 0 ? (
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: theme.spacing(1.5),
                }}
              >
                {Object.entries(input1).map(([key, value]) => {
                  const valueArray = Array.isArray(value) ? value : [value];
                  return (
                    <ErrorLocalizeCard
                      key={key}
                      value={valueArray}
                      column={
                        cellMetadata?.selected_input_key
                      }
                      datapoint={evalData}
                    />
                  );
                })}
              </Box>
            ) : (
              <Typography fontSize="14px" color="text.primary">
                No errors found.
              </Typography>
            )}
          </Box>
          )}
        </Box>
        <Box
          sx={{
            display: "flex",
            gap: theme.spacing(2),
            width: "100%",
            paddingTop: theme.spacing(2),
          }}
        >
          <Button
            aria-label="cancel"
            onClick={onClose}
            size="small"
            fullWidth
            variant="outlined"
          >
            Cancel
          </Button>
          <LoadingButton
            aria-label="add-feedback"
            fullWidth
            size="small"
            variant="contained"
            color="primary"
            onClick={() => onAddFeedbackClick?.(evalData)}
          >
            Add Feedback
          </LoadingButton>
        </Box>
      </Box>
    </Modal>
  );
};

ViewDetailsModal.propTypes = {
  data: PropTypes.object,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  handleRefetchRowData: PropTypes.func,
  isRefetching: PropTypes.bool,
  onAddFeedbackClick: PropTypes.func,
};

export default ViewDetailsModal;
