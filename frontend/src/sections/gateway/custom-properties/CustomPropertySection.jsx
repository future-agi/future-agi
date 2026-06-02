import React, { useState, useMemo } from "react";
import {
  Box,
  Typography,
  Button,
  Stack,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Skeleton,
  Card,
  InputAdornment,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  CardContent,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import SectionHeader from "../components/SectionHeader";
import PageErrorState from "../components/PageErrorState";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import { useGatewayContext } from "../context/useGatewayContext";

import {
  useCustomProperties,
  useCreateCustomProperty,
  useUpdateCustomProperty,
  useDeleteCustomProperty,
} from "./hooks/useCustomProperties";
import CreateEditPropertyDialog from "./CreateEditPropertyDialog";

const TYPE_ICONS = {
  string: "mdi:format-text",
  number: "mdi:numeric",
  boolean: "mdi:toggle-switch-outline",
  enum: "mdi:format-list-bulleted-type",
};

const CustomPropertySection = () => {
  const [searchQuery, setSearchQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editProperty, setEditProperty] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);

  const { gatewayId: _gatewayId } = useGatewayContext();
  const { data: properties, isLoading, error, refetch } = useCustomProperties();
  const createMutation = useCreateCustomProperty();
  const updateMutation = useUpdateCustomProperty();
  const deleteMutation = useDeleteCustomProperty();

  const filteredProperties = useMemo(() => {
    if (!properties) return [];
    if (!searchQuery.trim()) return properties;
    const q = searchQuery.toLowerCase();
    return properties.filter(
      (p) =>
        p.name?.toLowerCase().includes(q) ||
        p.description?.toLowerCase().includes(q),
    );
  }, [properties, searchQuery]);

  const handleCreate = (payload) => {
    createMutation.mutate(payload, {
      onSuccess: () => {
        enqueueSnackbar("Property schema created", { variant: "success" });
        setCreateOpen(false);
      },
      onError: (err) => {
        enqueueSnackbar(
          err?.response?.data?.message || "Failed to create property",
          { variant: "error" },
        );
      },
    });
  };

  const handleUpdate = (payload) => {
    updateMutation.mutate(payload, {
      onSuccess: () => {
        enqueueSnackbar("Property schema updated", { variant: "success" });
        setEditProperty(null);
      },
      onError: (err) => {
        enqueueSnackbar(
          err?.response?.data?.message || "Failed to update property",
          { variant: "error" },
        );
      },
    });
  };

  const handleDelete = () => {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () => {
        enqueueSnackbar("Property schema deleted", { variant: "success" });
        setDeleteTarget(null);
      },
      onError: () =>
        enqueueSnackbar("Failed to delete property", { variant: "error" }),
    });
  };

  if (isLoading) {
    return (
      <Box p={3}>
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Skeleton width={250} height={40} />
          <Skeleton width={140} height={36} variant="rounded" />
        </Stack>
        <Card>
          {[...Array(4)].map((_, i) => (
            <Stack
              key={i}
              direction="row"
              spacing={2}
              sx={{
                px: 2,
                py: 1.5,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Skeleton width="30%" height={20} />
              <Skeleton width="15%" height={20} />
              <Skeleton width="15%" height={20} />
              <Skeleton width="25%" height={20} />
            </Stack>
          ))}
        </Card>
      </Box>
    );
  }

  if (error) {
    return (
      <Box p={3}>
        <PageErrorState
          message={`Failed to load custom properties: ${error.message}`}
          onRetry={refetch}
        />
      </Box>
    );
  }

  return (
    <Box p={3}>
      <SectionHeader
        icon={GATEWAY_ICONS.customProperties}
        title="Custom Properties"
        subtitle="Define custom metadata schemas for your request logs"
        actions={[
          {
            label: "Add Property",
            variant: "contained",
            size: "small",
            icon: "mdi:plus",
            onClick: () => setCreateOpen(true),
          },
        ]}
      />

      <TextField
        placeholder="Search properties..."
        size="small"
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        sx={{ width: 300, mb: 3 }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <Iconify icon="eva:search-outline" width={18} />
            </InputAdornment>
          ),
        }}
      />

      {filteredProperties.length === 0 ? (
        <Box display="flex" flexDirection="column" alignItems="center" py={8}>
          <Iconify
            icon="mdi:form-textbox"
            width={48}
            sx={{ color: "text.disabled", mb: 2 }}
          />
          <Typography variant="h6" color="text.secondary" mb={1}>
            {properties?.length === 0
              ? "No custom properties defined"
              : "No properties match your search"}
          </Typography>
          {properties?.length === 0 && (
            <>
              <Typography
                variant="body2"
                color="text.secondary"
                mb={2}
                textAlign="center"
              >
                Custom properties let you attach structured metadata to request
                logs.
              </Typography>
              <Button
                variant="outlined"
                startIcon={<Iconify icon="mdi:plus" width={20} />}
                onClick={() => setCreateOpen(true)}
              >
                Define Your First Property
              </Button>
            </>
          )}
        </Box>
      ) : (
        <>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Type</TableCell>
                  <TableCell>Required</TableCell>
                  <TableCell>Allowed Values</TableCell>
                  <TableCell>Default</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filteredProperties.map((prop) => (
                  <TableRow key={prop.id} hover>
                    <TableCell>
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Iconify
                          icon={
                            TYPE_ICONS[prop.property_type] ||
                            "mdi:help-circle-outline"
                          }
                          width={18}
                          sx={{ color: "text.secondary" }}
                        />
                        <Box>
                          <Typography variant="body2" fontWeight={500}>
                            {prop.name}
                          </Typography>
                          {prop.description && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              {prop.description}
                            </Typography>
                          )}
                        </Box>
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={prop.property_type}
                        size="small"
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={prop.required ? "Required" : "Optional"}
                        color={prop.required ? "warning" : "default"}
                        size="small"
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell>
                      {prop.allowed_values?.length > 0 ? (
                        <Stack
                          direction="row"
                          spacing={0.5}
                          flexWrap="wrap"
                          useFlexGap
                        >
                          {prop.allowed_values.slice(0, 3).map((v) => (
                            <Chip
                              key={v}
                              label={v}
                              size="small"
                              sx={{ fontSize: "0.7rem" }}
                            />
                          ))}
                          {prop.allowed_values.length > 3 && (
                            <Chip
                              label={`+${prop.allowed_values.length - 3}`}
                              size="small"
                              variant="outlined"
                            />
                          )}
                        </Stack>
                      ) : (
                        <Typography variant="body2" color="text.secondary">
                          {"\u2014"}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ fontFamily: "monospace" }}
                      >
                        {prop.default_value != null
                          ? String(prop.default_value)
                          : "\u2014"}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Stack
                        direction="row"
                        spacing={0.5}
                        justifyContent="flex-end"
                      >
                        <IconButton
                          size="small"
                          title="Edit"
                          onClick={() => setEditProperty(prop)}
                        >
                          <Iconify icon="mdi:pencil-outline" width={20} />
                        </IconButton>
                        <IconButton
                          size="small"
                          title="Delete"
                          color="error"
                          onClick={() => setDeleteTarget(prop)}
                        >
                          <Iconify icon="mdi:delete-outline" width={20} />
                        </IconButton>
                      </Stack>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <Typography variant="body2" color="text.secondary" mt={2}>
            Showing {filteredProperties.length} propert
            {filteredProperties.length !== 1 ? "ies" : "y"}
          </Typography>
        </>
      )}

      {/* Preview card */}
      {filteredProperties.length > 0 && (
        <Card sx={{ mt: 3 }}>
          <CardContent>
            <Typography variant="subtitle2" mb={1}>
              Log Preview
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              display="block"
              mb={1.5}
            >
              This is how custom properties would appear in a request log entry:
            </Typography>
            <Box
              sx={{
                bgcolor: "action.hover",
                borderRadius: 1,
                p: 2,
                fontFamily: "monospace",
                fontSize: "0.8rem",
              }}
            >
              <pre style={{ margin: 0 }}>
                {JSON.stringify(
                  Object.fromEntries(
                    filteredProperties.map((p) => [
                      p.name,
                      p.default_value != null
                        ? p.default_value
                        : `<${p.property_type}>`,
                    ]),
                  ),
                  null,
                  2,
                )}
              </pre>
            </Box>
          </CardContent>
        </Card>
      )}

      <CreateEditPropertyDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSubmit={handleCreate}
        isPending={createMutation.isPending}
      />

      <CreateEditPropertyDialog
        open={Boolean(editProperty)}
        onClose={() => setEditProperty(null)}
        onSubmit={handleUpdate}
        property={editProperty}
        isPending={updateMutation.isPending}
      />

      <Dialog
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        maxWidth="xs"
      >
        <DialogTitle>Delete Property Schema</DialogTitle>
        <DialogContent>
          <Typography>
            Delete <strong>{deleteTarget?.name}</strong>? Existing logs with
            this property will not be affected.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>Cancel</Button>
          <Button
            variant="contained"
            color="error"
            onClick={handleDelete}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? "Deleting..." : "Delete"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default CustomPropertySection;
