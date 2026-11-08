/*
  Demo mode, first thing: what the app reads as its modules load — the demo
  session, and the GitHub app installed for the demo's code agents (Settings
  → Integrations can still remove it). Imported first in main.jsx so it runs
  before any of them. Does nothing unless VITE_DEMO_MODE=true.
*/
if (import.meta.env.VITE_DEMO_MODE === "true") {
  localStorage.setItem("accessToken", "demo-token");
  localStorage.setItem("refreshToken", "demo-token");
  /* Once per browser — including ones that saved their own GitHub state on an
     earlier visit — so a later removal in Settings still sticks. */
  if (!localStorage.getItem("fagi.demo.github.v1")) {
    const KEY = "fagi.proto.integrations.v1";
    const inst = (account, kind = "Organization") => ({ id: `inst_${account}`, account, kind, access: "all", repos: [], installedAt: "2026-10-15T09:00:00.000Z", installedBy: "you", githubAccess: "read" });
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(KEY)) || {}; } catch { /* start fresh */ }
    const accounts = [["acme-inc"], ["skyline-air"], ["acme"], ["acme-labs"], ["you", "Personal account"]];
    const have = saved.github?.installations || [];
    const installations = [...have, ...accounts.filter(([a]) => !have.some((i) => i.account === a)).map(([a, k]) => inst(a, k))];
    localStorage.setItem(KEY, JSON.stringify({ ...saved, github: { ...(saved.github || {}), installations } }));
    localStorage.setItem("fagi.demo.github.v1", "1");
  }
}
