/*
  Demo mode, first thing: what the app reads as its modules load — the demo
  session, and the GitHub app installed for the demo's code agents (Settings
  → Integrations can still remove it). Imported first in main.jsx so it runs
  before any of them. Does nothing unless VITE_DEMO_MODE=true.
*/
if (import.meta.env.VITE_DEMO_MODE === "true") {
  localStorage.setItem("accessToken", "demo-token");
  localStorage.setItem("refreshToken", "demo-token");
  if (!localStorage.getItem("fagi.proto.integrations.v1")) {
    const inst = (account, kind = "Organization") => ({ id: `inst_${account}`, account, kind, access: "all", repos: [], installedAt: "2026-10-15T09:00:00.000Z", installedBy: "you", githubAccess: "read" });
    localStorage.setItem("fagi.proto.integrations.v1", JSON.stringify({ github: { installations: [inst("acme-inc"), inst("skyline-air"), inst("acme"), inst("acme-labs"), inst("you", "Personal account")] } }));
  }
}
