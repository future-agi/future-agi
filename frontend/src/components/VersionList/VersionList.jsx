import {
  Box,
  FormControlLabel,
  Radio,
  RadioGroup,
  styled,
  Typography,
  useTheme,
  Skeleton,
} from "@mui/material";
import PropTypes from "prop-types";
import { formatDistanceToNow, isValid } from "date-fns";
import React from "react";

const formatTimeAgo = (dateStr) => {
  const date = new Date(dateStr);
  return isValid(date) ? formatDistanceToNow(date, { addSuffix: true }) : "—";
};
import { DraftBadge } from "src/sections/workbench/createPrompt/SharedStyledComponents";

const RADIO_SIZE = 18;
// FormControlLabel adds padding-left (~14px from MUI default) + pl:1 (8px) + half radio = center
const CONNECTOR_LEFT = 23.5;
const CONNECTOR_TOP = 30;
const CONNECTOR_BOTTOM = -21;
const CONNECTOR_WIDTH = 2;
const SCROLLBAR_WIDTH = 6;
const SKELETON_COUNT_INITIAL = 4;
const SKELETON_COUNT_NEXT = 3;

const BpIcon = styled("span")(({ theme }) => ({
  borderRadius: "50%",
  width: RADIO_SIZE,
  height: RADIO_SIZE,
  boxShadow: `inset 0 0 0 3px ${theme.palette.mode === "dark" ? "#52525b" : "#D1D1D1"}, inset 0 -1px 0 rgba(16,22,26,.1)`,
  backgroundColor: theme.palette.background.paper,
  backgroundImage:
    "linear-gradient(180deg,hsla(0,0%,100%,.8),hsla(0,0%,100%,0))",
  ".Mui-focusVisible &": {
    outline: "2px auto rgba(19,124,189,.6)",
    outlineOffset: 2,
  },
  "input:disabled ~ &": {
    boxShadow: "none",
    background: "rgba(206,217,224,.5)",
    ...theme.applyStyles?.("dark", {
      background: "rgba(57,75,89,.5)",
    }),
  },
  ...theme.applyStyles?.("dark", {
    boxShadow: "0 0 0 1px rgb(16 22 26 / 40%)",
    backgroundColor: "#394b59",
    backgroundImage:
      "linear-gradient(180deg,hsla(0,0%,100%,.05),hsla(0,0%,100%,0))",
  }),
}));

const BpCheckedIcon = styled(BpIcon)(({ theme }) => ({
  backgroundColor: theme.palette.primary.main,
  backgroundImage:
    "linear-gradient(180deg,hsla(0,0%,100%,.1),hsla(0,0%,100%,0))",
  boxShadow: "none",
  "&::before": {
    display: "block",
    width: RADIO_SIZE,
    height: RADIO_SIZE,
    backgroundImage: `radial-gradient(${theme.palette.primary.contrastText},${theme.palette.primary.contrastText} 28%,transparent 32%)`,
    content: '""',
  },
  "input:hover ~ &": {
    backgroundColor: theme.palette.primary.dark,
  },
}));

function BpRadio(props) {
  return (
    <Radio
      disableRipple
      color="default"
      checkedIcon={<BpCheckedIcon />}
      icon={<BpIcon />}
      {...props}
    />
  );
}

// Skeleton loader for versions
const VersionSkeleton = () => {
  return (
    <Box
      sx={{
        position: "relative",
        mb: 2,
        pl: 1,
        display: "flex",
        gap: 1.5,
      }}
    >
      <Skeleton
        variant="circular"
        width={RADIO_SIZE}
        height={RADIO_SIZE}
        sx={{ mt: 1, flexShrink: 0 }}
      />
      <Box sx={{ flex: 1, py: 0.8 }}>
        <Skeleton variant="text" width="60%" height={20} sx={{ mb: 0.5 }} />
        <Skeleton variant="text" width="40%" height={16} sx={{ mb: 0.5 }} />
        <Skeleton variant="text" width="80%" height={16} />
      </Box>
    </Box>
  );
};

const VersionList = ({
  versions = [],
  selectedVersion,
  onVersionChange,
  isLoading = false,
  isFetchingNextVersions = false,
  scrollContainerRef,
  sx,
}) => {
  const theme = useTheme();

  return (
    <Box
      ref={scrollContainerRef}
      sx={{
        flex: 1,
        overflowY: "auto",
        minWidth: "200px",
        scrollbarWidth: "thin",
        scrollbarColor: "rgba(0, 0, 0, 0.3) transparent",
        "&::-webkit-scrollbar": {
          width: `${SCROLLBAR_WIDTH}px`,
        },
        "&::-webkit-scrollbar-thumb": {
          backgroundColor: "rgba(0, 0, 0, 0.3)",
          borderRadius: `${SCROLLBAR_WIDTH / 2}px`,
        },
        "&::-webkit-scrollbar-track": {
          backgroundColor: "transparent",
        },
        px: theme.spacing(1),
        py: 1,
        ...sx,
      }}
    >
      {isLoading ? (
        <Box sx={{ px: 1 }}>
          {[...Array(SKELETON_COUNT_INITIAL)].map((_, i) => (
            <VersionSkeleton key={`initial-skeleton-${i}`} />
          ))}
        </Box>
      ) : !versions?.length ? (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            py: 4,
          }}
        >
          <Typography typography="s2" color="text.disabled">
            No versions available
          </Typography>
        </Box>
      ) : (
        <RadioGroup value={selectedVersion || ""}>
          {versions?.map((v, index) => (
            <Box
              key={v.id}
              sx={{
                position: "relative",
                mb: 2,
                pl: 1,
                borderRadius: 1,
                cursor: "pointer",
                "&:hover": {
                  backgroundColor: "action.hover",
                },
                "&::after": {
                  content: '""',
                  position: "absolute",
                  left: CONNECTOR_LEFT,
                  top: CONNECTOR_TOP,
                  bottom: CONNECTOR_BOTTOM,
                  width: `${CONNECTOR_WIDTH}px`,
                  backgroundColor: "action.hover",
                  display: index === versions.length - 1 ? "none" : "block",
                },
              }}
            >
              <FormControlLabel
                value={v.id}
                control={
                  <BpRadio
                    disableRipple
                    onChange={(e) => {
                      if (onVersionChange) {
                        onVersionChange(e.target.value, v.versionNameDisplay);
                      }
                    }}
                  />
                }
                label={
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 0.5,
                      py: 0.8,
                      width: "100%",
                    }}
                  >
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Typography
                        typography="s2_1"
                        fontWeight="fontWeightMedium"
                        color={"text.primary"}
                      >
                        Version {v.version_name_display}
                      </Typography>
                      {v.isDraft && <DraftBadge>Draft</DraftBadge>}
                    </Box>
                    <Typography
                      typography="s3"
                      fontWeight="fontWeightRegular"
                      color="text.disabled"
                    >
                      {formatTimeAgo(v.created_at)}
                    </Typography>
                    <Typography
                      typography="s3"
                      fontWeight="fontWeightRegular"
                      color="text.primary"
                    >
                      {v.commit_message}
                    </Typography>
                  </Box>
                }
                sx={{ alignItems: "flex-start", m: 0, width: "100%" }}
              />
            </Box>
          ))}
        </RadioGroup>
      )}

      {/* Loading skeletons while fetching next page */}
      {isFetchingNextVersions && (
        <Box sx={{ px: 1 }}>
          {[...Array(SKELETON_COUNT_NEXT)].map((_, i) => (
            <VersionSkeleton key={`skeleton-${i}`} />
          ))}
        </Box>
      )}
    </Box>
  );
};

VersionList.propTypes = {
  versions: PropTypes.array,
  selectedVersion: PropTypes.string,
  onVersionChange: PropTypes.func,
  isLoading: PropTypes.bool,
  isFetchingNextVersions: PropTypes.bool,
  scrollContainerRef: PropTypes.oneOfType([
    PropTypes.func,
    PropTypes.shape({ current: PropTypes.any }),
  ]),
  sx: PropTypes.object,
};

export default VersionList;
