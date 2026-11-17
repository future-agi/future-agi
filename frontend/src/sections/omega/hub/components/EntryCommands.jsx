/* eslint-disable react-refresh/only-export-components */
import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Popover from "@mui/material/Popover";
import Skeleton from "@mui/material/Skeleton";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { hubPaths } from "../constants";
import { hubApi } from "../api/endpoints";
import { hubApiUrl } from "../lib/registry";
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

/** Whether a card can offer its entry: it names an approved version. */
export function hasEntryAction(entry) {
  const { ref, name, publisher, latestVersion } = entry || {};
  return Boolean(ref && name && publisher?.handle && latestVersion);
}

// The approved files of the latest version as one .tar.gz, at the address
// the entry page's Get the files region uses (HubEntryPage downloadHref).
function bundleHref({ publisher, name, latestVersion }) {
  const path = hubApi.versionBundle(publisher.handle, name, latestVersion);
  return `${hubApiUrl(path)}?source=web`;
}

// A card's action sits above the card's stretched title link.
const ACTION_SX = {
  position: "relative",
  zIndex: 1,
  flexShrink: 0,
  borderColor: "divider",
};

// Use's chevron sits the theme's icon gap (6 px) after its word, never
// touching it, and nothing trails it.
const END_ICON_SX = { "& .MuiButton-endIcon": { ml: 0.75, mr: 0 } };

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
function UseButton({ entry, commands }) {
  const [anchor, setAnchor] = useState(null);
  const id = useId();
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
        sx={{ ...ACTION_SX, ...END_ICON_SX }}
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

UseButton.propTypes = {
  entry: PropTypes.shape({
    name: PropTypes.string,
    title: PropTypes.string,
  }).isRequired,
  commands: PropTypes.shape({
    run: PropTypes.string.isRequired,
    add: PropTypes.string.isRequired,
  }).isRequired,
};

// "Get the files" on a card while there is no command to show: a download
// of the latest version's approved files, in a new tab as on the entry page.
// Its label alone (no icon) keeps it beside "Runs without a key" on one line
// in a card a quarter of the page wide.
function GetFilesButton({ entry }) {
  const title = entry.title || entry.name;
  return (
    <Tooltip
      describeChild
      title={`The approved files of version ${entry.latestVersion}, as one .tar.gz`}
    >
      <Button
        component="a"
        href={bundleHref(entry)}
        target="_blank"
        rel="noopener noreferrer"
        size="small"
        variant="outlined"
        color="inherit"
        aria-label={`Get the files of ${title}`}
        data-testid="hub-entry-get-files"
        sx={ACTION_SX}
      >
        Get the files
      </Button>
    </Tooltip>
  );
}

GetFilesButton.propTypes = {
  entry: PropTypes.shape({
    name: PropTypes.string,
    title: PropTypes.string,
    latestVersion: PropTypes.string,
    publisher: PropTypes.object,
  }).isRequired,
};

// The one action a card holds: Use, with the run and add commands, once a
// customer can run them; until then Get the files. Nothing for an entry
// with no approved version to offer.
export function EntryAction({ entry }) {
  const commands = useEntryCommands(entry);
  if (!hasEntryAction(entry)) return null;
  if (commands) return <UseButton entry={entry} commands={commands} />;
  return <GetFilesButton entry={entry} />;
}

EntryAction.propTypes = {
  entry: PropTypes.shape({
    ref: PropTypes.string,
    name: PropTypes.string,
    title: PropTypes.string,
    latestVersion: PropTypes.string,
    publisher: PropTypes.object,
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

// The box's foot, as a card's: what to know on the left, the action on the
// right.
const TRY_FOOT_SX = {
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  columnGap: 1.5,
  rowGap: 0.5,
  minHeight: 30,
  pt: 1.25,
  borderTop: "1px solid",
  borderColor: "divider",
};

// "Try one now" on the Hub home: one entry to start with, its whole run
// command with copy, one line on what it is and a way to its page. That
// line is the maintainer's why when the entry is this week's pick (Start
// here then leaves it out), else its tagline; never both. With no command
// to show, the box ("Start with this one") still names the entry, its title
// a link to it. The foot holds the same action as the entry's card
// (EntryAction: Use with both commands once they can run, else Get the
// files). While the entry is being chosen (`loading`), placeholders hold
// the box's place, so the control row below does not move when it arrives.
export function TryOneNow({ entry, why, loading = false }) {
  const commands = useEntryCommands(entry);
  if (loading) {
    return (
      <Box aria-hidden="true" data-testid="hub-try-one-loading" sx={TRY_BOX_SX}>
        <Skeleton variant="text" width={88} sx={{ fontSize: 13 }} />
        <Skeleton variant="rounded" height={40} />
        <Skeleton variant="text" width="85%" sx={{ fontSize: 11 }} />
        <Box sx={{ ...TRY_FOOT_SX, justifyContent: "flex-end" }}>
          <Skeleton variant="rounded" width={96} height={26} />
        </Box>
      </Box>
    );
  }
  if (!entry?.ref) return null;
  const href = hubPaths.entry(entry.publisher?.handle, entry.name);
  const title = entry.title || entry.name;
  const said = why ? `Maintainer pick: ${why}` : entry.tagline;
  const actionable = hasEntryAction(entry);
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
      {said && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid={why ? "hub-try-one-why" : "hub-try-one-tagline"}
        >
          {said}
        </Typography>
      )}
      {(commands || entry.noKeyNeeded || actionable) && (
        <Box data-testid="hub-try-one-foot" sx={TRY_FOOT_SX}>
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
          {actionable && (
            <Box sx={{ ml: "auto", display: "inline-flex" }}>
              <EntryAction entry={entry} />
            </Box>
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
