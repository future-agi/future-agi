import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Typography } from "@mui/material";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import CreateKeyDialog from "../keys/CreateKeyDialog";
import { Secret } from "../keys/OneTimeKeyReveal";
import { permits } from "../keys/keyRouteAccess";
import { NO_RECORDING_CLASS } from "src/sections/omega/code/noRecording";

function Session({ gatewayId, route, children }) {
  const [open, setOpen] = useState(false);
  const [reveal, setReveal] = useState(null);
  const secret = reveal?.secret || "";
  const blocked = reveal && route && !(
    permits(reveal.allowedModels, route.id) &&
    permits(reveal.allowedProviders, route.provider) &&
    permits(reveal.requestedModels ?? [], route.id) &&
    permits(reveal.requestedProviders ?? [], route.provider)
  );
  const active = useRef(true);
  React.useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  return (
    <Box className={NO_RECORDING_CLASS}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 1,
          mb: 1,
        }}
      >
        <Typography typography="s2" color="text.secondary">
          {secret
            ? "This is the only time this secret is shown. Copy your key before leaving or forgetting it."
            : "Create a personal key to fill this snippet."}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          onClick={() => (secret ? setReveal(null) : setOpen(true))}
        >
          {secret ? "Forget key" : "Create a key"}
        </Button>
      </Box>
      {secret && (
        <Box mb={2}>
          <Secret label="API key" value={secret} copyLabel="Copy key" />
        </Box>
      )}
      {blocked ? (
        <Alert severity="warning">
          The selected model does not match this key&apos;s access lists. Choose an
          allowed model above or select Discover models. Your new key is still
          shown here; no request has been sent.
        </Alert>
      ) : children(secret)}
      {open && (
        <CreateKeyDialog
          open
          gatewayId={gatewayId}
          onClose={() => setOpen(false)}
          onCreated={(result) => {
            if (active.current) setReveal(result);
          }}
        />
      )}
    </Box>
  );
}
export default function GatewaySnippetKey(props) {
  const { user } = useSafeAuthContext();
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId } = useWorkspace();
  return (
    <Session
      key={`${user?.id}:${currentOrganizationId}:${currentWorkspaceId}:${props.gatewayId}`}
      {...props}
    />
  );
}
GatewaySnippetKey.propTypes = Session.propTypes = {
  gatewayId: PropTypes.string,
  route: PropTypes.shape({ id: PropTypes.string, provider: PropTypes.string }),
  children: PropTypes.func.isRequired,
};
