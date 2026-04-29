import {
  Box,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
} from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";

const BreakdownCard = ({
  index,
  setBreakdown,
  breakdown,
  removeBreakdown,
  cloneBreakdown,
}) => {
  const { id } = useParams();

  const { data: allOptions } = useQuery({
    queryFn: () => axios.get(endpoints.performance.getFilterOptions(id), {}),
    queryKey: ["performance-filter-options", id, ""],
    staleTime: 30 * 60 * 1000, // 30 min stale time
    select: (d) => d.data?.result,
  });

  return (
    <Paper
      elevation={2}
      sx={{ padding: "14px", display: "flex", flexDirection: "column", gap: 1 }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center" }}>
          <IconButton size="small" onClick={() => cloneBreakdown(index)}>
            <Iconify
              icon="bxs:duplicate"
              width={16}
              height={16}
              sx={{ color: "text.secondary" }}
            />
          </IconButton>
          <IconButton size="small" onClick={() => removeBreakdown(index)}>
            <Iconify
              icon="solar:trash-bin-trash-bold"
              width={16}
              height={16}
              sx={{ color: "text.secondary" }}
            />
          </IconButton>
        </Box>
      </Box>
      <FormControl size="small">
        <InputLabel>Breakdown</InputLabel>
        <Select
          label="Breakdown"
          value={breakdown?.keyId || ""}
          onChange={(e) => {
            const keyId = e.target.value;
            setBreakdown(index, (b) => ({
              ...b,
              key: allOptions?.properties.find((p) => p.id === keyId)?.name,
              keyId,
            }));
          }}
          MenuProps={{
            PaperProps: {
              style: {
                maxHeight: "200px",
              },
            },
          }}
        >
          {allOptions?.properties.map(({ name, id }) => (
            <MenuItem value={id} key={id}>
              {name}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
    </Paper>
  );
};

BreakdownCard.propTypes = {
  index: PropTypes.number,
  setBreakdown: PropTypes.func,
  breakdown: PropTypes.object,
  removeBreakdown: PropTypes.func,
  cloneBreakdown: PropTypes.func,
};

export default BreakdownCard;
