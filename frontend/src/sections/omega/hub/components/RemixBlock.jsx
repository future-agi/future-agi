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
import HubPanel from "./HubPanel";

// "Remixed from" (one link) and the remixes made from this entry: one level
// of lineage, like a model tree (SPEC §6.3). It is a side card like the
// others; once there is a remix, its heading carries the count in a quiet
// pill ("Remixes 3"), the way the tabs count files and versions, so a new
// entry shows no zero and the word is not said twice. The remix itself is
// made with `omega hub new`, so that command shows only once a customer can
// install a CLI that has it (OMEGA_PART HUB_CLI). With no lineage and no CLI
// the block is left out.
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
  const title =
    remixCount > 0 ? (
      <>
        Remixes{" "}
        <Box
          component="span"
          data-testid="hub-remix-count"
          sx={{
            ml: 0.25,
            px: 0.75,
            borderRadius: 1,
            bgcolor: "background.neutral",
            color: "text.secondary",
            typography: "s3",
            fontWeight: 500,
            lineHeight: "18px",
          }}
        >
          {remixCount}
        </Box>
      </>
    ) : (
      "Remixes"
    );
  return (
    <HubPanel title={title} testId="hub-remixes" dense>
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
    </HubPanel>
  );
}

RemixBlock.propTypes = {
  publisher: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  remixOf: PropTypes.string,
  remixCount: PropTypes.number,
  version: PropTypes.string,
};
