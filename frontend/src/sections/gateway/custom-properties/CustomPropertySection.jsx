import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import SvgColor from "src/components/svg-color";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayPageFrame,
  GatewayStatusChip,
} from "../components/frame";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { CopyButton, SettingsPanel } from "../settings/ui";
import { formatDate } from "../utils/formatters";
import CreateEditPropertyDialog from "./CreateEditPropertyDialog";
import {
  hasStoredDefault,
  RULES_CHECKED_BY_VALIDATE_API,
} from "./customPropertyForm";
import {
  useCreateCustomProperty,
  useCustomProperties,
  useDeleteCustomProperty,
  useUpdateCustomProperty,
} from "./hooks/useCustomProperties";
import { reservedReason, sendSnippet } from "./reservedNames";
import { GATEWAY_MONO } from "../utils/fonts";

const ROW_HEIGHT = 52;

const TYPE_LABEL = {
  string: "Text",
  number: "Number",
  boolean: "Boolean",
  enum: "One of a list",
};

// Required and Allowed values: checked by the Validate API.
const hasRules = (p) =>
  Boolean(p.required) || (p.allowed_values || []).length > 0;

const Cell = ({ children }) => (
  <Box sx={{ display: "flex", alignItems: "center", height: 1, minWidth: 0 }}>
    {children}
  </Box>
);
Cell.propTypes = { children: PropTypes.node };

function NameCell({ data }) {
  const reserved = reservedReason(data.name);
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ height: 1, minWidth: 0 }}
    >
      <Box sx={{ lineHeight: 1.2, minWidth: 0 }}>
        <Typography typography="s1" noWrap sx={{ fontFamily: GATEWAY_MONO }}>
          {data.name}
        </Typography>
        {data.description && (
          <Typography
            typography="s3"
            color="text.secondary"
            noWrap
            title={data.description}
          >
            {data.description}
          </Typography>
        )}
      </Box>
      {reserved && (
        <Tooltip title={reserved}>
          <Box sx={{ flexShrink: 0 }}>
            <GatewayStatusChip tone="warning" label="Not recorded" />
          </Box>
        </Tooltip>
      )}
    </Stack>
  );
}
NameCell.propTypes = { data: PropTypes.object };

function AllowedCell({ value }) {
  const list = value || [];
  if (!list.length) {
    return (
      <Cell>
        <Typography typography="s2" color="text.disabled">
          Any
        </Typography>
      </Cell>
    );
  }
  return (
    <Stack
      direction="row"
      gap={0.5}
      alignItems="center"
      sx={{ height: 1, overflow: "hidden" }}
      title={list.join(", ")}
    >
      {list.slice(0, 3).map((v, index, shown) => (
        <Box
          key={String(v)}
          component="span"
          sx={{
            px: 0.75,
            py: 0.125,
            borderRadius: 0.5,
            border: 1,
            borderColor: "divider",
            typography: "s3",
            whiteSpace: "nowrap",
            // Long values give way (the last shown one first), so the +N
            // count stays whole.
            minWidth: 0,
            flexShrink: index === shown.length - 1 ? 100 : 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {String(v)}
        </Box>
      ))}
      {list.length > 3 && (
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ flexShrink: 0 }}
        >
          +{list.length - 3}
        </Typography>
      )}
    </Stack>
  );
}
AllowedCell.propTypes = { value: PropTypes.array };

function RowActions({ data, context }) {
  // Edit and Delete are gateway writes: Owner or Admin only.
  const allowed = Boolean(context.canEdit);
  const action = (label, icon, onClick) => {
    const button = (
      <IconButton
        size="small"
        aria-label={`${label} ${data.name}`}
        onClick={() => onClick(data)}
        disabled={!allowed}
      >
        <SvgColor src={icon} aria-hidden sx={{ width: 16, height: 16 }} />
      </IconButton>
    );
    return allowed ? (
      <Tooltip title={label}>{button}</Tooltip>
    ) : (
      <GatewayAdminOnly allowed={false}>{button}</GatewayAdminOnly>
    );
  };
  return (
    <Stack direction="row" gap={0.5} alignItems="center" sx={{ height: 1 }}>
      {action("Edit", "/assets/icons/ic_edit.svg", context.onEdit)}
      {action("Delete", "/assets/icons/ic_delete.svg", context.onDelete)}
    </Stack>
  );
}
RowActions.propTypes = { data: PropTypes.object, context: PropTypes.object };

const COLUMNS = [
  {
    field: "name",
    headerName: "Property",
    minWidth: 220,
    flex: 1.5,
    cellRenderer: NameCell,
  },
  {
    field: "property_type",
    headerName: "Type",
    minWidth: 110,
    flex: 0.6,
    valueFormatter: ({ value }) => TYPE_LABEL[value] || value,
  },
  {
    field: "required",
    headerName: "Required (Validate API)",
    // A boolean column would render as a checkbox; this one is a word.
    cellDataType: "text",
    minWidth: 144,
    flex: 0.5,
    valueFormatter: ({ value }) => (value ? "Required" : "Optional"),
  },
  {
    field: "allowed_values",
    headerName: "Allowed values (Validate API)",
    minWidth: 200,
    flex: 1.3,
    sortable: false,
    cellRenderer: AllowedCell,
  },
  // A Default has no reader anywhere: the column is there only when
  // a property still has one stored, and says it is not applied.
  {
    colId: "stored_default",
    field: "default_value",
    headerName: "Stored default (not applied)",
    minWidth: 200,
    flex: 0.8,
    valueFormatter: ({ value }) =>
      value === null || value === undefined ? "—" : String(value),
  },
  {
    field: "created_at",
    headerName: "Added",
    minWidth: 110,
    flex: 0.6,
    valueFormatter: ({ value }) => formatDate(value),
  },
  {
    colId: "actions",
    headerName: "",
    minWidth: 90,
    flex: 0.4,
    sortable: false,
    resizable: false,
    cellRenderer: RowActions,
  },
];

function CodeLine({ label, code }) {
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: 0.5 }}
      >
        <Typography typography="s2" color="text.secondary">
          {label}
        </Typography>
        <CopyButton text={code} label={`Copy ${label.toLowerCase()}`} />
      </Stack>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 1.5,
          borderRadius: 1,
          border: 1,
          borderColor: "divider",
          bgcolor: "background.neutral",
          typography: "s2",
          fontFamily: GATEWAY_MONO,
          overflowX: "auto",
          whiteSpace: "pre",
        }}
      >
        {code}
      </Box>
    </Box>
  );
}
CodeLine.propTypes = {
  label: PropTypes.string.isRequired,
  code: PropTypes.string.isRequired,
};

function matches(property, query) {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    property.name?.toLowerCase().includes(q) ||
    property.description?.toLowerCase().includes(q)
  );
}

// Custom properties: the metadata names the organization attaches to its
// requests (tenant, feature, ...). A declared property becomes a column you
// can show in Logs. Required and Allowed values are checked by the Validate
// API, not applied by the gateway; a stored default is not applied anywhere
//; and a name the gateway reserves is never recorded.
export default function CustomPropertySection() {
  // Declaring, editing and deleting a property are gateway writes: Owner or
  // Admin only, as on Providers, API keys and Budgets.
  const { canEdit } = useGatewayAdmin();
  const [search, setSearch] = useState("");
  const [dialog, setDialog] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);

  const { data: properties, isLoading, error, refetch } = useCustomProperties();
  const createMutation = useCreateCustomProperty();
  const updateMutation = useUpdateCustomProperty();
  const deleteMutation = useDeleteCustomProperty();

  const all = useMemo(() => properties || [], [properties]);
  const rows = useMemo(
    () => all.filter((p) => matches(p, search.trim())),
    [all, search],
  );
  const snippet = useMemo(() => sendSnippet(all), [all]);
  const columns = useMemo(
    () =>
      all.some(hasStoredDefault)
        ? COLUMNS
        : COLUMNS.filter((column) => column.colId !== "stored_default"),
    [all],
  );

  const save = (payload) => {
    const editing = Boolean(payload.id);
    const mutation = editing ? updateMutation : createMutation;
    mutation.mutate(payload, {
      onSuccess: () => {
        enqueueSnackbar(editing ? "Property saved" : "Property added", {
          variant: "success",
        });
        setDialog(null);
      },
      onError: (err) =>
        enqueueSnackbar(
          err?.message ||
            err?.response?.data?.message ||
            (editing
              ? "Failed to save the property"
              : "Failed to add the property"),
          { variant: "error" },
        ),
    });
  };

  const remove = () => {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () => {
        enqueueSnackbar("Property deleted", { variant: "success" });
        setDeleteTarget(null);
      },
      onError: () =>
        enqueueSnackbar("Failed to delete the property", { variant: "error" }),
    });
  };

  let content;
  if (isLoading) {
    content = (
      <Stack spacing={1} data-testid="custom-properties-loading">
        <Skeleton variant="rounded" height={32} width={320} />
        <Skeleton variant="rounded" height={160} />
      </Stack>
    );
  } else if (error) {
    content = (
      <GatewayEmptyState
        icon="properties"
        title="Couldn't load custom properties"
        description={error.message || "The custom properties request failed."}
        actionLabel="Retry"
        onAction={() => refetch?.()}
      />
    );
  } else {
    const none = all.length === 0;
    content = (
      <>
        {!none && (
          <GatewayFilterBar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search properties"
          />
        )}
        {all.some(hasRules) && (
          <Typography typography="s2" color="text.secondary">
            {RULES_CHECKED_BY_VALIDATE_API}
          </Typography>
        )}
        <GatewayGrid
          rowData={rows}
          columnDefs={columns}
          stateKey="gateway-custom-properties"
          getRowId={(params) => params.data.id}
          rowHeight={ROW_HEIGHT}
          height={Math.max(rows.length, 1) * ROW_HEIGHT + 42}
          context={{
            canEdit,
            onEdit: (property) => setDialog({ property }),
            onDelete: setDeleteTarget,
          }}
          emptyState={
            none
              ? {
                  icon: "properties",
                  title: "No custom properties yet",
                  description:
                    "Declare the metadata your requests carry, such as tenant or feature, so it can be shown as a column in Logs.",
                  actionLabel: canEdit ? "Add property" : undefined,
                  onAction: canEdit
                    ? () => setDialog({ property: null })
                    : undefined,
                }
              : { variant: "no-results", onAction: () => setSearch("") }
          }
        />
        {snippet.count > 0 && (
          <SettingsPanel
            title="Send them with a request"
            description="Add the properties to the x-agentcc-metadata header, or to the request body's metadata. The gateway records each one on the request."
          >
            <CodeLine label="Header" code={snippet.header} />
            <CodeLine label="Request body" code={snippet.body} />
            <Typography typography="s2" color="text.secondary">
              Show a property as a column in{" "}
              <Link
                component={RouterLink}
                href={paths.dashboard.gateway.logs}
                underline="hover"
              >
                Logs
              </Link>{" "}
              (Columns, Custom properties).
            </Typography>
          </SettingsPanel>
        )}
      </>
    );
  }

  return (
    <GatewayPageFrame
      title="Custom properties"
      description="The metadata your requests carry, such as tenant or feature, declared so Logs can show it as a column."
      actions={
        <GatewayAdminOnly allowed={canEdit}>
          <Button
            variant="contained"
            color="primary"
            onClick={() => setDialog({ property: null })}
            disabled={!canEdit}
          >
            Add property
          </Button>
        </GatewayAdminOnly>
      }
    >
      {content}

      <CreateEditPropertyDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        onSubmit={save}
        property={dialog?.property || null}
        isPending={createMutation.isPending || updateMutation.isPending}
      />

      <Dialog
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Delete property</DialogTitle>
        <DialogContent>
          <Typography typography="s1">
            Delete <strong>{deleteTarget?.name}</strong>? Requests keep the
            values they were logged with.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>Cancel</Button>
          <Button
            variant="contained"
            color="error"
            onClick={remove}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? "Deleting…" : "Delete"}
          </Button>
        </DialogActions>
      </Dialog>
    </GatewayPageFrame>
  );
}
