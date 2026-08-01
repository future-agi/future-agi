import { useState } from "react";
import PropTypes from "prop-types";
import { Autocomplete, TextField, CircularProgress } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { useParams } from "react-router-dom";

const selectSpanAttributeValueSuggestions = (response) => {
  const payload = response?.data || {};
  return {
    options: Array.isArray(payload.result)
      ? payload.result.map((item) => item.value)
      : [],
    queryComplete: payload.query_complete !== false,
  };
};

const AutocompleteTextValueSelector = ({
  definition,
  filter,
  updateFilter,
}) => {
  const [inputValue, setInputValue] = useState(
    filter?.filter_config?.filter_value || "",
  );
  const debouncedInput = useDebounce(inputValue, 300);
  const { id: projectId } = useParams();

  const { data: suggestions, isLoading } = useQuery({
    queryKey: [
      "span-attribute-values",
      projectId,
      definition?.propertyId,
      debouncedInput,
    ],
    queryFn: () =>
      axios.get(endpoints.project.spanAttributeValues(), {
        params: {
          project_id: projectId,
          key: definition?.propertyId,
          q: debouncedInput,
          limit: 20,
        },
      }),
    select: selectSpanAttributeValueSuggestions,
    enabled: Boolean(projectId) && Boolean(definition?.propertyId),
    staleTime: 30000,
  });
  const options = suggestions?.options || [];
  const suggestionsIncomplete = suggestions?.queryComplete === false;

  return (
    <Autocomplete
      freeSolo
      size="small"
      options={options}
      loading={isLoading}
      noOptionsText={
        suggestionsIncomplete ? "Type the value manually" : "No matching values"
      }
      inputValue={inputValue}
      onInputChange={(_, newInputValue) => {
        setInputValue(newInputValue);
      }}
      value={filter?.filter_config?.filter_value || ""}
      onChange={(_, newValue) => {
        updateFilter({
          ...filter,
          filter_config: {
            ...filter?.filter_config,
            filter_value: newValue || "",
          },
        });
      }}
      onBlur={() => {
        if (inputValue !== filter?.filter_config?.filter_value) {
          updateFilter({
            ...filter,
            filter_config: {
              ...filter?.filter_config,
              filter_value: inputValue,
            },
          });
        }
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          placeholder="Type or select a value..."
          variant="outlined"
          size="small"
          helperText={
            suggestionsIncomplete
              ? "Value suggestions are incomplete. Type a value to continue."
              : undefined
          }
          FormHelperTextProps={{
            sx: suggestionsIncomplete ? { color: "warning.main" } : undefined,
          }}
          sx={{ minWidth: 180 }}
          InputProps={{
            ...params.InputProps,
            endAdornment: (
              <>
                {isLoading ? (
                  <CircularProgress color="inherit" size={16} />
                ) : null}
                {params.InputProps.endAdornment}
              </>
            ),
          }}
        />
      )}
      sx={{ minWidth: 200 }}
    />
  );
};

AutocompleteTextValueSelector.propTypes = {
  definition: PropTypes.shape({
    propertyId: PropTypes.string,
  }),
  filter: PropTypes.shape({
    filter_config: PropTypes.shape({
      filter_value: PropTypes.string,
    }),
  }),
  updateFilter: PropTypes.func.isRequired,
};

export default AutocompleteTextValueSelector;
