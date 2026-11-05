import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Tab, Typography } from "@mui/material";
import CodeBlock from "src/components/code-block";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { RL_CLIENTS, RL_ISOLATION, RL_SCALE } from "../_mock/release";

/*
  RL access at scale (T46): how many sessions run at once, that they never
  share a world, and what they cost. Read the same on an environment's
  Release panel and on a catalogue world.
*/
export function RlScaleFacts({ running = 0, credits = null, action }) {
  const rows = [
    [
      "Sessions at once",
      `Up to ${RL_SCALE.limit} on this environment${running ? ` · ${running} running now` : ""}`,
    ],
    [
      "Isolation",
      <>
        <Box component="span" sx={{ color: "status.success.fg" }}>
          Checked
        </Box>
        {` — ${RL_ISOLATION}`}
      </>,
    ],
    [
      "Credits",
      `${RL_SCALE.creditsPerSession} per session, from the same credits as simulations${credits != null ? ` · ${credits.toLocaleString()} used` : ""}`,
    ],
  ];
  return (
    <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}>
      {rows.map(([k, v], i) => (
        <Stack
          key={k}
          direction="row"
          spacing={2}
          sx={{
            px: 1.5,
            py: 0.75,
            borderTop: i ? 1 : 0,
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s2",
              color: "text.tertiary",
              width: 130,
              flexShrink: 0,
            }}
          >
            {k}
          </Typography>
          <Typography sx={{ typography: "s2", minWidth: 0 }}>{v}</Typography>
        </Stack>
      ))}
      {action && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ px: 1.5, py: 1, borderTop: 1, borderColor: "divider" }}
        >
          {action}
        </Stack>
      )}
    </Box>
  );
}
RlScaleFacts.propTypes = {
  running: PropTypes.number,
  credits: PropTypes.number,
  action: PropTypes.node,
};

/* The trainer's calls, on our SDK or on any ORS client. */
export function RlClientCode({ sdk, ors }) {
  const [client, setClient] = useState("sdk");
  return (
    <Box>
      <SegmentedTabs
        value={client}
        onChange={(_, v) => setClient(v)}
        sx={{ mb: 1 }}
      >
        {RL_CLIENTS.map((c) => (
          <Tab key={c.id} value={c.id} label={c.label} />
        ))}
      </SegmentedTabs>
      <CodeBlock code={client === "ors" ? ors : sdk} />
    </Box>
  );
}
RlClientCode.propTypes = {
  sdk: PropTypes.string.isRequired,
  ors: PropTypes.string.isRequired,
};
