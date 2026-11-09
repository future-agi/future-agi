// What the console says when a sign-in needs the organization's own app
// registration with the vendor (W4b, G14b). FutureAGI's own Gmail, Google
// Calendar, Outlook and Teams connectors sign people in only through the
// organization's own Google or Microsoft OAuth app; an Arcade gateway is the
// customer's own Arcade project. The page never claims more than that: a
// registration is the organization's to make, and nothing here makes one.

const VENDORS = { google: "Google", microsoft: "Microsoft", arcade: "Arcade" };

// The vendor whose app registration a client group needs, or null.
export function registrationVendor(clientGroup) {
  return Object.prototype.hasOwnProperty.call(VENDORS, clientGroup)
    ? VENDORS[clientGroup]
    : null;
}

// Arcade's own limits, as the customer must set their project up (partners
// §3.1, §4.1): stated wherever an Arcade gateway is offered.
export const ARCADE_REQUIREMENTS =
  "It needs your own Arcade gateway with OAuth gateway authentication and Tool Recommendation turned off. Arcade's default OAuth apps require each end user to be a member of your Arcade project; for many end users, bring your own OAuth apps and a custom user verifier.";

// Why Connect waits for the organization's own app, in the app's drawer an
// Owner or Admin reads (only they may add it, below).
export function registrationHold(appName, clientGroup) {
  const vendor = registrationVendor(clientGroup);
  if (vendor === "Arcade") {
    return `${appName} requires your Arcade app registration. ${ARCADE_REQUIREMENTS}`;
  }
  if (!vendor) {
    return `${appName} signs in only through your organization's own OAuth app. Add it below to connect.`;
  }
  return `${appName} requires your organization's own ${vendor} OAuth app registration; Future AGI's ${vendor} app is not used here. Add it below to connect.`;
}
