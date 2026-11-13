import { Box, InputAdornment, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import CallDetailLogGrid from "./CallDetailLogGrid";
import { useCallLogsSearchStore } from "./states";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { CategoryOptions, LevelOptions } from "./common";

const CallDetailLogs = ({ callLogId, vapiId, module, callLogs }) => {
  const theme = useTheme();
  const apiRef = useRef();

  const {
    search,
    setSearch,
    level,
    setLevel,
    category,
    setCategory,
    totalCount,
  } = useCallLogsSearchStore();

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        maxHeight: "500px",
      }}
    >
      <FormSearchField
        size="small"
        placeholder="Search"
        searchQuery={search}
        onChange={(e) => {
          setSearch(e.target.value);
          apiRef.current?.api?.onFilterChanged();
        }}
        sx={{
          width: "100%",
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
          endAdornment: search && (
            <InputAdornment position="end">
              <Iconify
                icon="solar:close-linear"
                onClick={() => {
                  setSearch("");
                }}
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
      <Box
        sx={{
          display: "flex",
          gap: 1,
          alignItems: "flex-end",
          justifyContent: "space-between",
        }}
      >
        <Box sx={{ display: "flex", gap: 1 }}>
          <FormSearchSelectFieldState
            onChange={(e) => {
              setLevel(e.target.value);
              apiRef.current?.api?.onFilterChanged();
            }}
            value={level}
            size="small"
            placeholder="Level"
            options={LevelOptions}
            sx={{
              width: "160px",
            }}
          />
          <FormSearchSelectFieldState
            onChange={(e) => {
              setCategory(e.target.value);
              apiRef.current?.api?.onFilterChanged();
            }}
            value={category}
            size="small"
            placeholder="Category"
            options={CategoryOptions}
            sx={{
              width: "160px",
            }}
          />
        </Box>

        <Typography typography="s2_1"> Showing {totalCount} logs</Typography>
      </Box>
      <CallDetailLogGrid
        callLogId={callLogId}
        vapiId={vapiId}
        ref={apiRef}
        module={module}
        callLogs={callLogs}
      />
    </Box>
  );
};

CallDetailLogs.propTypes = {
  module: PropTypes.string,
  callLogId: PropTypes.string,
  vapiId: PropTypes.string,
  callLogs: PropTypes.array,
};

export default CallDetailLogs;
