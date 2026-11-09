import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import FormControlLabel from "@mui/material/FormControlLabel";
import FormGroup from "@mui/material/FormGroup";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import { GatewayDetailDrawer, GatewayStatusChip } from "../../components/frame";
import ConfirmDialog from "../../components/ConfirmDialog";
import { apiErrorMessage } from "../api";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { useMCPConfigPatch } from "../hooks/useMCPConfigPatch";
import CopyField from "../components/CopyField";
import SectionTitle from "../components/SectionTitle";
import {
  NAMING,
  deleteVirtualServerPatch,
  toolCandidates,
  formFromEntry,
  mcpEndpoint,
  notEnforced,
  slugError,
  slugFromTitle,
  virtualServerPatch,
} from "./virtualServers";
import { GATEWAY_MONO } from "../../utils/fonts";
import { SavedButNotAppliedError } from "../../providers/hooks/useGatewayConfig";
import ToolLimitsFields from "./ToolLimitsFields";

function Field({ title, note, children }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <SectionTitle title={title} note={note} component="h3" />
      {children}
    </Box>
  );
}

Field.propTypes = {
  title: PropTypes.string.isRequired,
  note: PropTypes.node,
  children: PropTypes.node,
};

function ListInput({
  label,
  value,
  onChange,
  options,
  getOptionLabel,
  placeholder,
  disabled = false,
}) {
  return (
    <Autocomplete
      multiple
      disabled={disabled}
      freeSolo={!getOptionLabel}
      size="small"
      options={options}
      value={value}
      getOptionLabel={getOptionLabel || ((option) => option)}
      onChange={(_, next) =>
        onChange(next.map((item) => String(item).trim()).filter(Boolean))
      }
      renderTags={(items, getTagProps) =>
        items.map((item, index) => {
          const { key, ...tagProps } = getTagProps({ index });
          return (
            <Chip
              variant="outlined"
              key={key}
              size="small"
              label={getOptionLabel ? getOptionLabel(item) : item}
              {...tagProps}
            />
          );
        })
      }
      renderInput={(params) => (
        <TextField
          {...params}
          label={label}
          placeholder={value.length ? "" : placeholder}
        />
      )}
    />
  );
}

ListInput.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  options: PropTypes.array.isRequired,
  getOptionLabel: PropTypes.func,
  placeholder: PropTypes.string,
  disabled: PropTypes.bool,
};

// Create or edit one virtual server: a named MCP endpoint (/mcp/v/<slug>)
// that serves a chosen set of the org's servers and tools to chosen keys.
// Only settings the gateway enforces are editable; stored ones it does not
// enforce are listed as such and kept as they are.
export default function VirtualServerDrawer({
  open,
  onClose,
  slug,
  entry,
  gatewayId,
  baseUrl,
  serverIds,
  tools,
  keys,
  takenSlugs,
}) {
  const { canEdit, disabledReason } = useGatewayAdmin();
  const isNew = !slug;
  const [form, setForm] = useState(() => formFromEntry(entry, slug || ""));
  const [slugTouched, setSlugTouched] = useState(false);
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const patch = useMCPConfigPatch(gatewayId);

  useEffect(() => {
    if (!open) return;
    setForm(formFromEntry(entry, slug || ""));
    setSlugTouched(false);
    setError("");
  }, [open, entry, slug]);

  const set = (key) => (value) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const onTitle = (value) =>
    setForm((prev) => ({
      ...prev,
      title: value,
      ...(isNew && !slugTouched ? { slug: slugFromTitle(value) } : {}),
    }));

  const slugProblem = isNew ? slugError(form.slug, takenSlugs) : "";
  const membersProblem =
    form.members.length === 0 ? "Choose at least one server." : "";

  // Stored members that are not one of the org's servers today (removed, or
  // an account member) stay listed so a save does not drop them silently.
  const memberChoices = useMemo(
    () => [...new Set([...serverIds, ...form.members])],
    [serverIds, form.members],
  );

  const preview = useMemo(
    () =>
      toolCandidates(
        {
          members: form.members,
          tools: { include: form.include, exclude: form.exclude },
          overrides: entry?.overrides,
        },
        tools,
      ),
    [form, entry, tools],
  );

  const toolPatterns = useMemo(() => {
    const options = [];
    form.members.forEach((member) => {
      options.push(`${member}/*`);
      tools
        .filter((tool) => tool.server_id === member)
        .forEach((tool) =>
          options.push(`${member}/${tool.upstream_name || tool.name}`),
        );
    });
    return options;
  }, [form.members, tools]);

  const keyLabels = useMemo(() => {
    const labels = {};
    keys.forEach((key) => {
      const id = key.gatewayKeyId || key.gateway_key_id;
      if (id) labels[id] = key.name ? `${key.name} (${id})` : id;
    });
    return labels;
  }, [keys]);

  const unenforced = notEnforced(entry);
  const kept = [];
  if (entry?.overrides && Object.keys(entry.overrides).length) {
    kept.push(`${Object.keys(entry.overrides).length} tool overrides`);
  }
  if (entry?.access?.rules?.length)
    kept.push(`${entry.access.rules.length} access rules`);
  if (entry?.access?.users?.length)
    kept.push(`${entry.access.users.length} user ids`);

  const endpoint =
    form.slug && !slugProblem ? mcpEndpoint(baseUrl, form.slug) : null;

  const save = async () => {
    if (!canEdit || slugProblem || membersProblem || patch.isPending) return;
    setError("");
    try {
      const result = await patch.mutateAsync(virtualServerPatch(form, entry));
      if (result?.gateway_synced === false) {
        enqueueSnackbar(new SavedButNotAppliedError(result).message, {
          variant: "warning",
        });
      } else {
        enqueueSnackbar(
          isNew ? "Virtual server created" : "Virtual server saved",
          {
            variant: "success",
          },
        );
      }
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err, "The virtual server could not be saved."));
    }
  };

  const remove = async () => {
    if (!canEdit || !slug || patch.isPending) return;
    setError("");
    try {
      const result = await patch.mutateAsync(deleteVirtualServerPatch(slug));
      if (result?.gateway_synced === false) {
        enqueueSnackbar(new SavedButNotAppliedError(result).message, {
          variant: "warning",
        });
      } else {
        enqueueSnackbar("Virtual server deleted", { variant: "success" });
      }
      setConfirmDelete(false);
      onClose();
    } catch (err) {
      setConfirmDelete(false);
      setError(
        apiErrorMessage(err, "The virtual server could not be deleted."),
      );
    }
  };

  const canSave =
    canEdit &&
    !slugProblem &&
    !membersProblem &&
    !form.limitsError &&
    !patch.isPending;

  return (
    <GatewayDetailDrawer
      open={open}
      onClose={onClose}
      title={isNew ? "New virtual server" : form.title || slug}
      subtitle={isNew ? "Virtual server" : `Virtual server · ${slug}`}
      width={600}
    >
      <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5, pb: 1 }}>
        {!canEdit && <Alert severity="info">{disabledReason}</Alert>}
        {error && <Alert severity="error">{error}</Alert>}

        <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <TextField
            label="Title"
            disabled={!canEdit}
            size="small"
            value={form.title}
            onChange={(event) => onTitle(event.target.value)}
            fullWidth
          />
          <TextField
            label="Slug"
            size="small"
            value={form.slug}
            onChange={(event) => {
              setSlugTouched(true);
              set("slug")(event.target.value);
            }}
            disabled={!canEdit || !isNew}
            error={Boolean(slugProblem)}
            helperText={
              slugProblem ||
              (isNew
                ? "Part of the endpoint URL. It cannot change once created."
                : "The slug and id of a virtual server never change.")
            }
            fullWidth
          />
          <TextField
            label="Description"
            disabled={!canEdit}
            size="small"
            value={form.description}
            onChange={(event) => set("description")(event.target.value)}
            multiline
            minRows={2}
            fullWidth
          />
          {endpoint && (
            <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
              <Typography typography="s2" color="text.secondary">
                MCP endpoint (Streamable HTTP; send a gateway key as a bearer
                token)
              </Typography>
              <CopyField value={endpoint} label="MCP endpoint" />
            </Box>
          )}
        </Box>

        <Field
          title="Servers"
          note={membersProblem || "Tools come from these servers."}
        >
          {memberChoices.length === 0 ? (
            <Typography typography="s2" color="text.secondary">
              Add an MCP server first; a virtual server serves tools from the
              org&apos;s servers.
            </Typography>
          ) : (
            <FormGroup row sx={{ gap: 0.5 }}>
              {memberChoices.map((id) => (
                <FormControlLabel
                  key={id}
                  label={id}
                  control={
                    <Checkbox
                      size="small"
                      disabled={!canEdit}
                      checked={form.members.includes(id)}
                      onChange={(event) =>
                        set("members")(
                          event.target.checked
                            ? [...form.members, id]
                            : form.members.filter((m) => m !== id),
                        )
                      }
                    />
                  }
                  componentsProps={{ typography: { typography: "s1" } }}
                />
              ))}
            </FormGroup>
          )}
        </Field>

        <Field
          title="Tool names"
          note="How clients of this endpoint see each tool's name."
        >
          <RadioGroup
            row
            value={form.naming}
            onChange={(event) => set("naming")(event.target.value)}
          >
            {NAMING.map((option) => (
              <FormControlLabel
                key={option.value}
                value={option.value}
                control={<Radio size="small" disabled={!canEdit} />}
                label={
                  <Typography typography="s1">
                    {option.label}{" "}
                    <Typography
                      component="span"
                      typography="s2"
                      color="text.secondary"
                      sx={{ fontFamily: GATEWAY_MONO }}
                    >
                      {option.example}
                    </Typography>
                  </Typography>
                }
              />
            ))}
          </RadioGroup>
        </Field>

        <Field
          title="Tools"
          note="Patterns match <server>/<tool>; * matches anything. Leave both empty to serve every tool."
        >
          <ListInput
            disabled={!canEdit}
            label="Only these tools"
            value={form.include}
            onChange={set("include")}
            options={toolPatterns}
            placeholder="support-tools/*"
          />
          <ListInput
            disabled={!canEdit}
            label="Leave out"
            value={form.exclude}
            onChange={set("exclude")}
            options={toolPatterns}
            placeholder="*/delete_*"
          />
        </Field>

        <Field
          title="Who can use it"
          note="With no keys or agents named, every key in the organization can."
        >
          <ListInput
            disabled={!canEdit}
            label="Gateway keys"
            value={form.keys}
            onChange={set("keys")}
            options={Object.keys(keyLabels)}
            getOptionLabel={(id) => keyLabels[id] || id}
          />
          <ListInput
            disabled={!canEdit}
            label="Agent ids"
            value={form.agents}
            onChange={set("agents")}
            options={[]}
            placeholder="The x-agentcc-agent-id a caller sends"
          />
          <FormControlLabel
            control={
              <Switch
                disabled={!canEdit}
                checked={form.defaultForOrg}
                onChange={(event) => set("defaultForOrg")(event.target.checked)}
              />
            }
            label={
              <Box>
                <Typography typography="s1">Organization default</Typography>
                <Typography typography="s2" color="text.secondary">
                  Served at /mcp to every key that is not bound to another
                  virtual server.
                </Typography>
              </Box>
            }
          />
          {kept.length > 0 && (
            <Typography typography="s2" color="text.secondary">
              {`Kept as configured: ${kept.join(", ")}.`}
            </Typography>
          )}
        </Field>

        {open && (
          <ToolLimitsFields
            key={slug || "new"}
            budgets={entry?.budgets}
            onChange={(limitsPatch, limitsError) =>
              setForm((prev) => ({ ...prev, limitsPatch, limitsError }))
            }
          />
        )}

        {unenforced.length > 0 && (
          <Field
            title="Stored, not enforced"
            note="These settings are saved on this virtual server, but this gateway does not apply them. Calls are not held or limited by them."
          >
            {unenforced.map((item) => (
              <Box
                key={item.key}
                sx={{
                  display: "flex",
                  alignItems: { xs: "flex-start", sm: "center" },
                  flexDirection: { xs: "column", sm: "row" },
                  gap: 1,
                  justifyContent: "space-between",
                  p: 1.25,
                  borderRadius: 1,
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Box sx={{ minWidth: 0 }}>
                  <Typography typography="s1">{item.label}</Typography>
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    sx={{ overflowWrap: "anywhere" }}
                  >
                    {item.detail}
                  </Typography>
                </Box>
                <GatewayStatusChip
                  tone="neutral"
                  label="Not enforced on this gateway"
                />
              </Box>
            ))}
          </Field>
        )}

        <Field
          title={`${preview.length} tool ${preview.length === 1 ? "candidate" : "candidates"}`}
          note="Uncomposed upstream inventory, not the served tool set. The gateway normalizes names and excludes reserved names, collisions and quarantined tools; the served count may be lower."
        >
          {preview.length > 0 ? (
            <Box
              sx={{
                display: "flex",
                flexWrap: "wrap",
                gap: 0.75,
                maxHeight: 180,
                overflow: "auto",
              }}
            >
              {preview.map((tool) => (
                <Chip
                  key={tool.ref}
                  size="small"
                  variant="outlined"
                  label={tool.ref}
                  sx={{ fontFamily: GATEWAY_MONO, fontSize: 12 }}
                />
              ))}
            </Box>
          ) : (
            <Typography typography="s2" color="text.secondary">
              No candidate matches yet.
            </Typography>
          )}
        </Field>

        <Box
          sx={{
            display: "flex",
            gap: 1,
            justifyContent: "flex-end",
            flexWrap: "wrap",
            pt: 1,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {!isNew && (
            <Button
              color="error"
              variant="outlined"
              size="small"
              onClick={() => setConfirmDelete(true)}
              disabled={!canEdit || patch.isPending}
              sx={{ mr: "auto" }}
            >
              Delete
            </Button>
          )}
          <Button size="small" variant="outlined" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={save}
            disabled={!canSave}
          >
            {patch.isPending ? "Saving" : isNew ? "Create" : "Save"}
          </Button>
        </Box>
      </Box>

      <ConfirmDialog
        open={canEdit && confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={remove}
        title="Delete virtual server"
        message={`Clients of /mcp/v/${slug} will get 404 only after deletion is saved and successfully synced to the gateway. Its tool calls stay in the log.`}
        confirmLabel="Delete virtual server"
        isLoading={patch.isPending}
      />
    </GatewayDetailDrawer>
  );
}

VirtualServerDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  slug: PropTypes.string,
  entry: PropTypes.object,
  gatewayId: PropTypes.string,
  baseUrl: PropTypes.string,
  serverIds: PropTypes.arrayOf(PropTypes.string).isRequired,
  tools: PropTypes.array.isRequired,
  keys: PropTypes.array.isRequired,
  takenSlugs: PropTypes.arrayOf(PropTypes.string).isRequired,
};
