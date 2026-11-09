import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import CreateKeyDialog from "../keys/CreateKeyDialog";
import { NO_RECORDING_CLASS } from "src/sections/omega/code/noRecording";

function Session({ gatewayId, children }) {
  const [open, setOpen] = useState(false);
  const [secret, setSecret] = useState("");
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
            ? "Your personal key fills this snippet until you leave or forget it."
            : "Create a personal key to fill this snippet."}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          onClick={() => (secret ? setSecret("") : setOpen(true))}
        >
          {secret ? "Forget key" : "Create a key"}
        </Button>
      </Box>
      {children(secret)}
      {open && (
        <CreateKeyDialog
          open
          gatewayId={gatewayId}
          onClose={() => setOpen(false)}
          onCreated={(result) => {
            if (active.current) setSecret(result.secret);
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
  children: PropTypes.func.isRequired,
};
