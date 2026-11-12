// Every sentence the Studio tab shows, in one place. Plain words, no promises
// the page cannot keep.
import { LINK_PROBLEM, serverDisplayName } from "./connect-link";

export const STUDIO_BASE_PATH = "/dashboard/studio";
export const STUDIO_PROBE_PATH = "/studio/v1/openapi.json";
// How long the frame may take to say hello before the page looks for the
// cause. Studio says hello as soon as its script runs, well before this; the
// bound plus one loopback probe keeps the cause on screen within 6 s. A hello
// that arrives later still connects.
export const STUDIO_HELLO_TIMEOUT_MS = 4500;
export const STUDIO_LABEL = "local Studio";

// The command that starts Studio so this page may frame it. --no-open keeps
// omega dev from opening a second, local Studio tab and makes it print its
// link (with the key) for this page instead.
export const omegaDevCommand = (origin) =>
  `omega dev --no-open --embed-origin ${origin}`;

export const PAGE = {
  title: "Studio",
  description:
    "Run and debug the Omega project on your computer, right here in Future AGI.",
};

export const CONNECT = {
  title: "Connect to omega dev",
  body: "Paste the link omega dev printed in your terminal.",
  placeholder: "http://127.0.0.1:4191/#t=…",
  action: "Connect",
  localNetwork:
    "Your browser may ask to let this site reach apps on this device. Allow it, then connect.",
};

export const LINK_PROBLEM_COPY = {
  [LINK_PROBLEM.empty]: "Paste the link omega dev printed.",
  [LINK_PROBLEM.invalid]:
    "That doesn't look like a link from omega dev. Copy the whole line it printed.",
  [LINK_PROBLEM.notLocal]:
    "Studio runs on this computer, so its address starts with http://127.0.0.1 or http://localhost.",
};

export const SAVED = {
  title: "Saved connections",
  // A saved row is an address and a name; the key never is. Said before the
  // person clicks, so Connect asking for the link is no surprise.
  note: "Remembers each server's name and address, not its key. Connecting asks for the link omega dev printed.",
  connect: "Connect",
  rename: "Rename",
  remove: "Remove",
  save: "Save",
  cancel: "Cancel",
};

export const START = {
  title: "Start omega dev",
  stepRun: "In your Omega project, run:",
  project:
    "An Omega project is a folder with an omega.studio.json, omega.workflow.json or omega.agent.json file.",
  stepOpen:
    "Paste the link it prints (it starts with http://127.0.0.1) in the box above and press Connect. If it prints an “Open in Future AGI” link, opening that link connects you too.",
  stepSignIn:
    "If Future AGI asks you to sign in first, sign in, then open the “Open in Future AGI” link again.",
  docsLink: "Read the Studio guide",
  privacy:
    "The link carries your project's key, kept in .omega/studio.key. Links keep working across omega dev restarts at the same address and port while that key stays unchanged. To stop older links opening the next server, stop omega dev and run omega dev --new-key. Other instances still running keep their keys. Future AGI hands the key to Studio inside this page; the tab keeps it in memory, not in saved connections, and does not send it to Future AGI servers.",
};

// Step 1. The command is built from the one value the release sets
// (release-config.js); a build that names no package shows no install step.
export const INSTALL = {
  step: "Install the Omega CLI, which provides omega dev:",
  requirement: "Needs Node.js 22.13 or later.",
};

// Why a framed Studio never said hello, in Studio's words; the command, when
// there is one, is shown under the sentence with a copy button.
export const DIAGNOSIS_COPY = {
  framingBlocked: (server) => ({
    title: "Studio didn't open inside Future AGI",
    body: `${server} answered, but Studio did not connect inside this page. It may not allow this site to frame it. Stop it and start it again with:`,
  }),
  notRunning: (server) => ({
    title: `Can't reach Studio at ${server}`,
    body: "Start Studio from your Omega project with the command below. If it is running, check the address and port it printed.",
  }),
  // Added to the shared Safari words: Safari blocks a local http page inside
  // https Future AGI, while Chrome and Firefox connect to it here.
  safariAlternative:
    "Local Studio connects inside Future AGI in Chrome and Firefox.",
};

// Why this tab holds no key for the Studio it shows, in terms of the link the
// person handles. The key lives only in memory, so where the connection came
// from says what happened to it:
//   saved     a saved row: an address and a name, never a key
//   restored  this tab was reloaded, or left and opened again after a pasted
//             or saved connection
//   link      the "Open in Future AGI" link was already used, so the tab was
//             left and opened again
//   pasted    a bare address, with no key in it
// A connection that did hold a key (hasKey) lost it inside Studio.
export function needsLinkReason({ source, hasKey } = {}) {
  if (hasKey)
    return "Studio no longer has the key for this tab, so it needs omega dev's link again.";
  switch (source) {
    case "saved":
      return "A saved connection keeps the name and address, not the key, so this tab needs omega dev's link again.";
    case "restored":
      return "After a reload or a visit to another page, this tab needs omega dev's link again.";
    case "link":
      return "After leaving the Studio page, this tab needs omega dev's link again.";
    case "pasted":
      return "That address has no key. This tab needs the whole link omega dev printed, with #t= and the key.";
    default:
      return "This tab needs omega dev's link again.";
  }
}

const REFUSED_REASON =
  "The link may be for another omega dev, or for a key it no longer uses.";

// The gate panel's needs-key words for one connection.
export const needsKeyGateCopy = (connection) => ({
  title: "Local Studio needs its key",
  body: `${needsLinkReason(connection)} Paste it, or open the “Open in Future AGI” link omega dev printed.`,
});

export const GATE_COPY = {
  connecting: "Connecting to local Studio…",
  // A key was handed over; local Studio is asking its own server about it.
  checking: "Checking the key with local Studio…",
  // The one step that can work once the key is gone: Reconnect cannot bring
  // it back, so the panel's button goes to the paste field instead.
  pasteLink: "Paste the link",
  needsKey: needsKeyGateCopy(),
  refused: {
    title: "Local Studio didn't accept the key",
    body: `${REFUSED_REASON} Paste the newest link from the omega dev you want, or open its “Open in Future AGI” link.`,
  },
};

export const TOOLBAR = {
  openLocal: "Open local Studio",
  disconnect: "Disconnect",
  status: {
    connecting: "Connecting…",
    checking: "Checking the key…",
    diagnosing: "Connecting…",
    connected: "Connected",
    "needs-key": "Needs the link",
    refused: "Link not accepted",
    timeout: "Not connected",
  },
};

// "Support desk (127.0.0.1:4191)" for a renamed saved server, else
// "Studio at 127.0.0.1:4191".
function serverName(notice) {
  const host = notice.url ? serverDisplayName(notice.url) : null;
  if (!host) return "Studio";
  if (notice.label && notice.label !== host) return `${notice.label} (${host})`;
  return `Studio at ${host}`;
}

// Pasting the link on the connect screen reopens the page the address bar
// still names; said only when there is such a page.
const returnLine = (notice) =>
  notice.returnTo ? " Pasting it here reopens the page you were on." : "";

// Shown on the connect screen after a connection had to be closed.
export function noticeCopy(notice) {
  if (!notice) return null;
  const where = serverName(notice);
  switch (notice.kind) {
    case "needs-key":
      return {
        severity: "info",
        title: `${where} needs its key`,
        body: `${needsLinkReason(notice)} Paste it below, or open the “Open in Future AGI” link omega dev printed.${returnLine(notice)}`,
      };
    case "refused":
      return {
        severity: "warning",
        title: `${where} didn't accept the key`,
        body: `${REFUSED_REASON} Below, paste the newest link from the omega dev you want.${returnLine(notice)}`,
      };
    case "not-local":
      return {
        severity: "warning",
        title: "That link doesn't point to this computer",
        body: LINK_PROBLEM_COPY[LINK_PROBLEM.notLocal],
      };
    default:
      return null;
  }
}
