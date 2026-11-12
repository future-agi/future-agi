/* eslint-disable react-refresh/only-export-components */
import React from "react";
import Box from "@mui/material/Box";

import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";

import { HubSection, HubState } from "./HubStates";

// Uploads on a server with no Hub checker configured (OMEGA_HUB_CHECKER_TOKEN
// unset). That is a configuration error, so the Publish page stays and says
// what is missing instead of hiding ("DON'T
// hide Studio/Hub, finish them"). The server refuses such an upload with 503
// OMEGA_HUB_CHECKER_NOT_CONFIGURED and stores nothing (omega_hub/services/
// intake.py). An organization owner or admin gets the operator's message,
// which names the setting. Anyone else is told to ask their administrator.

/** The server's code for an upload no checker could ever check. */
export const CHECKER_NOT_CONFIGURED = "OMEGA_HUB_CHECKER_NOT_CONFIGURED";

/** An organization owner or admin (accounts/omega_session.py maps them to Omega owner and admin). */
export const isAdminRole = (role) => role === "Owner" || role === "Admin";

export const UPLOADS_UNAVAILABLE = Object.freeze({
  admin: Object.freeze({
    title: "This server has no Hub checker configured",
    detail:
      "So it refuses uploads and stores nothing. Make sure OMEGA_HUB_CHECKER_TOKEN is set on the server and, on Docker Compose, that hub is in COMPOSE_PROFILES in .env (a --profile flag alone is not seen by the server); the checker uses the same token. Uploads open once the server restarts with both.",
  }),
  member: Object.freeze({
    title: "Uploads are not available on this server: ask your administrator.",
    detail: null,
  }),
});

export const uploadsUnavailableCopy = (role) =>
  isAdminRole(role) ? UPLOADS_UNAVAILABLE.admin : UPLOADS_UNAVAILABLE.member;

/** The words for this person: the operator's for an owner or admin, else "ask your administrator". */
export function useUploadsUnavailableCopy() {
  const { role } = useSafeAuthContext();
  return uploadsUnavailableCopy(role);
}

/** In an error alert (an upload the server refused with CHECKER_NOT_CONFIGURED). */
export function UploadsUnavailableText() {
  const copy = useUploadsUnavailableCopy();
  return (
    <Box component="span" data-testid="hub-uploads-unavailable-text">
      {copy.title}
      {copy.detail ? ` ${copy.detail}` : ""}
    </Box>
  );
}

/** In place of the upload card and the terminal block, while the server reports no checker. */
export default function UploadsUnavailable() {
  const copy = useUploadsUnavailableCopy();
  return (
    <HubSection title="Upload a folder" testId="hub-uploads-unavailable">
      <HubState compact icon="info" title={copy.title} description={copy.detail ?? undefined} />
    </HubSection>
  );
}
