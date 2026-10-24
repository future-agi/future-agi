import { Box, Divider, Typography, useTheme } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";

const AnnotatorPerformace = ({ annotatorList = [] }) => {
  const theme = useTheme();
  if (annotatorList.length === 0) {
    return <></>;
  }
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        width: "100%",
        marginTop: 2,
      }}
    >
      <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
        Annotator perfomance
      </Typography>
      <Box display="flex" gap={2} flexWrap={"wrap"}>
        {annotatorList.map((item, index) => {
          const nameParts = item?.name?.trim().split(" "); // Split and trim the name
          const initials =
            nameParts?.length >= 2
              ? `${nameParts[0][0]}${nameParts[1][0]}` // First letters of the first two words
              : nameParts?.length > 0
                ? `${nameParts[0][0]}`
                : "";
          return (
            <Box
              key={index}
              sx={{
                width: "calc(33.3% - 12px)",
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                padding: 2,
                display: "flex",
                flexDirection: "column",
                gap: "10px",
                backgroundColor: "background.neutral",
              }}
            >
              <Box
                display={"flex"}
                flexDirection={"column"}
                alignItems={"center"}
                gap="10px"
              >
                {initials && (
                  <Box
                    sx={{
                      borderRadius: "50%",
                      // @ts-ignore
                      background: `linear-gradient(90deg, ${theme.palette.primary.main}, ${theme.palette.pink[500]})`,
                      width: "54px",
                      height: "54px",
                      display: "flex",
                      justifyContent: "center",
                      alignItems: "center",
                    }}
                  >
                    <Box
                      sx={{
                        borderRadius: "50%",
                        backgroundColor: "background.paper",
                        width: "50px",
                        height: "50px",
                        display: "flex",
                        justifyContent: "center",
                        alignItems: "center",
                      }}
                    >
                      <Typography
                        typography={"m1"}
                        fontWeight={"fontWeightSemiBold"}
                        sx={{
                          // @ts-ignore
                          background: `linear-gradient(90deg, ${theme.palette.primary.main}, ${theme.palette.pink[500]})`,
                          backgroundClip: "text",
                          color: "transparent",
                          display: "inline-block",
                        }}
                      >
                        {initials.toUpperCase()}
                      </Typography>
                    </Box>
                  </Box>
                )}
                <Typography typography={"m1"} fontWeight={"fontWeightMedium"}>
                  {item.name}
                </Typography>
              </Box>
              <Box
                sx={{
                  padding: 1,
                  display: "flex",
                  gap: 1.25,
                  backgroundColor: "background.paper",
                  borderRadius: "4px",
                }}
              >
                <Box
                  sx={{
                    flex: 1,
                    textAlign: "center",
                    display: "flex",
                    flexDirection: "column",
                  }}
                >
                  <Typography
                    typography={"s2"}
                    fontWeight={"fontWeightMedium"}
                    color="text.secondary"
                  >
                    Annotations
                  </Typography>
                  <Typography typography={"m1"} fontWeight={"fontWeightMedium"}>
                    {item.annotations}
                  </Typography>
                </Box>
                <Divider orientation="vertical" />
                <Box
                  sx={{
                    flex: 1,
                    textAlign: "center",
                    display: "flex",
                    flexDirection: "column",
                  }}
                >
                  <Typography
                    typography={"s2"}
                    fontWeight={"fontWeightMedium"}
                    color="text.secondary"
                  >
                    Avg. time
                  </Typography>
                  <Typography typography={"m1"} fontWeight={"fontWeightMedium"}>
                    {item.avgTime || 0} mins
                  </Typography>
                </Box>
              </Box>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
};

export default AnnotatorPerformace;
AnnotatorPerformace.propTypes = {
  annotatorList: PropTypes.array,
};
