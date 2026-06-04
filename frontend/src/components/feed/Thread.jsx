import {
  Box,
  Divider,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { threadStatusMapper } from "../common";

export default function Thread({ data = [], sx }) {
  const theme = useTheme();
  return (
    <Stack
      sx={{
        ...sx,
      }}
    >
      <TextField size="small" label={"Add a comment"} />
      <Box
        sx={{
          position: "relative",
          pt: theme.spacing(2),
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
        }}
      >
        {data?.map((item, index) => (
          <Stack key={index} direction={"row"} gap={theme.spacing(1.5)}>
            <Box
              component={"img"}
              src={threadStatusMapper?.[item?.status]?.icon}
              sx={{
                height: 20,
                width: 20,
                zIndex: 20,
                color: "text.disabled",
                bgcolor: "background.paper",
              }}
            />
            <Stack>
              <Typography
                typography={"s2"}
                fontWeight={"fontWeightMedium"}
                color={"text.primary"}
              >
                {item?.title}
              </Typography>
              {item?.subtitle ? (
                <Typography
                  typography={"s2"}
                  color={"text.secondary"}
                  fontWeight={"fontWeightRegular"}
                >
                  {item?.subtitle}
                </Typography>
              ) : (
                <Typography
                  typography={"s2"}
                  color={"text.disabled"}
                  fontWeight={"fontWeightRegular"}
                >
                  By{" "}
                  <Typography
                    typography={"s2"}
                    component={"span"}
                    fontWeight={"fontWeightMedium"}
                  >
                    {item?.author}
                  </Typography>
                </Typography>
              )}
            </Stack>
            <Typography
              typography={"s2"}
              color={"text.disabled"}
              fontWeight={"fontWeightRegular"}
              sx={{
                ml: "auto",
                mt: 1,
              }}
            >
              {item?.timestamp}
            </Typography>
          </Stack>
        ))}
        <Divider
          orientation="vertical"
          sx={{
            position: "absolute",
            left: 10,
            top: 0,
            borderColor: "divider",
            bottom: 24,
            height: "auto", // ✅ allow top/bottom to define height
          }}
        />
      </Box>
    </Stack>
  );
}

Thread.propTypes = {
  data: PropTypes.array,
  sx: PropTypes.object,
};
