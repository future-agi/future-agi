import { Box, Button, InputAdornment, useTheme } from "@mui/material";
import React from "react";
import SvgColor from "src/components/svg-color";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { useParams } from "react-router";
import { useMutation } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";

const CallLogsHeader = ({ searchText, setSearchText }) => {
  const { testId } = useParams();

  const { mutate: exportLogs } = useMutation({
    mutationFn: () =>
      axios.get(endpoints.runTests.callExecutionsExport(testId)),
    onSuccess: (response) => {
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `call-logs-${testId}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      enqueueSnackbar("Call Logs downloaded successfully", {
        variant: "success",
      });
    },
  });

  const theme = useTheme();
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <FormSearchField
        size="small"
        placeholder="Search Logs..."
        searchQuery={searchText}
        onChange={(e) => {
          setSearchText(e.target.value);
        }}
        sx={{
          width: "279px",
          "& .MuiInputBase-input": {
            paddingY: `${theme.spacing(0.5)}`,
            paddingRight: `${theme.spacing(0.5)}`,
          },
        }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <SvgColor
                src={`/assets/icons/custom/search.svg`}
                sx={{ width: "20px", height: "20px", color: "text.disabled" }}
              />
            </InputAdornment>
          ),
          endAdornment: searchText && (
            <InputAdornment position="end">
              <Iconify
                icon="solar:close-linear"
                onClick={() => setSearchText("")}
                sx={{ color: "text.disabled", cursor: "pointer" }}
              />
            </InputAdornment>
          ),
        }}
        inputProps={{
          sx: {
            padding: 0,
          },
        }}
      />
      <Box sx={{ display: "flex", gap: 2 }}>
        <Button
          variant="outlined"
          size="small"
          onClick={() => exportLogs()}
          startIcon={
            <SvgColor
              width={16}
              src="/assets/icons/action_buttons/ic_download.svg"
              sx={{
                color: "text.primary",
              }}
            />
          }
        >
          Export Logs
        </Button>
      </Box>
    </Box>
  );
};

export default CallLogsHeader;

CallLogsHeader.propTypes = {
  searchText: PropTypes.string,
  setSearchText: PropTypes.func,
};
