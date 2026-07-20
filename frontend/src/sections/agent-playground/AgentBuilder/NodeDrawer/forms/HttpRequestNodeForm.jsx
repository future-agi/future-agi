import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Controller, useFormContext } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

/**
 * HttpRequestNodeForm
 *
 * Lets users configure:
 *   - HTTP method (GET / POST / PUT / PATCH / DELETE)
 *   - URL (supports {{variable}} template syntax)
 *   - Request headers (key-value, one per line as "Key: Value")
 *   - Request body (raw JSON, shown only for POST/PUT/PATCH)
 *   - Output variable name (where the response JSON is stored)
 */
export default function HttpRequestNodeForm({ nodeId: _nodeId }) {
  const { control, watch } = useFormContext();
  const method = watch("method");
  const hasBody = ["POST", "PUT", "PATCH"].includes(method);

  return (
    <Stack spacing={2} sx={{ p: 2 }}>
      {/* Method + URL */}
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Controller
          name="method"
          control={control}
          defaultValue="GET"
          render={({ field }) => (
            <TextField
              {...field}
              select
              size="small"
              label="Method"
              sx={{ width: 110, flexShrink: 0 }}
            >
              {HTTP_METHODS.map((m) => (
                <MenuItem key={m} value={m}>
                  {m}
                </MenuItem>
              ))}
            </TextField>
          )}
        />
        <FormTextFieldV2
          name="url"
          control={control}
          label="URL"
          placeholder="https://api.example.com/endpoint"
          size="small"
          fullWidth
          rules={{ required: "URL is required" }}
        />
      </Stack>

      {/* Headers */}
      <Box>
        <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: "block" }}>
          Headers (one per line: <code>Key: Value</code>)
        </Typography>
        <FormTextFieldV2
          name="headers"
          control={control}
          placeholder={"Content-Type: application/json\nAuthorization: Bearer {{token}}"}
          multiline
          minRows={3}
          size="small"
          fullWidth
        />
      </Box>

      {/* Body — only for POST/PUT/PATCH */}
      {hasBody && (
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: "block" }}>
            Body (JSON — use <code>{"{{variable}}"}</code> for dynamic values)
          </Typography>
          <FormTextFieldV2
            name="body"
            control={control}
            placeholder={'{\n  "key": "{{variable}}"\n}'}
            multiline
            minRows={5}
            size="small"
            fullWidth
            sx={{ fontFamily: "monospace", fontSize: 12 }}
          />
        </Box>
      )}

      {/* Output variable name */}
      <Box>
        <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: "block" }}>
          Output variable name
        </Typography>
        <FormTextFieldV2
          name="outputKey"
          control={control}
          placeholder="http_response"
          size="small"
          fullWidth
          rules={{
            pattern: {
              value: /^[a-z0-9_]+$/,
              message: "Lowercase letters, numbers, and underscores only",
            },
          }}
        />
        <Typography variant="caption" color="text.disabled" sx={{ mt: 0.5, display: "block" }}>
          The response JSON will be available as this variable in downstream nodes.
        </Typography>
      </Box>
    </Stack>
  );
}

HttpRequestNodeForm.propTypes = {
  nodeId: PropTypes.string.isRequired,
};
