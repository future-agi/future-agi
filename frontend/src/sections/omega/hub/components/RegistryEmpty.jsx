import React from "react";
import Button from "@mui/material/Button";

import { isHostedHub } from "../lib/registry";

import { HubEmpty } from "./HubStates";

// Self-hosted is an independent registry, not a mirror. This is an ordinary
// external link: browsing the app never fetches the public catalogue (D6).
export default function RegistryEmpty() {
  const hosted = isHostedHub();
  return (
    <HubEmpty
      title="The Hub has no entries yet"
      description={
        hosted
          ? "Entries appear here once a Future AGI maintainer approves them."
          : "This Hub is your organisation's own registry. Entries appear here after approval. Official entries are available in the public Omega Hub catalogue; they are not automatically imported into this registry."
      }
      action={
        hosted ? null : (
          <Button
            component="a"
            href="https://docs.futureagi.com/hub/"
            target="_blank"
            rel="noopener noreferrer"
            size="small"
            variant="outlined"
          >
            Browse the public Omega Hub catalogue
          </Button>
        )
      }
    />
  );
}
