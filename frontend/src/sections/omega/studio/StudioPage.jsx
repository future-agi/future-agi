import React from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { Helmet } from "react-helmet-async";

import StudioConnectScreen from "./StudioConnectScreen";
import StudioConnectedView from "./StudioConnectedView";
import { PAGE } from "./studio-copy";
import { useStudioConnection } from "./use-studio-connection";

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
    onStatusChange,
  } = useStudioConnection();
  const hostOrigin = window.location.origin;

  return (
    <Box
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
