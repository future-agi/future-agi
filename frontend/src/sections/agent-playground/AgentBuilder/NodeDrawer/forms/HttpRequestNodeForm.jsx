import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Divider,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import { useFormContext, useWatch } from "react-hook-form";
import { enqueueSnackbar } from "notistack";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import {
  useAgentPlaygroundStore,
  useAgentPlaygroundStoreShallow,
} from "../../../store";
import usePartialNodeUpdate from "../../hooks/usePartialNodeUpdate";
import { useSaveDraftContext } from "../../saveDraftContext";
import VariableAccessInfo from "../../../components/VariableAccessInfo";

const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];
const AUTH_TYPES = [
  { value: "none", label: "None" },
  { value: "bearer", label: "Bearer Token" },
  { value: "basic", label: "Basic Auth" },
];

function headersToRows(headers) {
  return Object.entries(headers || {}).map(([key, value], index) => ({
    id: `${key}-${index}`,
    key,
    value,
  }));
}

function rowsToHeaders(rows) {
  const headers = {};
  rows.forEach((row) => {
    if (row.key.trim()) {
      headers[row.key.trim()] = row.value;
    }
  });
  return headers;
}

function buildHttpConfig(data) {
  let body = data.body;
  if (typeof body === "string" && body.trim()) {
    try {
      body = JSON.parse(body);
    } catch {
      body = data.body;
    }
  } else if (!body || (typeof body === "string" && !body.trim())) {
    body = null;
  }

  const auth = { type: data.authType || "none" };
  if (data.authType === "bearer") {
    auth.token = data.authToken || "";
  } else if (data.authType === "basic") {
    auth.username = data.authUsername || "";
    auth.password = data.authPassword || "";
  }

  return {
    method: data.method,
    url: data.url,
    headers: data.headers || {},
    body,
    auth,
    timeout: data.timeout ?? 30,
    retries: data.retries ?? 0,
  };
}

export default function HttpRequestNodeForm({ nodeId }) {
  const { handleSubmit, control, setValue } = useFormContext();
  const authType = useWatch({ name: "authType" });
  const method = useWatch({ name: "method" });

  const [headerRows, setHeaderRows] = useState(() => {
    const node = useAgentPlaygroundStore.getState().getNodeById(nodeId);
    return headersToRows(node?.data?.config?.headers);
  });

  const { updateNodeData, clearSelectedNode, clearValidationErrorNode } =
    useAgentPlaygroundStoreShallow((state) => ({
      updateNodeData: state.updateNodeData,
      clearSelectedNode: state.clearSelectedNode,
      clearValidationErrorNode: state.clearValidationErrorNode,
    }));
  const { partialUpdate, isPending } = usePartialNodeUpdate();
  const { ensureDraft } = useSaveDraftContext();

  const showBody = useMemo(() => method && method !== "GET", [method]);

  const handleHeaderRowChange = (id, field, value) => {
    setHeaderRows((rows) =>
      rows.map((row) => (row.id === id ? { ...row, [field]: value } : row)),
    );
  };

  const handleAddHeaderRow = () => {
    setHeaderRows((rows) => [
      ...rows,
      { id: `header-${Date.now()}`, key: "", value: "" },
    ]);
  };

  const handleRemoveHeaderRow = (id) => {
    setHeaderRows((rows) => rows.filter((row) => row.id !== id));
  };

  const onSave = async (data) => {
    const storeState = useAgentPlaygroundStore.getState();
    const otherNodes = storeState.nodes.filter((n) => n.id !== nodeId);
    if (otherNodes.some((n) => n.data?.label === data.name)) {
      enqueueSnackbar("A node with this name already exists", {
        variant: "error",
      });
      return;
    }

    const config = buildHttpConfig({
      ...data,
      headers: rowsToHeaders(headerRows),
    });

    const draftResult = await ensureDraft();
    if (draftResult === false) return;

    clearValidationErrorNode(nodeId);
    updateNodeData(nodeId, { label: data.name, config });

    try {
      await partialUpdate(nodeId, {
        label: data.name,
        config,
      });
      enqueueSnackbar("HTTP request node saved", { variant: "success" });
      clearSelectedNode();
    } catch (error) {
      enqueueSnackbar(error?.message || "Failed to save node", {
        variant: "error",
      });
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1.25,
        height: "100%",
      }}
    >
      <FormTextFieldV2
        fullWidth
        size="small"
        control={control}
        fieldName="name"
        label="Node Name"
        required
      />

      <Stack direction="row" spacing={1}>
        <Select
          size="small"
          value={method || "GET"}
          onChange={(e) =>
            setValue("method", e.target.value, { shouldDirty: true })
          }
          sx={{ width: 130 }}
        >
          {HTTP_METHODS.map((m) => (
            <MenuItem key={m} value={m}>
              {m}
            </MenuItem>
          ))}
        </Select>
        <FormTextFieldV2
          fullWidth
          size="small"
          control={control}
          fieldName="url"
          label="URL"
          placeholder="https://api.example.com/users/{{user_id}}"
          required
        />
      </Stack>

      <Divider />

      <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        <Stack spacing={1.5}>
          <VariableAccessInfo />

          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
          >
            <Typography typography="s1_2" fontWeight="fontWeightMedium">
              Headers
            </Typography>
            <IconButton size="small" onClick={handleAddHeaderRow}>
              <Iconify icon="solar:add-circle-outline" width={20} />
            </IconButton>
          </Stack>

          {headerRows.map((row) => (
            <Stack
              key={row.id}
              direction="row"
              spacing={1}
              alignItems="center"
            >
              <TextField
                size="small"
                placeholder="Key"
                value={row.key}
                onChange={(e) =>
                  handleHeaderRowChange(row.id, "key", e.target.value)
                }
                sx={{ flex: 1 }}
              />
              <TextField
                size="small"
                placeholder="Value or {{variable}}"
                value={row.value}
                onChange={(e) =>
                  handleHeaderRowChange(row.id, "value", e.target.value)
                }
                sx={{ flex: 2 }}
              />
              <IconButton
                size="small"
                onClick={() => handleRemoveHeaderRow(row.id)}
              >
                <Iconify icon="solar:trash-bin-trash-outline" width={18} />
              </IconButton>
            </Stack>
          ))}

          {showBody && (
            <FormTextFieldV2
              fullWidth
              size="small"
              control={control}
              fieldName="body"
              label="Body (JSON or text)"
              placeholder='{"name": "{{name}}"}'
              multiline
              minRows={4}
            />
          )}

          <Divider />

          <Typography typography="s1_2" fontWeight="fontWeightMedium">
            Authentication
          </Typography>
          <Select
            size="small"
            value={authType || "none"}
            onChange={(e) =>
              setValue("authType", e.target.value, { shouldDirty: true })
            }
          >
            {AUTH_TYPES.map((option) => (
              <MenuItem key={option.value} value={option.value}>
                {option.label}
              </MenuItem>
            ))}
          </Select>

          {authType === "bearer" && (
            <FormTextFieldV2
              fullWidth
              size="small"
              control={control}
              fieldName="authToken"
              label="Bearer Token"
              placeholder="Token or {{variable}}"
            />
          )}

          {authType === "basic" && (
            <>
              <FormTextFieldV2
                fullWidth
                size="small"
                control={control}
                fieldName="authUsername"
                label="Username"
              />
              <FormTextFieldV2
                fullWidth
                size="small"
                control={control}
                fieldName="authPassword"
                label="Password"
                fieldType="password"
              />
            </>
          )}

          <Divider />

          <Stack direction="row" spacing={1}>
            <FormTextFieldV2
              fullWidth
              size="small"
              control={control}
              fieldName="timeout"
              label="Timeout (seconds)"
              fieldType="number"
            />
            <FormTextFieldV2
              fullWidth
              size="small"
              control={control}
              fieldName="retries"
              label="Retries"
              fieldType="number"
            />
          </Stack>
        </Stack>
      </Box>

      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          pt: 1.5,
          borderTop: 1,
          borderColor: "divider",
        }}
      >
        <LoadingButton
          type="submit"
          variant="outlined"
          size="small"
          loading={isPending}
          onClick={handleSubmit(onSave)}
        >
          Save
        </LoadingButton>
      </Box>
    </Box>
  );
}

HttpRequestNodeForm.propTypes = {
  nodeId: PropTypes.string.isRequired,
};
