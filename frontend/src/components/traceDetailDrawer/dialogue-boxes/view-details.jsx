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
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import ErrorLocalizeCard from "src/sections/common/ErrorLocalizeCard";
import axios, { endpoints } from "src/utils/axios";
import { getChipColor, getChipLabel, getFontColor } from "../common";
import CellMarkdown from "src/sections/common/CellMarkdown";
import AudioErrorCard from "src/components/custom-audio/AudioErrorCard";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import { canonicalEntries } from "src/utils/utils";

const ViewDetailsModal = ({ open, onClose, selectedViewDetail, title }) => {
  const theme = useTheme();
  const typographyTheme = theme.typography;
  const [isRefreshing] = useState(false);
  const ids = selectedViewDetail?.id.split("**");
  const observationSpanId = ids?.pop();
  const customEvalConfigId = ids?.[0];
  const { data, isPending } = useQuery({
    queryKey: ["span-details", customEvalConfigId, observationSpanId],
    queryFn: () =>
      axios.get(
        endpoints.project.getEvalDetails(observationSpanId, customEvalConfigId),
      ),
    enabled: !!customEvalConfigId && !!observationSpanId,
    select: (d) => d?.data?.result,
  });
  const input1 = useMemo(() => {
    if (!isPending) {
      const input1 = data?.errorAnalysis?.input1;
      return Array.isArray(input1) ? input1 : input1 ? [input1] : [];
    }
  }, [data?.errorAnalysis?.input1, isPending]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Box
        sx={{
          height: "85%",
          overflowY: "auto",
          bgcolor: "background.paper",
          boxShadow: 24,
          width: "625px",
          borderRadius: theme.spacing(1.5),
          padding: theme.spacing(2.5),
          display: "flex",
          flexDirection: "column",
          gap: "16px",
        }}
      >
        <Box
          display={"flex"}
          alignItems={"center"}
          justifyContent={"space-between"}
          flexDirection={"row"}
        >
          <Typography
            fontSize={typographyTheme.body1.fontSize}
            fontWeight={typographyTheme.fontWeightBold}
            color={theme.palette.text.primary}
          >
            {title}
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="mingcute:close-line" />
          </IconButton>
        </Box>
        <Box
          sx={{
            gap: "8px",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Typography
            fontWeight={typographyTheme.fontWeightMedium}
            fontSize={typographyTheme.subtitle2.fontSize}
            color={theme.palette.text.primary}
          >
            Score
          </Typography>
          {isPending ? (
            <Box
              sx={{
                display: "flex",
                gap: "8px",
                flexDirection: "row",
              }}
            >
              {[1, 2, 3].map((i, k) => (
                <Skeleton
                  key={k}
                  sx={{
                    width: "60px",
                    height: "24px",
                    borderRadius: "8px",
                  }}
                />
              ))}
            </Box>
          ) : Array.isArray(data?.score) ? (
            <Box
              sx={{
                display: "flex",
                flexDirection: "row",
              }}
            >
              {data?.score.map((item, idx) => (
                <Chip
                  key={idx}
                  variant="soft"
                  label={item}
                  size="small"
                  sx={{
                    marginRight: 0.5,
                    backgroundColor: theme.palette.action.hover,
                    color: theme.palette.primary.main,
                    fontWeight: typographyTheme.fontWeightRegular,
                    width: "fit-content",
                  }}
                />
              ))}
            </Box>
          ) : (
            <Chip
              variant="soft"
              label={getChipLabel(data)}
              size="small"
              sx={{
                backgroundColor: getChipColor(data),
                color: getFontColor(data),
                width: "fit-content",
              }}
            />
          )}
        </Box>
        <Box
          sx={{
            gap: "8px",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Typography
            fontWeight={typographyTheme.fontWeightMedium}
            fontSize={typographyTheme.subtitle2.fontSize}
            color={theme.palette.text.primary}
          >
            Explanation
          </Typography>
          {isPending ? (
            <Skeleton
              sx={{
                minHeight: "128px",
                borderRadius: "8px",
              }}
            />
          ) : (
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: theme.spacing(1),
              }}
            >
              {data?.explanation ? (
                <Typography
                  fontSize={typographyTheme.body2.fontSize}
                  sx={{ padding: (theme) => theme.spacing(2) }}
                >
                  <CellMarkdown spacing={0} text={data?.explanation} />
                </Typography>
              ) : (
                <Box
                  sx={{
                    padding: theme.spacing(2),
                  }}
                >
                  <Typography
                    fontSize={typographyTheme.body2.fontSize}
                    fontWeight={typographyTheme.fontWeightRegular}
                    color={theme.palette.text.primary}
                  >
                    Unable to fetch explanation
                  </Typography>
                </Box>
              )}
            </Box>
          )}
        </Box>
        <Box
          sx={{
            gap: "8px",
            display: "flex",
            flexDirection: "column",
            flexGrow: 1,
          }}
        >
          <Typography
            fontWeight={typographyTheme.fontWeightMedium}
            fontSize={typographyTheme.subtitle2.fontSize}
            color="text.primary"
          >
            Possible Error
          </Typography>
          {isPending ? (
            <Skeleton
              sx={{
                flexGrow: 1,
                minHeight: "280px",
                borderRadius: "8px",
              }}
            />
          ) : data?.score === "error" ? (
            <Box
              sx={{
                display: "flex",
                gap: 1,
                bgcolor: "red.o5",
                border: "1px solid",
                borderColor: "red.200",
                borderRadius: theme.spacing(0.5),
                padding: 2,
                justifyContent: "space-between",
                height: theme.spacing(7.5),
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
                <Typography
                  color={theme.palette.red["500"]}
                  fontSize={typographyTheme.body2.fontSize}
                  fontWeight={typographyTheme.fontWeightMedium}
                >
                  We couldn&apos;t fetch the errors right now.
                </Typography>
              </Box>
              {isRefreshing ? (
                <Box display="flex" alignItems="center" justifyContent="center">
                  <CircularProgress size={18} color="error" />
                </Box>
              ) : (
                <Button
                  aria-label="fetch-error"
                  style={{
                    fontSize: typographyTheme.body2.fontSize,
                    textDecoration: "underline",
                    padding: 0,
                    fontWeight: typographyTheme.fontWeightMedium,
                    color: theme.palette.text.primary,
                  }}
                  // onClick={handleTryAgain}
                >
                  Try again
                </Button>
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
              {data &&
                typeof data === "object" &&
                data?.errorAnalysis &&
                (() => {
                  const errorAnalysisEntries = canonicalEntries(
                    data?.errorAnalysis,
                  );
                  const hasOrgSegment = errorAnalysisEntries
                    .map(([, value]) => value)
                    .flat()
                    .some((entry) => entry?.orgSegment);

                  if (hasOrgSegment) {
                    return (
                      <AudioPlaybackProvider>
                        <AudioErrorCard
                          valueInfos={data}
                          column={data?.selectedInputKey}
                        />
                      </AudioPlaybackProvider>
                    );
                  }

                  return errorAnalysisEntries
                    .filter(([_, value]) => value?.length)
                    .map(([key, value]) => (
                      <ErrorLocalizeCard
                        key={key}
                        value={value}
                        column={data?.selectedInputKey}
                        datapoint={data}
                      />
                    ));
                })()}
            </Box>
          ) : (
            <Typography fontSize="14px" color="text.primary">
              No errors found.
            </Typography>
          )}
        </Box>
        {/* <Box
          sx={{
            display:'flex',
            justifyContent:'space-between',
            gap:'12px'
          }}
        >
          <Button
            variant="outlined"
            fullWidth
            sx={{
              color:'text.disabled',
              fontWeight:'400',
            }}
            onClick={onClose}
          >Cancel</Button>
          <Button
            variant="contained"
            fullWidth
            sx={{
              bgcolor:'primary.main',
              ":hover":{
                bgcolor:'primary.dark'
              }
            }}
          >
            Add Feedback
          </Button>
        </Box> */}
      </Box>
    </Modal>
  );
};

ViewDetailsModal.propTypes = {
  selectedViewDetail: PropTypes.object,
  open: PropTypes.bool,
  title: PropTypes.string,
  onClose: PropTypes.func,
};

export default ViewDetailsModal;
