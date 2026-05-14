import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import Iconify from "src/components/iconify";
import AudioErrorCard from "src/components/custom-audio/AudioErrorCard";
import ErrorLocalizeCard from "src/sections/common/ErrorLocalizeCard";
import { ShowComponent } from "src/components/show";
import {
  getLabel,
  getStatusColor,
} from "src/sections/develop-detail/DataTab/common";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { canonicalEntries } from "src/utils/utils";

export default function ViewDetailsModal({
  evalDetail,
  onClose,
  handleOpenFeedbackForm,
  open,
  clearEvalDetail,
}) {
  const theme = useTheme();

  const finalArray = useMemo(() => {
    if (
      evalDetail?.cellValue &&
      evalDetail?.cellValue?.startsWith("[") &&
      evalDetail?.cellValue?.endsWith("]")
    ) {
      return JSON.parse(evalDetail?.cellValue?.replace(/'/g, '"'));
    }
  }, [evalDetail?.cellValue]);
  const metadataErrorAnalysis =
    evalDetail?.valueInfos?.metadata?.errorAnalysis ||
    evalDetail?.valueInfos?.metadata?.error_analysis;
  const metadataErrorAnalysisEntries = canonicalEntries(
    metadataErrorAnalysis || {},
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      TransitionProps={{
        onExited: () => {
          clearEvalDetail();
        },
      }}
      PaperProps={{
        sx: {
          width: "500px",
          maxWidth: "none",
          borderRadius: "16px !important",
          padding: "20px !important",
        },
      }}
    >
      <DialogTitle
        sx={{
          padding: 0,
          mb: "20px",
        }}
      >
        <Stack
          direction={"row"}
          justifyContent={"space-between"}
          alignItems={"center"}
        >
          <Typography
            color={"text.primary"}
            variant="m3"
            fontWeight={"fontWeightMedium"}
          >
            {evalDetail?.headerName}
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="material-symbols:close-rounded" />
          </IconButton>
        </Stack>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: 0,
          paddingBottom: "28px",
          display: "flex",
          flexDirection: "column",
          rowGap: "16px",
        }}
      >
        <Stack direction={"column"} gap={"8px"}>
          <Typography
            variant="s1"
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            Score
          </Typography>
          <Box>
            {evalDetail?.status === "error" ? (
              <Box sx={{ color: theme.palette.error.main, fontSize: "14px" }}>
                Error
              </Box>
            ) : (
              evalDetail?.cellValue &&
              evalDetail?.cellValue !== "" && (
                <>
                  <ShowComponent condition={!Array.isArray(finalArray)}>
                    <Chip
                      variant="soft"
                      label={getLabel(evalDetail?.cellValue)}
                      size="small"
                      sx={{
                        ...getStatusColor(evalDetail?.cellValue, theme),
                        transition: "none",
                        "&:hover": {
                          backgroundColor: getStatusColor(
                            evalDetail?.cellValue,
                            theme,
                          ).backgroundColor, // Lock it to same color
                          boxShadow: "none",
                        },
                      }}
                    />
                  </ShowComponent>
                  <ShowComponent condition={Array.isArray(finalArray)}>
                    <ShowComponent condition={finalArray?.length === 0}>
                      <Chip
                        variant="soft"
                        label={"None"}
                        size="small"
                        sx={{
                          backgroundColor: theme.palette.red.o10,
                          color: theme.palette.red[500],
                          marginRight: "10px",
                          transition: "none",
                          "&:hover": {
                            backgroundColor: theme.palette.red.o10, // Lock it to same color
                            boxShadow: "none",
                          },
                        }}
                      />
                    </ShowComponent>
                    <ShowComponent condition={finalArray?.length > 0}>
                      {finalArray?.map((val) => (
                        <Chip
                          key={val}
                          variant="soft"
                          label={val}
                          size="small"
                          sx={{
                            ...getStatusColor(evalDetail?.cellValue, theme),
                            marginRight: theme.spacing(1),
                            transition: "none",
                            "&:hover": {
                              backgroundColor: getStatusColor(
                                evalDetail?.cellValue,
                                theme,
                              ).backgroundColor, // Lock it to same color
                              boxShadow: "none",
                            },
                          }}
                        />
                      ))}
                    </ShowComponent>
                  </ShowComponent>
                </>
              )
            )}
          </Box>
        </Stack>
        <Stack direction={"column"} gap={"8px"}>
          <Typography
            variant="s1"
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            Explanation
          </Typography>
          <Box
            sx={{
              border: "1px solid",
              borderRadius: "8px",
              borderColor: "action.hover",
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.primary",
              p: "16px",
            }}
          >
            <CellMarkdown spacing={0} text={evalDetail?.valueInfos?.reason} />
          </Box>
        </Stack>
        <Stack direction={"column"} gap={"8px"}>
          <Typography
            variant="s1"
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            Possible Error
          </Typography>
          {/* <FailedFetchingError /> */}
          {metadataErrorAnalysisEntries.length === 0 && (
            <Typography
              color={"text.disabled"}
              typography={"s1"}
              fontWeight={"fontWeightRegular"}
            >
              No error found
            </Typography>
          )}
          <Box
            sx={{
              display: "flex",
              marginTop: "10px",
              flexDirection: "column",
              gap: 2,
              overflowY: "auto",
            }}
          >
            {evalDetail &&
              typeof evalDetail?.valueInfos?.metadata === "object" &&
              metadataErrorAnalysis &&
              (() => {
                const hasOrgSegment = metadataErrorAnalysisEntries
                  .map(([, value]) => value)
                  .flat()
                  .some((entry) => entry.orgSegment);

                if (hasOrgSegment) {
                  return (
                    <AudioErrorCard
                      valueInfos={evalDetail.valueInfos}
                      column={evalDetail.valueInfos.metadata.selectedInputKey}
                    />
                  );
                }

                return metadataErrorAnalysisEntries
                  .filter(([_, value]) => value.length)
                  .map(([key, value]) => (
                    <ErrorLocalizeCard
                      key={key}
                      value={value}
                      column={evalDetail.valueInfos.metadata.selectedInputKey}
                      datapoint={evalDetail.valueInfos}
                    />
                  ));
              })()}
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions
        sx={{
          padding: 0,
          paddingTop: "8px",
        }}
      >
        <Button
          onClick={onClose}
          variant="outlined"
          fullWidth
          sx={{
            "&:hover": {
              borderColor: "background.default",
            },
          }}
        >
          <Typography
            variant="s2"
            fontWeight={"fontWeightSemiBold"}
            color={"text.primary"}
          >
            Cancel
          </Typography>
        </Button>
        <Button
          onClick={handleOpenFeedbackForm}
          variant="contained"
          color="primary"
          fullWidth
        >
          <Typography
            variant="s2"
            fontWeight={"fontWeightMedium"}
            color={"white"}
          >
            Add Feedback
          </Typography>
        </Button>
      </DialogActions>
    </Dialog>
  );
}

ViewDetailsModal.propTypes = {
  evalDetail: PropTypes.object,
  onClose: PropTypes.func,
  handleOpenFeedbackForm: PropTypes.func,
  open: PropTypes.bool,
  clearEvalDetail: PropTypes.func,
};
