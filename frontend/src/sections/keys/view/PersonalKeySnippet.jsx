import React, { useContext, useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { NO_RECORDING_CLASS } from "src/sections/omega/code/noRecording";
import CreateApiKey from "./CreateApiKey";
import { PersonalKeyContext as KeyContext } from "src/components/personal-key-snippet-context";

function SnippetSession({ children, active }) {
  const [pair, setPair] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!active) {
      setPair(null);
      setOpen(false);
    }
  }, [active]);
  return (
    <KeyContext.Provider
      value={{
        pair: active ? pair : null,
        create: () => setOpen(true),
        forget: () => setPair(null),
      }}
    >
      <Box className={NO_RECORDING_CLASS} sx={{ display: "contents" }}>
        {children}
      </Box>
      <CreateApiKey
        open={active && open}
        onClose={() => setOpen(false)}
        onCreated={setPair}
      />
    </KeyContext.Provider>
  );
}

// Scoped to this surface: navigation, closing, logout or a context change drops it.
// Neither this boundary nor its control fetches or creates a key on mount.
export default function PersonalKeySnippet({ children, active = true }) {
  const { user } = useSafeAuthContext();
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId } = useWorkspace();
  return (
    <SnippetSession
      key={`${user?.id}:${currentOrganizationId}:${currentWorkspaceId}`}
      active={active}
    >
      {children}
    </SnippetSession>
  );
}

export function PersonalKeyControl() {
  const context = useContext(KeyContext);
  if (!context) return null;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 1,
        my: 1,
      }}
    >
      <Typography typography="s2" color="text.secondary">
        {context.pair
          ? "Your personal key fills this snippet until you leave or forget it."
          : "Use your personal API key. The example keeps placeholders until you create one."}
      </Typography>
      <Button
        size="small"
        variant="outlined"
        onClick={context.pair ? context.forget : context.create}
      >
        {context.pair ? "Forget key" : "Create a key"}
      </Button>
    </Box>
  );
}

PersonalKeySnippet.propTypes = {
  children: PropTypes.node,
  active: PropTypes.bool,
};
SnippetSession.propTypes = { children: PropTypes.node, active: PropTypes.bool };
