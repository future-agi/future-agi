import PropTypes from "prop-types";
import { useState } from "react";
import {
  Autocomplete,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useDashboardFilterValues } from "src/hooks/useDashboards";
import { OPERATOR_LABELS, reducedType, sourceCapability } from "./builderSpec";

export default function ConditionRow({
  input,
  condition,
  capabilities,
  projectId,
  disabled,
  onChange,
}) {
  const [search, setSearch] = useState("");
  const type =
    input.condition_stage === "observation"
      ? input.value_type
      : reducedType(input);
  const discoverable =
    type === "text" &&
    input.source === "span" &&
    input.field === "attribute_text";
  const values = useDashboardFilterValues({
    metricName: input.attribute_key,
    metricType: "custom_attribute",
    projectIds: projectId ? [projectId] : [],
    source: "traces",
    attributeType: "string",
    search,
    enabled: discoverable && Boolean(projectId) && !disabled,
  });
  const options = (values.data || []).flatMap((option) => {
    const value = typeof option === "string" ? option : option.value;
    return typeof value === "string" ? [value] : [];
  });
  const label = `Value for ${input.attribute_key || input.alias}`;
  return (
    <Stack gap={1}>
      <Stack direction="row" gap={1.5} alignItems="flex-start">
        <TextField
          select
          size="small"
          label="Comparison"
          value={condition.operator}
          disabled={disabled}
          sx={{ width: 160, flexShrink: 0 }}
          onChange={(event) =>
            onChange({ ...condition, operator: event.target.value })
          }
        >
          {(sourceCapability(capabilities, type)?.operators || []).map(
            (operator) => (
              <MenuItem key={operator} value={operator}>
                {OPERATOR_LABELS[operator]}
              </MenuItem>
            ),
          )}
        </TextField>
        {condition.right_alias ? (
          <Typography
            variant="body2"
            sx={{ alignSelf: "flex-end", lineHeight: "32px" }}
          >
            Compared with {condition.right_alias} (saved source)
          </Typography>
        ) : type === "boolean" ? (
          <TextField
            select
            fullWidth
            size="small"
            label={label}
            value={
              typeof condition.value === "boolean"
                ? String(condition.value)
                : ""
            }
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...condition, value: event.target.value === "true" })
            }
          >
            <MenuItem value="true">True</MenuItem>
            <MenuItem value="false">False</MenuItem>
          </TextField>
        ) : type === "text" ? (
          <Autocomplete
            freeSolo
            fullWidth
            size="small"
            disabled={disabled}
            options={[...new Set(options)]}
            value={condition.value ?? null}
            inputValue={condition.value ?? ""}
            loading={values.isFetching}
            filterOptions={(available) => available}
            onInputChange={(_, text, reason) => {
              if (["input", "clear"].includes(reason)) {
                setSearch(text);
                onChange({ ...condition, value: text });
              }
            }}
            onChange={(_, value) => onChange({ ...condition, value })}
            renderInput={(params) => (
              <TextField
                {...params}
                label={label}
                helperText="Choose an observed value or enter exact text."
              />
            )}
          />
        ) : (
          <TextField
            fullWidth
            size="small"
            type="number"
            label={label}
            value={condition.value ?? ""}
            disabled={disabled}
            onChange={(event) =>
              onChange({
                ...condition,
                value:
                  event.target.value === "" ? null : Number(event.target.value),
              })
            }
          />
        )}
      </Stack>
      {discoverable && values.isError && (
        <Typography variant="caption" color="text.secondary">
          Value suggestions unavailable.{" "}
          <Button size="small" onClick={() => values.refetch()}>
            Retry values
          </Button>
        </Typography>
      )}
      {discoverable && values.hasNextPage && (
        <Button
          size="small"
          disabled={values.isFetching}
          onClick={() => values.fetchNextPage()}
        >
          More observed values
        </Button>
      )}
    </Stack>
  );
}
ConditionRow.propTypes = {
  input: PropTypes.object.isRequired,
  condition: PropTypes.object.isRequired,
  capabilities: PropTypes.object,
  projectId: PropTypes.string,
  disabled: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
};
