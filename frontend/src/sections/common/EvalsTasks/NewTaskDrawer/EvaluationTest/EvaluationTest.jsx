import React from "react";
import { Typography, Box, useTheme, Chip } from "@mui/material";
import Iconify from "src/components/iconify";

const EvaluationTest = () => {
  const theme = useTheme();

  const staticInputValue = "This is a static input value.";
  const staticOutputValue = "This is a static output value.";
  const resultStatus = "Failed";

  return (
    <Box
      display="flex"
      flexDirection="column"
      minHeight="100vh"
      sx={{
        minWidth: "33vw",
        bgcolor: "background.paper",
        paddingY: 2,
        paddingX: 2.5,
        boxShadow: "none",
        borderRight: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box
        sx={{
          marginBottom: 1.5,
        }}
      >
        <Typography variant="h6" color="text.disabled" component="div">
          Evaluation Test
        </Typography>
        <Box
          sx={{ display: "flex", alignItems: "center", gap: 0.5, marginTop: 1 }}
        >
          <Iconify
            icon="solar:info-circle-bold"
            color="text.disabled"
            width={18}
          />
          <Typography fontSize="13px" color="text.secondary">
            Run a sample evals to see how it works
          </Typography>
        </Box>
      </Box>

      <Box
        sx={{
          marginTop: 2,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "10px",
        }}
      >
        <Box
          sx={{
            borderBottom: "1px solid",
            paddingX: 2,
            paddingY: 1.5,
            borderColor: "divider",
            display: "flex",
          }}
        >
          <Typography variant="subtitle1" color="text.secondary">
            Input
          </Typography>
        </Box>
        <textarea
          // {...register("input")}
          // placeholder="Input"
          value={staticInputValue}
          readOnly
          style={{
            width: "100%",
            minHeight: "100px",
            padding: "14px",
            border: "none",
            resize: "none",
            fontFamily: "inherit",
            borderBottomLeftRadius: "10px",
            borderBottomRightRadius: "10px",
            outline: "none",
            backgroundColor: theme.palette.background.default,
            verticalAlign: "top",
            fontSize: "15px",
          }}
        />
      </Box>
      <Box
        sx={{
          marginTop: 2,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "10px",
        }}
      >
        <Box
          sx={{
            borderBottom: "1px solid",
            paddingX: 2,
            paddingY: 1.5,
            borderColor: "divider",
            display: "flex",
          }}
        >
          <Typography variant="subtitle1" color="text.secondary">
            Output
          </Typography>
        </Box>
        <textarea
          // {...register("output")}
          // placeholder="output"
          value={staticOutputValue}
          readOnly
          style={{
            width: "100%",
            minHeight: "100px",
            padding: "14px",
            border: "none",
            resize: "none",
            fontFamily: "inherit",
            borderBottomLeftRadius: "10px",
            borderBottomRightRadius: "10px",
            outline: "none",
            backgroundColor: theme.palette.background.default,
            verticalAlign: "top",
            fontSize: "15px",
            color: theme.palette.text.primary,
          }}
        />
      </Box>
      <Box
        sx={{
          marginTop: 2,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "10px",
          minHeight: "220px",
          position: "relative",
        }}
      >
        <Box
          sx={{
            borderBottom: "1px solid",
            paddingX: 2,
            paddingY: 1.5,
            borderColor: "divider",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography variant="subtitle1" color="text.secondary">
            Result
          </Typography>
          <Chip
            label={resultStatus}
            sx={{
              borderWidth: "1px",
              borderStyle: "solid",
              borderRadius: "10px",
              ...(resultStatus === "Passed"
                ? {
                    backgroundColor: theme.palette.success.light,
                    borderColor: theme.palette.success.main,
                    color: "text.secondary",
                    "&:hover": {
                      backgroundColor: theme.palette.success.light,
                      borderColor: theme.palette.success.main,
                    },
                  }
                : {
                    backgroundColor: "#DB2F2D14",
                    borderColor: "#DB2F2D29",
                    color: "text.secondary",
                    "&:hover": {
                      backgroundColor: "#DB2F2D14",
                      borderColor: "#DB2F2D29",
                    },
                  }),
            }}
          />
        </Box>

        <Box
          sx={{
            paddingX: 1.5,
            paddingY: 2,
          }}
        >
          {/* Pass Label and Value */}
          <Box
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 1,
              backgroundColor: "background.neutral",
              paddingX: 3,
              borderRadius: "8px",
              border: "1px solid",
              borderColor: "divider",
              marginBottom: 2,
            }}
          >
            <Typography
              variant="subtitle2"
              color="text.secondary"
              sx={{ fontWeight: "500" }}
            >
              Pass
            </Typography>
            <Typography variant="h5" color="text.primary">
              0
            </Typography>
          </Box>
          <Typography sx={{ marginTop: 1, display: "block" }}>
            Evaluation failed as similarity score 0.0 is below the failure
            threshold of 0.75 using CosineSimilarity
          </Typography>
        </Box>
        <Box
          sx={{
            position: "absolute",
            bottom: 8,
            right: 12,
            display: "flex",
            alignItems: "center",
            gap: 0.5,
          }}
        >
          <Iconify icon="mdi:clock-outline" width={18} color="text.primary" />{" "}
          <Typography variant="body2" color="text.primary">
            Ran in 13 sec
          </Typography>
        </Box>
      </Box>
    </Box>
  );
};

export default EvaluationTest;
