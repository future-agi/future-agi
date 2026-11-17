/* eslint-disable react-refresh/only-export-components */
import React, { useId } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { useUploadsOffAudience } from "../lib/uploadsChecked";

import HubIcon from "./HubIcon";
import HubPanel from "./HubPanel";

// Uploads on a server with no Hub checker configured (OMEGA_HUB_CHECKER_TOKEN
// unset). That is a configuration error, so the Publish page stays and says
// what is missing instead of hiding. The server refuses such an upload with
// 503 OMEGA_HUB_CHECKER_NOT_CONFIGURED and stores nothing (omega_hub/
// services/intake.py). Everyone reads one plain sentence first, in a notice
// laid out with the rest of the work area. What follows depends on who reads
// it (useUploadsOffAudience, lib/uploadsChecked.js): on a server confirmed
// self-hosted, an organization owner or admin then reads the setup, step by
// step, under "For administrators", ending with a link to the self-host
// docs, and anyone else reads the sentence alone, which asks them to send
// their administrator; on Cloud, and while the server's mode is not known,
// everyone reads only that uploads are off.

/** The server's code for an upload no checker could ever check. */
export const CHECKER_NOT_CONFIGURED = "OMEGA_HUB_CHECKER_NOT_CONFIGURED";

/** An organization owner or admin (lib/uploadsChecked.js). */
export { isAdminRole } from "../lib/uploadsChecked";

/**
 * What is off, said alone: on Cloud, and while the server's mode is not
 * known.
 */
export const UPLOADS_OFF_LINE = "Uploads are off on this server.";

const ASK_ADMIN = "Ask your administrator to start the Hub checker.";

/**
 * The sentence on a self-hosted server, where an administrator starts the
 * checker.
 */
export const UPLOADS_OFF = `${UPLOADS_OFF_LINE} ${ASK_ADMIN}`;

// The self-host docs, at the address the app's first-run checks link to.
const SELF_HOST_DOCS = "https://docs.futureagi.com/docs/self-hosting";

// The setup, in the order an administrator does it. A name to type sits
// between backticks and shows as code.
const ADMIN_STEPS = Object.freeze([
  "Make sure `OMEGA_HUB_CHECKER_TOKEN` is set on the server. The Hub checker uses the same token.",
  "On Docker Compose, make sure `hub` is in `COMPOSE_PROFILES` in `.env`. The server does not read a `--profile` flag; it reads only that setting.",
  "Restart the server. Uploads work once it runs with both settings.",
]);

// Keyed by UPLOADS_OFF_AUDIENCE (lib/uploadsChecked.js).
export const UPLOADS_UNAVAILABLE = Object.freeze({
  admin: Object.freeze({
    title: UPLOADS_OFF,
    heading: "For administrators",
    intro: "Until it runs, the server refuses every upload and stores nothing.",
    steps: ADMIN_STEPS,
    docs: Object.freeze({ label: "Self-host docs", href: SELF_HOST_DOCS }),
  }),
  member: Object.freeze({
    title: UPLOADS_OFF,
    heading: null,
    intro: null,
    steps: null,
    docs: null,
  }),
  everyone: Object.freeze({
    title: UPLOADS_OFF_LINE,
    heading: null,
    intro: null,
    steps: null,
    docs: null,
  }),
});

/**
 * The words for `audience` (uploadsOffAudience); an audience the words do not
 * know reads the short line.
 */
export const uploadsUnavailableCopy = (audience) =>
  UPLOADS_UNAVAILABLE[audience] || UPLOADS_UNAVAILABLE.everyone;

/** The words for this person on this server. */
export function useUploadsUnavailableCopy() {
  return uploadsUnavailableCopy(useUploadsOffAudience());
}

const plain = (text) => text.replace(/`/g, "");

/** In an error alert (an upload the server refused with CHECKER_NOT_CONFIGURED). */
export function UploadsUnavailableText() {
  const copy = useUploadsUnavailableCopy();
  const setup = copy.steps
    ? ` ${copy.heading}: ${[copy.intro, ...copy.steps].map(plain).join(" ")}`
    : "";
  return (
    <Box component="span" data-testid="hub-uploads-unavailable-text">
      {copy.title}
      {setup}
    </Box>
  );
}

const CODE_SX = {
  fontFamily: "monospace",
  fontSize: 12,
  color: "text.primary",
  bgcolor: "background.neutral",
  borderRadius: 0.5,
  px: 0.5,
};

// A step's words, each name between backticks shown as code.
function StepText({ text }) {
  return text.split("`").map((piece, index) =>
    index % 2 ? (
      <Box key={index} component="code" sx={CODE_SX}>
        {piece}
      </Box>
    ) : (
      <React.Fragment key={index}>{piece}</React.Fragment>
    ),
  );
}

StepText.propTypes = { text: PropTypes.string.isRequired };

// The docs link keeps its own width under the steps, its external-link mark
// a small gap after the words.
const DOCS_LINK_SX = {
  alignSelf: "flex-start",
  display: "inline-flex",
  alignItems: "center",
  gap: 0.5,
  mt: 0.5,
};

/**
 * The setup under "For administrators", for an owner or admin of a
 * self-hosted server: the steps, then the self-host docs. `headingComponent`
 * keeps the page's heading order (an h3 in Publish's panel, an h2 on My
 * submissions).
 */
export function UploadsSetup({ copy, headingComponent = "h3" }) {
  const headingId = useId();
  return (
    <Box
      component="section"
      aria-labelledby={headingId}
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        maxWidth: 760,
      }}
    >
      <Typography
        id={headingId}
        component={headingComponent}
        typography="s1"
        fontWeight="fontWeightSemiBold"
        color="text.primary"
      >
        {copy.heading}
      </Typography>
      <Typography typography="s1" color="text.secondary">
        {copy.intro}
      </Typography>
      <Box
        component="ol"
        aria-labelledby={headingId}
        sx={{
          m: 0,
          pl: 2.5,
          display: "grid",
          gap: 0.75,
          typography: "s1",
          color: "text.secondary",
        }}
      >
        {copy.steps.map((step) => (
          <Box component="li" key={step} sx={{ pl: 0.5 }}>
            <StepText text={step} />
          </Box>
        ))}
      </Box>
      {copy.docs && (
        <Link
          href={copy.docs.href}
          target="_blank"
          rel="noopener noreferrer"
          color="text.link"
          underline="always"
          typography="s1"
          sx={DOCS_LINK_SX}
        >
          {copy.docs.label}
          <HubIcon name="external" size={12} />
        </Link>
      )}
    </Box>
  );
}

UploadsSetup.propTypes = {
  copy: PropTypes.shape({
    heading: PropTypes.string.isRequired,
    intro: PropTypes.string,
    steps: PropTypes.arrayOf(PropTypes.string).isRequired,
    docs: PropTypes.shape({
      label: PropTypes.string.isRequired,
      href: PropTypes.string.isRequired,
    }),
  }).isRequired,
  headingComponent: PropTypes.oneOf(["h2", "h3"]),
};

/** In place of the upload card and the terminal block, while the server reports no checker. */
export default function UploadsUnavailable() {
  const copy = useUploadsUnavailableCopy();
  return (
    <HubPanel title="Upload a folder" testId="hub-uploads-unavailable">
      <Alert severity="info" role="status" data-testid="hub-uploads-off">
        {copy.title}
      </Alert>
      {copy.steps && <UploadsSetup copy={copy} />}
    </HubPanel>
  );
}
