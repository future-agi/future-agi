import { Box, Divider, Skeleton, useTheme } from "@mui/material";
import React from "react";

const LoadingModelHover = () => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        padding: "16px",
        backgroundColor: "background.paper",
        borderRadius: "8px",
      }}
    >
      <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
        <Skeleton variant="circular" width={24} height={24} />
        <Skeleton variant="rectangular" width={100} />
      </Box>
      <Box
        sx={{
          width: "100%",
          overflow: "hidden",
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "divider",
          marginY: "8px",
        }}
      >
        <table
          border={1}
          width="100%"
          style={{
            borderCollapse: "collapse",
            borderColor: theme.palette.divider,
          }}
        >
          <tr>
            <th style={{ textAlign: "left", padding: "4px" }}>
              <Skeleton variant="rectangular" height={16} width={100} />
            </th>
            <th style={{ textAlign: "left", padding: "4px", width: "33%" }}>
              <Skeleton variant="rectangular" height={16} width={30} />
            </th>
            <th style={{ textAlign: "left", padding: "4px", width: "33%" }}>
              <Skeleton variant="rectangular" height={16} width={30} />
            </th>
          </tr>
          <tr>
            <td style={{ padding: "4px" }}>
              <Skeleton variant="rectangular" height={16} width={80} />
            </td>
            <td style={{ padding: "4px" }}>
              <Skeleton variant="rectangular" height={16} width={40} />
            </td>
            <td style={{ padding: "4px" }}>
              <Skeleton variant="rectangular" height={16} width={40} />
            </td>
          </tr>
          <tr>
            <td style={{ padding: "4px" }}>
              <Skeleton variant="rectangular" height={16} width={80} />
            </td>
            <td style={{ padding: "4px" }}>
              <Skeleton variant="rectangular" height={16} width={40} />
            </td>
            <td style={{ padding: "4px" }}>
              <Skeleton variant="rectangular" height={16} width={40} />
            </td>
          </tr>
        </table>
      </Box>
      <Divider />
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          gap: "12px",
          marginY: "8px",
        }}
      >
        <Box
          sx={{
            display: "flex",
            gap: "4px",
            flexDirection: "column",
            "& > ul > li::marker": {
              fontSize: "12px",
              color: "text.disabled",
            },
            width: "100%",
          }}
        >
          <Skeleton variant="rectangular" height={16} width={100} />
          <Skeleton variant="rectangular" height={70} />
        </Box>
        <Box>
          <Divider orientation="vertical" />
        </Box>
        <Box
          sx={{
            display: "flex",
            gap: "4px",
            flexDirection: "column",
            "& > ul > li::marker": {
              fontSize: "12px",
              color: "text.disabled",
            },
            width: "100%",
          }}
        >
          <Skeleton variant="rectangular" height={16} width={100} />
          <Skeleton variant="rectangular" height={90} />
        </Box>
      </Box>
      <Divider />
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          gap: "12px",
          marginTop: "8px",
        }}
      >
        <Box
          sx={{
            display: "flex",
            gap: "4px",
            flexDirection: "column",
            "& > ul > li::marker": {
              color: "transparent",
            },
            width: "100%",
          }}
        >
          <Skeleton variant="rectangular" height={16} width={80} />
          <Skeleton variant="rectangular" height={16} width={50} />
        </Box>
        <Box>
          <Divider orientation="vertical" />
        </Box>
        <Box
          sx={{
            display: "flex",
            gap: "4px",
            flexDirection: "column",
            "& > ul > li::marker": {
              fontSize: "12px",
              color: "text.disabled",
            },
            width: "100%",
          }}
        >
          <Skeleton variant="rectangular" height={16} width={80} />
          <Skeleton variant="rectangular" height={16} width={50} />
        </Box>
        <Box>
          <Divider orientation="vertical" />
        </Box>
        <Box
          sx={{
            display: "flex",
            gap: "4px",
            flexDirection: "column",
            "& > ul > li::marker": {
              fontSize: "12px",
              color: "text.disabled",
            },
            width: "100%",
          }}
        >
          <Skeleton variant="rectangular" height={16} width={80} />
          <Skeleton variant="rectangular" height={16} width={100} />
        </Box>
      </Box>
    </Box>
  );
};

export default LoadingModelHover;
