import React, { Suspense, lazy } from "react";
import CommandPalette from "./CommandPalette";
import PaletteBoundary from "./PaletteBoundary";

// The Console route (/dashboard/console/*) renders outside the dashboard
// layout, so its layout mounts the app palette here, once, for every state
// of the tab: the frame, the viewer panel and the page's loading screen. The
// bridge that lets the embedded Console open the palette and hand it its
// commands lives with the frame (ConsoleFrame, useConsolePaletteBridge).
// The palette itself is ready with the tab; the page list follows.
const AppPageCommands = lazy(() => import("./AppPageCommands"));

export default function ConsolePaletteHost() {
  return (
    <>
      <PaletteBoundary>
        <Suspense fallback={null}>
          <AppPageCommands />
        </Suspense>
      </PaletteBoundary>
      <PaletteBoundary>
        <CommandPalette host="console" />
      </PaletteBoundary>
    </>
  );
}
