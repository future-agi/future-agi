// Why a role is not offered a Linear action on an Error Feed issue.
// Connecting Linear is an integration change, open to an Owner, Admin or
// workspace Admin (futureagi/integrations/permissions.py, as Settings >
// Integrations offers it); creating an issue needs write access to the
// workspace, which a Viewer does not have (the API answers 403).
export const LINEAR_CONNECT_ADMIN_ONLY =
  "Only an Owner or Admin can connect Linear.";
export const LINEAR_CREATE_VIEWER = "Viewers cannot create issues.";
