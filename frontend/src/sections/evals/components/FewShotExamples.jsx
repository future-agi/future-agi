import { Autocomplete, Box, Chip, TextField, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";

/**
 * Few-shot dataset selector for LLM-As-A-Judge evaluations.
 *
 * Users select one or more datasets. At eval runtime, rows from these
 * datasets are injected as few-shot examples into the judge prompt.
 */
const FewShotExamples = ({
  selectedDatasets = [],
  onChange,
  disabled = false,
}) => {
  const { data: datasets = [], isLoading } = useQuery({
    queryKey: ["datasets-for-fewshot"],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.develop.getDatasets());
      return data?.result?.datasets || [];
    },
  });

  return (
    <Box>
      <Box sx={{ mb: 1 }}>
        <Typography variant="body2" fontWeight={600}>
          Few-shot Examples
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Select datasets to guide the LLM judge with example evaluations
        </Typography>
      </Box>

      <Autocomplete
        multiple
        size="small"
        options={datasets}
        loading={isLoading}
        getOptionLabel={(opt) => opt.name || opt.id || ""}
        isOptionEqualToValue={(opt, val) => opt.id === val.id}
        value={
          // Resolve stored IDs to full dataset objects
          selectedDatasets
            .map((ds) =>
              typeof ds === "string" ? datasets.find((d) => d.id === ds) : ds,
            )
            .filter(Boolean)
        }
        onChange={(_, newVal) => onChange(newVal)}
        disabled={disabled}
        sx={
          disabled
            ? {
                cursor: "not-allowed",
                "& .MuiInputBase-root": { cursor: "not-allowed" },
              }
            : undefined
        }
        renderInput={(params) => (
          <TextField
            {...params}
            placeholder={
              selectedDatasets.length === 0 ? "Search datasets..." : ""
            }
            sx={{
              "& .MuiInputBase-root": {
                fontSize: "13px",
                ...(disabled && { cursor: "not-allowed" }),
              },
            }}
          />
        )}
        renderTags={(value, getTagProps) =>
          value.map((opt, index) => (
            <Chip
              {...getTagProps({ index })}
              key={opt.id}
              label={opt.name}
              size="small"
              icon={<Iconify icon="mdi:database-outline" width={14} />}
              sx={{ fontSize: "12px", height: 24 }}
            />
          ))
        }
        renderOption={(props, option) => (
          <li {...props} key={option.id}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                width: "100%",
              }}
            >
              <Iconify
                icon="mdi:database-outline"
                width={16}
                sx={{ color: "text.secondary", flexShrink: 0 }}
              />
              <Typography
                variant="body2"
                sx={{ fontSize: "13px", flex: 1 }}
                noWrap
              >
                {option.name}
              </Typography>
              {option.row_count != null && (
                <Typography
                  variant="caption"
                  color="text.disabled"
                  sx={{ flexShrink: 0 }}
                >
                  {option.row_count} rows
                </Typography>
              )}
            </Box>
          </li>
        )}
        noOptionsText="No datasets found"
      />

      {selectedDatasets.length > 0 && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ mt: 0.5, display: "block" }}
        >
          Rows from selected datasets will be used as few-shot examples at
          evaluation time
        </Typography>
      )}
    </Box>
  );
};

FewShotExamples.propTypes = {
  selectedDatasets: PropTypes.array,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

export default FewShotExamples;
