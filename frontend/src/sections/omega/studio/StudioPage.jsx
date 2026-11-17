import React, { useEffect, useRef } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { Helmet } from "react-helmet-async";

import StudioConnectScreen from "./StudioConnectScreen";
import StudioConnectedView from "./StudioConnectedView";
import { PAGE } from "./studio-copy";
import { useStudioConnection } from "./use-studio-connection";

// Notices that ask for the link again: the paste field is where the next step
// happens, so it takes focus when the connect screen comes back with one.
const ASKS_FOR_LINK = new Set(["needs-key", "refused"]);

// /dashboard/studio and /dashboard/studio/*: Omega Studio inside Future AGI.
//
// `omega dev` prints a link back to this page that carries its server and a
// one-time key in the fragment. The first script in index.html takes both out
// of the address bar before any analytics starts; this page frames the Studio
// that omega dev serves and hands the key over postMessage (omega-embed/1).
// Without a link it shows the connect screen. Future AGI itself never calls
// the local server: only the frame does.
export default function StudioPage() {
  const {
    connection,
    notice,
    status,
    connectPasted,
    connectSaved,
    disconnect,
    pasteLink,
    onStatusChange,
  } = useStudioConnection();
  const hostOrigin = window.location.origin;
  const root = useRef(null);

  useEffect(() => {
    if (connection || !ASKS_FOR_LINK.has(notice?.kind)) return;
    root.current
      ?.querySelector('input[data-testid="studio-connect-input"]')
      ?.focus();
  }, [connection, notice]);

  return (
    <Box
      ref={root}
      // The shell's route tests find every tab entry by data-testid
      // "omega-tab-<tab>".
      data-testid="omega-tab-studio"
      data-connected={connection ? "true" : "false"}
      sx={{
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        p: 2,
      }}
    >
      <Helmet>
        <title>{PAGE.title}</title>
      </Helmet>
      {connection ? (
        <StudioConnectedView
          connection={connection}
          status={status}
          hostOrigin={hostOrigin}
          onStatusChange={onStatusChange}
          onDisconnect={disconnect}
          onPasteLink={pasteLink}
        />
      ) : (
        <>
          <Box>
            <Typography
              component="h1"
              typography="m2"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              {PAGE.title}
            </Typography>
            <Typography typography="s1" color="text.secondary">
              {PAGE.description}
            </Typography>
          </Box>
          <StudioConnectScreen
            origin={hostOrigin}
            notice={notice}
            onConnectPasted={connectPasted}
            onConnectSaved={connectSaved}
          />
        </>
      )}
    </Box>
  );
}
