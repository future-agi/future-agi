import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Popover from "@mui/material/Popover";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { hubPaths } from "../constants";
import { cliCommands } from "../lib/useThis";

import CopyCommand from "./CopyCommand";
import HubIcon from "./HubIcon";

// The run and add commands of an entry's latest version, in their registry
// form (lib/useThis.js builds them), only once a customer can run them: the
// server says the Hub part of the Omega command line is released. Until then
// there is no command to show, and neither part below draws one.
function useEntryCommands(entry) {
  const released = useOmegaPart(OMEGA_PART.HUB_CLI);
  if (!released || !entry?.ref || !entry?.latestVersion) return null;
  return cliCommands(entry.ref, entry.latestVersion);
}

function CommandRow({ caption, text, testId }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
      <Typography typography="s2" color="text.secondary">
        {caption}
      </Typography>
      <CopyCommand text={text} testId={testId} />
    </Box>
  );
}

CommandRow.propTypes = {
  caption: PropTypes.string.isRequired,
  text: PropTypes.string.isRequired,
  testId: PropTypes.string,
};

// "Use" on a card: a popover with the entry's two whole commands, each with
// its own copy button. It is anchored to the card, so opening it never grows
// the row. Escape or a click outside closes it, and focus goes back to Use.
export function UseEntryButton({ entry }) {
  const commands = useEntryCommands(entry);
  const [anchor, setAnchor] = useState(null);
  const id = useId();
  if (!commands) return null;
  const title = entry.title || entry.name;
  const open = Boolean(anchor);
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        aria-label={`Use ${title}`}
        aria-haspopup="dialog"
        aria-expanded={open ? "true" : "false"}
        aria-controls={open ? id : undefined}
        endIcon={<HubIcon name="chevronDown" size={14} />}
        onClick={(event) => setAnchor(event.currentTarget)}
        data-testid="hub-entry-use"
        sx={{
          position: "relative",
          zIndex: 1,
          flexShrink: 0,
          borderColor: "divider",
        }}
      >
        Use
      </Button>
      <Popover
        id={id}
        open={open}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            role: "dialog",
            "aria-label": `Use ${title}`,
            sx: {
              mt: 0.5,
              p: 1.5,
              width: 400,
              maxWidth: "calc(100vw - 32px)",
              display: "flex",
              flexDirection: "column",
              gap: 1.5,
            },
          },
        }}
      >
        <CommandRow
          caption="Run it"
          text={commands.run}
          testId="hub-entry-use-run"
        />
        <CommandRow
          caption="Add it to your project"
          text={commands.add}
          testId="hub-entry-use-add"
        />
      </Popover>
    </>
  );
}

UseEntryButton.propTypes = {
  entry: PropTypes.shape({
    ref: PropTypes.string,
    name: PropTypes.string,
    title: PropTypes.string,
    latestVersion: PropTypes.string,
  }).isRequired,
};

const TRY_BOX_SX = {
  display: "flex",
  flexDirection: "column",
  gap: 1,
  p: 2,
  minWidth: 0,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
  bgcolor: "background.paper",
};

// "Try one now" on the Hub home: one entry to start with, its whole run
// command with copy, what it does (with the maintainer's why when it is this
// week's pick, since Start here then leaves it out) and a way to its page.
// With no command to show, the box still names the entry and links to it.
// While the entry is being chosen (`loading`), placeholders hold the box's
// place, so the control row below does not move when it arrives.
export function TryOneNow({ entry, why, loading = false }) {
  const commands = useEntryCommands(entry);
  if (loading) {
    return (
      <Box aria-hidden="true" data-testid="hub-try-one-loading" sx={TRY_BOX_SX}>
        <Skeleton variant="text" width={88} sx={{ fontSize: 13 }} />
        <Skeleton variant="rounded" height={40} />
        <Skeleton variant="text" width="85%" sx={{ fontSize: 11 }} />
      </Box>
    );
  }
  if (!entry?.ref) return null;
  const href = hubPaths.entry(entry.publisher?.handle, entry.name);
  const title = entry.title || entry.name;
  return (
    <Box
      component="section"
      aria-labelledby="hub-try-one-title"
      data-testid="hub-try-one"
      data-ref={entry.ref}
      sx={TRY_BOX_SX}
    >
      <Typography
        id="hub-try-one-title"
        component="h2"
        typography="s1"
        fontWeight="fontWeightSemiBold"
        color="text.primary"
      >
        {commands ? "Try one now" : "Start with this one"}
      </Typography>
      {commands ? (
        <CopyCommand text={commands.run} testId="hub-try-one-run" />
      ) : (
        <Link
          component={RouterLink}
          href={href}
          underline="hover"
          typography="m3"
          fontWeight="fontWeightSemiBold"
          color="text.primary"
        >
          {title}
        </Link>
      )}
      {entry.tagline && (
        <Typography typography="s2" color="text.secondary">
          {entry.tagline}
        </Typography>
      )}
      {why && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="hub-try-one-why"
        >
          {`Maintainer pick: ${why}`}
        </Typography>
      )}
      {(commands || entry.noKeyNeeded) && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            columnGap: 1.5,
            rowGap: 0.25,
          }}
        >
          {entry.noKeyNeeded && (
            <Typography typography="s2" color="text.secondary">
              Runs without a key
            </Typography>
          )}
          {commands && (
            <Link
              component={RouterLink}
              href={href}
              underline="hover"
              typography="s2"
              fontWeight={500}
            >
              See the entry
            </Link>
          )}
        </Box>
      )}
    </Box>
  );
}

TryOneNow.propTypes = {
  entry: PropTypes.shape({
    ref: PropTypes.string,
    name: PropTypes.string,
    title: PropTypes.string,
    tagline: PropTypes.string,
    latestVersion: PropTypes.string,
    noKeyNeeded: PropTypes.bool,
    publisher: PropTypes.object,
  }),
  why: PropTypes.string,
  loading: PropTypes.bool,
};
