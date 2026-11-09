import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { hubPaths } from "../constants";
import { useHubRemixes } from "../api/queries";
import { splitRef } from "../lib/format";
import { hubCommand } from "../lib/registry";

import CopyCommand from "./CopyCommand";
import { HubSection } from "./HubStates";

// "Remixed from" (one link) and "Remixes (N)": one level of lineage, like a
// model tree (SPEC §6.3). The remix itself is made with `omega hub new`, so
// that command shows only once a customer can install a CLI that has it
// (OMEGA_PART HUB_CLI). With no lineage and no CLI the block is left out.
export default function RemixBlock({
  publisher,
  name,
  remixOf,
  remixCount,
  version,
}) {
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  const { data: remixes } = useHubRemixes(publisher, name, remixCount > 0);
  const origin = remixOf ? splitRef(remixOf) : null;
  const canRemix = cliReleased && Boolean(version);
  if (!origin && !(remixCount > 0) && !canRemix) return null;
  return (
    <HubSection title="Remixes" testId="hub-remixes">
      {origin && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="hub-remixed-from"
        >
          {"Remixed from "}
          <Link
            component={RouterLink}
            href={hubPaths.entry(origin.publisher, origin.name)}
            underline="hover"
          >
            {`${origin.publisher}/${origin.name}`}
          </Link>
          {origin.version ? ` @ ${origin.version}` : ""}
        </Typography>
      )}
      <Typography
        typography="s2"
        color="text.secondary"
        data-testid="hub-remix-count"
      >
        {`Remixes (${remixCount || 0})`}
      </Typography>
      {(remixes || []).length > 0 && (
        <Box component="ul" sx={{ m: 0, pl: 2, display: "grid", gap: 0.25 }}>
          {remixes.map((card) => (
            <li key={card.ref}>
              <Link
                component={RouterLink}
                href={hubPaths.entry(card.publisher.handle, card.name)}
                typography="s2"
              >
                {card.ref}
              </Link>
            </li>
          ))}
        </Box>
      )}
      {canRemix && (
        <>
          <Typography typography="s3" color="text.tertiary">
            Remix it from your terminal:
          </Typography>
          <CopyCommand
            text={hubCommand([
              "new",
              `my-${name}`,
              "--from",
              `hub:${publisher}/${name}@${version}`,
            ])}
          />
        </>
      )}
    </HubSection>
  );
}

RemixBlock.propTypes = {
  publisher: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  remixOf: PropTypes.string,
  remixCount: PropTypes.number,
  version: PropTypes.string,
};
