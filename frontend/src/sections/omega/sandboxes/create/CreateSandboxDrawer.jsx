import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import { useNavigate } from "react-router-dom";

import {
  newIdempotencyKey,
  retryIdempotent,
  useEnvironments,
  useSandboxMutation,
} from "../api/queries";
import ShowAsCode from "../../code/ShowAsCode";
import { sandboxCreateRequest } from "../../code/sandboxRequests";
import SandboxIcon from "../components/SandboxIcon";
import {
  CUSTOM_SIZE,
  LIMIT_FIELDS,
  buildCreatePayload,
  findSize,
  findTemplate,
  initialForm,
  sizeSummary,
  validateForm,
} from "../lib/createForm";
import { SANDBOX_SETTINGS_PATH as SETTINGS_PATH } from "../lib/links";
import { createRefusalText, sandboxErrorText } from "../lib/offerCopy";
import CreateSandboxForm from "./CreateSandboxForm";

/** Right-hand drawer that creates one sandbox within the provider's caps. */
export default function CreateSandboxDrawer({
  open,
  caps,
  onClose,
  onCreated,
}) {
  const navigate = useNavigate();
  const [form, setForm] = useState(() => initialForm(caps));
  const [errors, setErrors] = useState({});
  // Read when the drawer opens: the Environment source shows only when the
  // workspace has a container environment to start from.
  const environments = useEnvironments(open);
  // One key per submitted form: a retried request replays EE's first answer.
  const attempt = useRef(null);
  const create = useSandboxMutation(
    (api, payload) => api.create(payload, { idempotencyKey: attempt.current }),
    { retry: retryIdempotent, onSuccess: (sandbox) => onCreated(sandbox) },
  );

  useEffect(() => {
    if (open) {
      setForm(initialForm(caps));
      setErrors({});
      create.reset();
    }
    // Reset only when the drawer opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const change = (patch) => {
    setForm((current) => ({ ...current, ...patch }));
    setErrors((current) => {
      const next = { ...current };
      Object.keys(patch).forEach((key) => delete next[key]);
      return next;
    });
  };

  // While the form shows refusals, the footer must not describe a sandbox the
  // button will not create (found in the live walk: "3 vCPU · 6000 MB" next to
  // fields refusing exactly those values). Only refusals that still apply to
  // the form as it is now count: one left over from another source does not.
  // Custom limits are checked as they are typed (their range and the chosen
  // template's minimum), not only on submit (console-reality audit: "5 vCPU ·
  // 32 MB" with Create enabled).
  const stillRefused = validateForm(form, caps);
  const shown = { ...errors };
  if (form.size === CUSTOM_SIZE) {
    LIMIT_FIELDS.forEach(({ key }) => {
      if (stillRefused[key]) shown[key] = stillRefused[key];
    });
  }
  const invalid = Object.keys(shown).some((key) => stillRefused[key]);

  const refusal = caps.createRefusal || null;
  const submit = (event) => {
    event.preventDefault();
    if (refusal) return;
    const found = validateForm(form, caps);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    attempt.current = newIdempotencyKey();
    create.mutate(buildCreatePayload(form));
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={create.isPending ? undefined : onClose}
      PaperProps={{
        sx: { width: { xs: "100%", sm: 560 }, bgcolor: "background.paper" },
      }}
    >
      <Box
        component="form"
        noValidate
        onSubmit={submit}
        data-testid="create-sandbox-drawer"
        sx={{ height: "100%", display: "flex", flexDirection: "column" }}
      >
        <Box
          sx={{
            px: 2.5,
            py: 1.5,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box>
            <Typography
              typography="m3"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              New sandbox
            </Typography>
            <Typography typography="s2" color="text.secondary">
              A container of its own, with files, snapshots and an idle timeout.
            </Typography>
          </Box>
          <IconButton
            aria-label="Close"
            onClick={onClose}
            disabled={create.isPending}
            size="small"
          >
            <SandboxIcon name="close" />
          </IconButton>
        </Box>
        <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 2.5 }}>
          {refusal ? (
            // Settings that leave nothing runnable: say why and who can fix
            // it, instead of a form that offers shapes EE will refuse.
            <Alert severity="warning" data-testid="create-refusal">
              {createRefusalText(refusal)}
              {caps.settings?.canEdit && (
                <Box sx={{ mt: 1 }}>
                  <Button
                    size="small"
                    color="inherit"
                    variant="outlined"
                    onClick={() => navigate(SETTINGS_PATH)}
                    data-testid="create-refusal-settings"
                  >
                    Open sandbox settings
                  </Button>
                </Box>
              )}
            </Alert>
          ) : (
            <>
              <CreateSandboxForm
                caps={caps}
                form={form}
                errors={shown}
                onChange={change}
                environments={{
                  data: environments.data,
                  isLoading: environments.isLoading,
                  error: environments.error,
                }}
              />
              {/* The request Create would send for the form as it is now. */}
              <Box sx={{ mt: 3 }}>
                <ShowAsCode
                  request={sandboxCreateRequest(form, caps)}
                  blockedText="Complete or fix the fields above to see the request Create would send."
                  testId="create-show-as-code"
                />
              </Box>
            </>
          )}
        </Box>
        <Box
          sx={{
            px: 2.5,
            py: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {create.error && (
            <Alert severity="error" sx={{ mb: 1.5 }} data-testid="create-error">
              {sandboxErrorText(create.error)}
            </Alert>
          )}
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-end",
              flexWrap: "wrap",
              gap: 1,
            }}
          >
            {!refusal && (
              <Typography
                typography="s2"
                color={invalid ? "error.main" : "text.secondary"}
                data-testid="create-summary"
                sx={{ mr: "auto", minWidth: 0 }}
              >
                {invalid
                  ? "Fix the highlighted fields to create this sandbox."
                  : summary(form, caps)}
              </Typography>
            )}
            <Button
              color="inherit"
              onClick={onClose}
              disabled={create.isPending}
            >
              Cancel
            </Button>
            <LoadingButton
              type="submit"
              variant="contained"
              color="primary"
              loading={create.isPending}
              disabled={Boolean(refusal)}
              data-testid="create-submit"
            >
              Create sandbox
            </LoadingButton>
          </Box>
        </Box>
      </Box>
    </Drawer>
  );
}

/** "Base · Small, 0.5 vCPU · 1 GB · network off": what the button will create. */
function summary(form, caps) {
  const parts = [];
  if (form.source === "catalog") {
    const template = findTemplate(caps, form.template);
    if (template) parts.push(template.name);
  } else if (form.source === "image" && form.image) {
    parts.push(form.image.split("/").pop());
  }
  if (form.size === CUSTOM_SIZE) {
    parts.push(`${form.cpus} vCPU · ${form.memoryMb} MiB`);
  } else {
    const size = findSize(caps, form.size);
    if (size) parts.push(`${size.label}, ${sizeSummary(size)}`);
  }
  parts.push(
    form.network === "full" ? "network policy-controlled" : "network off",
  );
  return parts.join(" · ");
}

CreateSandboxDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  caps: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
  onCreated: PropTypes.func.isRequired,
};
