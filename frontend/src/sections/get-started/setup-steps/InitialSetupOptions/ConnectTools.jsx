import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import BrandMark from "src/sections/simulate-v2/integrations/BrandMark";
import GithubInstallDialog from "src/sections/simulate-v2/integrations/GithubInstallDialog";
import SlackInstallDialog from "src/sections/simulate-v2/integrations/SlackInstallDialog";
import { githubAccountsLabel, installedRepos, useIntegrations } from "src/sections/simulate-v2/_mock/integrations";
import HeaderContent from "./HeaderContent";

/* First-run nudge for the workspace tools — optional, never a gate. */
const ConnectTools = () => {
  const integrations = useIntegrations();
  const { github, slack, slackRequest } = integrations;
  const [gh, setGh] = useState(false);
  const [sl, setSl] = useState(false);

  const rows = [
    {
      id: "github", title: "GitHub",
      body: "Connect agents straight from your code, version them from commits, and get fixes as pull requests.",
      done: github ? `Installed on ${githubAccountsLabel(integrations)} · ${installedRepos(integrations).length} repositor${installedRepos(integrations).length === 1 ? "y" : "ies"}` : null,
      action: "Install GitHub App",
      open: () => setGh(true),
    },
    {
      id: "slack", title: "Slack",
      body: "Hear about runs, regressions and release blockers in your team's channels — and approve fixes there.",
      done: slack ? `${slack.workspace} · ${slack.defaultChannel}` : null,
      pending: !slack && slackRequest?.status === "pending" ? `Waiting for an admin of ${slackRequest.workspace} to approve` : null,
      action: "Add to Slack",
      open: () => setSl(true),
    },
  ];

  return (
    <Box>
      <HeaderContent
        title="Connect your tools"
        description="Optional — you can do this any time from Settings → Integrations."
      />
      <Stack spacing={1.5} sx={{ mt: 2.5, maxWidth: 720 }}>
        {rows.map((r) => (
          <Stack key={r.id} direction="row" alignItems="center" spacing={2} sx={{ p: 2, borderRadius: 1.5, border: "1px solid", borderColor: "divider" }}>
            <BrandMark brand={r.id} size={40} />
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s1", fontWeight: 600 }}>{r.title}</Typography>
              <Typography sx={{ typography: "s2", color: r.pending && !r.done ? "#CA8A04" : "text.secondary" }}>{r.done || r.pending || r.body}</Typography>
            </Box>
            {r.done ? (
              <Stack direction="row" alignItems="center" spacing={0.5} sx={{ color: "#16A34A" }}>
                <Iconify icon="solar:check-circle-bold" width={16} />
                <Typography sx={{ typography: "s2", fontWeight: 600, color: "inherit" }}>Installed</Typography>
              </Stack>
            ) : (
              <Button variant="outlined" size="small" onClick={r.open}>{r.action}</Button>
            )}
          </Stack>
        ))}
      </Stack>
      <GithubInstallDialog open={gh} onClose={() => setGh(false)} />
      <SlackInstallDialog open={sl} onClose={() => setSl(false)} />
    </Box>
  );
};

export default ConnectTools;
