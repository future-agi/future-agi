import PropTypes from "prop-types";
import { Alert, Box, Button, Dialog, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { toolFit } from "../_mock/toolFit";
import { envVersionNameOf } from "../_mock/codeRefs";
import {
  agentVersions,
  isCredentialsVersion,
  productionAgentVersion,
  versionNumber,
} from "../_mock/versions";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * Change an environment's default agent version — production (it moves with
 * each new production version) or one version held. Nothing runs — the next
 * run uses it — earlier runs stay with the version they ran, and the change
 * is written to the environment's history. If the new version calls a tool
 * this environment's world doesn't answer, say so before switching rather
 * than after a run fails.
 */
export default function SwitchVersionDialog({
  open,
  onClose,
  env,
  envState,
  label,
  onConfirm,
}) {
  if (!env || !envState || !label) return null;
  /* A code agent's versions read as commits — "main @ 3f9a2c1". */
  const nm = (l) => (l ? envVersionNameOf(envState, l, env?.id).main : l);
  const name = nm(label);
  const fit = toolFit(env, envState, { agent: label });
  /* Production as the default moves with it; any other version is held until the default is production again. */
  const follows = label === productionAgentVersion(envState)?.label;
  /* Credentials are versioned with the agent, so an older version runs with
     the key it had — said, in case that key has since been revoked. */
  const newerKeys = agentVersions(envState).filter(
    (v) =>
      isCredentialsVersion(v) && versionNumber(v.label) > versionNumber(label),
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Box sx={{ px: 3, pt: 2.5, pb: 2 }}>
        <Typography sx={{ typography: "m3", fontWeight: 600 }}>
          {follows
            ? `Make production the default in ${env.name}?`
            : `Make ${name} the default in ${env.name}?`}
        </Typography>
        <Typography
          sx={{ typography: "s2", color: "text.secondary", mt: 0.75 }}
        >
          {follows
            ? `It tests ${name}, the production version, and follows each new one from now on. Nothing runs now; earlier runs keep their version.`
            : `It stays on ${name} — new production versions won't move it. Nothing runs now; earlier runs keep their version.`}
        </Typography>
        {!follows && newerKeys.length > 0 && (
          <Typography
            sx={{ typography: "s3", color: "text.secondary", mt: 1.25 }}
          >
            {`${name} runs with the credentials it had — from before ${newerKeys.map((v) => nm(v.label)).join(", ")}. If that key was revoked, its runs won't reach the agent.`}
          </Typography>
        )}
        {!fit.fits && (
          <Alert
            severity="warning"
            icon={<Iconify icon="solar:danger-triangle-linear" width={16} />}
            sx={{ mt: 1.75 }}
          >
            {`${name} calls ${fit.missing.length === 1 ? "a tool" : "tools"} this environment's world doesn't answer: `}
            <Box component="span" sx={{ fontFamily: MONO }}>
              {fit.missing.map((t) => t.name).join(", ")}
            </Box>
            {`. Scenarios that need ${fit.missing.length === 1 ? "it" : "them"} come back Not measured until the World tab adds ${fit.missing.length === 1 ? "it" : "them"}.`}
          </Alert>
        )}
      </Box>
      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{ px: 3, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={onConfirm}
        >
          {!fit.fits
            ? "Change anyway"
            : follows
              ? "Default to production"
              : `Make ${name} the default`}
        </Button>
      </Stack>
    </Dialog>
  );
}

SwitchVersionDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  label: PropTypes.string,
  onConfirm: PropTypes.func,
};
