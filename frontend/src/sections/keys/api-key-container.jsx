import {
  Box,
  IconButton,
  InputAdornment,
  LinearProgress,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";

export default function ApiKeyContainer() {
  const [showTooltip, setShowTooltip] = useState({
    secretKey: false,
    apiKey: false,
  });

  const handleCopy = (text, field) => {
    navigator.clipboard.writeText(text);
    // Show tooltip
    setShowTooltip({ ...showTooltip, [field]: true });
    // Hide tooltip after 2 seconds
    setTimeout(() => {
      setShowTooltip({ ...showTooltip, [field]: false });
    }, 2000);
  };

  const {
    data: keysData,
    isPending,
    isSuccess,
  } = useQuery({
    queryKey: ["keys"],
    queryFn: () => axios.get(endpoints.keys.keys),
    select: (d) => d.data?.data,
  });

  return (
    <>
      <Box
        sx={{
          p: 2,
        }}
      >
        <Typography color={"text.primary"} variant="h6">
          Keys
        </Typography>
        {isPending && <LinearProgress />}
        {isSuccess && (
          <Stack
            sx={{
              width: 300,
            }}
          >
            <TextField
              variant="outlined"
              margin="normal"
              type="password"
              disabled
              value="random"
              label="API Key"
              InputProps={{
                endAdornment: (
                  <InputAdornment position="end">
                    <Tooltip title="Copied!" open={showTooltip.apiKey} arrow>
                      <IconButton
                        onClick={() => handleCopy(keysData.apiKey, "apiKey")}
                      >
                        <Iconify icon="solar:copy-linear" />
                      </IconButton>
                    </Tooltip>
                  </InputAdornment>
                ),
              }}
            />
            <TextField
              label="Secret Key"
              variant="outlined"
              margin="normal"
              type="password"
              disabled
              value="random"
              InputProps={{
                endAdornment: (
                  <InputAdornment position="end">
                    <Tooltip title="Copied!" open={showTooltip.secretKey} arrow>
                      <IconButton
                        onClick={() =>
                          handleCopy(keysData.secretKey, "secretKey")
                        }
                      >
                        <Iconify icon="solar:copy-linear" />
                      </IconButton>
                    </Tooltip>
                  </InputAdornment>
                ),
              }}
            />
          </Stack>
        )}
      </Box>
    </>
  );
}
