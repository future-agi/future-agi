import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  Slack palette per theme. Dark uses Slack Dark's aubergine sidebar
  (#19171D) + dark main pane (#1A1D21) + light text; light uses Slack
  Aubergine (the default) — matches what a user opening Slack now
  actually sees in their own theme.
*/
function slackPalette(dark) {
  return dark ? {
    mainBg: "#1A1D21", mainFg: "#D1D2D3",
    topBg: "#19171D", topFg: "#F4EDE4",
    channelBg: "#19171D", channelFg: "#D1D2D3",
    channelHead: "rgba(255,255,255,0.9)",
    channelBorder: "rgba(255,255,255,0.08)",
    subFg: "#ABABAD",
    divider: "#2C2D30",
    composerBorder: "#3A3B3F",
    composerBg: "#222529",
    inputBg: "rgba(255,255,255,0.08)",
    placeholder: "#8D8D8D",
  } : {
    mainBg: "#FFFFFF", mainFg: "#1D1C1D",
    topBg: "#3F0E40", topFg: "#F4EDE4",
    channelBg: "#4A154B", channelFg: "#F4EDE4",
    channelHead: "#F4EDE4",
    channelBorder: "rgba(255,255,255,0.08)",
    subFg: "#616061",
    divider: "#E8E8E8",
    composerBorder: "#DDDDDD",
    composerBg: "#FFFFFF",
    inputBg: "rgba(255,255,255,0.15)",
    placeholder: "#8A8A8A",
  };
}

/**
 * A live-looking mock of the Slack workspace surface, rendered inline
 * as the star of the Twin detail page. Not the real Slack — a faithful
 * enough facsimile that a viewer immediately understands "your agent
 * calls into a real sandbox that renders this way." Everything below
 * is presentational: workspace name, channel rail, DMs, message
 * composer, top nav — no state, no interactivity beyond the visible
 * chrome. Interactive Slack lives behind the "Open surface" button.
 *
 * The palette is Slack's — aubergine sidebar (#3F0E40), red DM
 * indicators — because rendering these in the app's own theme would
 * make it read as "our reskin of Slack" rather than "a sandbox
 * running the real thing".
 */
import { liveSandboxContentFor } from "../_mock/twins";

export default function SlackSandboxMock({ workspace = "Default Workspace", envState }) {
  const dark = useTheme().palette.mode === "dark";
  const c = slackPalette(dark);
  const live = liveSandboxContentFor(envState, "slack");
  const channelPosts = live.filter((m) => m.kind === "post" && m.channel === "#general");
  const urgentPosts = live.filter((m) => m.kind === "post" && m.channel === "#support-urgent");
  const hasActivity = live.length > 0;
  return (
    <Box sx={{
      borderRadius: 1.5, overflow: "hidden",
      border: "1px solid", borderColor: "divider",
      bgcolor: c.mainBg, color: c.mainFg,
      height: 800, display: "flex", flexDirection: "column",
    }}>
      {/* top: search bar + notification/help/user rail */}
      <Stack
        direction="row" alignItems="center" spacing={1.25}
        sx={{
          height: 40, px: 1.5, flexShrink: 0,
          bgcolor: c.topBg, color: c.topFg,
        }}
      >
        <Stack direction="row" spacing={0.75}>
          <NavIcon icon="solar:alt-arrow-left-linear" />
          <NavIcon icon="solar:alt-arrow-right-linear" />
        </Stack>
        <Box sx={{
          flex: 1, maxWidth: 520, height: 24, borderRadius: 0.75,
          bgcolor: "rgba(255,255,255,0.15)", display: "flex",
          alignItems: "center", px: 1, gap: 0.75,
        }}>
          <Iconify icon="solar:magnifer-linear" width={11} sx={{ color: "rgba(255,255,255,0.7)" }} />
          <Typography sx={{ fontSize: 11, color: "rgba(255,255,255,0.85)" }}>
            Search {workspace}
          </Typography>
        </Box>
        <Box flex={1} />
        <NavIcon icon="solar:clock-circle-linear" />
        <NavIcon icon="solar:question-circle-linear" />
        <Box sx={{
          width: 22, height: 22, borderRadius: "50%",
          bgcolor: "#E8912D",
        }} />
      </Stack>

      <Stack direction="row" sx={{ flex: 1, minHeight: 0 }}>
        {/* left rail — icon nav */}
        <Stack
          spacing={2} alignItems="center"
          sx={{
            width: 60, py: 1.5, flexShrink: 0,
            bgcolor: c.topBg, color: c.topFg,
          }}
        >
          <RailButton icon="solar:home-2-linear" label="Home" active />
          <RailButton icon="solar:chat-round-line-linear" label="DMs" />
          <RailButton icon="solar:bell-linear" label="Activity" />
          <RailButton icon="solar:folder-linear" label="Files" />
          <RailButton icon="solar:menu-dots-linear" label="More" />
        </Stack>

        {/* channels rail */}
        <Stack
          sx={{
            width: 220, flexShrink: 0,
            bgcolor: c.channelBg, color: c.channelFg,
          }}
        >
          <Stack
            direction="row" alignItems="center" spacing={1}
            sx={{ px: 1.5, py: 1.25, borderBottom: `1px solid ${c.channelBorder}` }}
          >
            <Typography sx={{ fontSize: 13, fontWeight: 700, flex: 1, color: c.channelHead }} noWrap>
              {workspace}
            </Typography>
            <Iconify icon="solar:pen-linear" width={11} sx={{ color: "rgba(255,255,255,0.7)" }} />
          </Stack>

          <SidebarSection label="Channels" items={["general", "support-urgent", "product-updates"]} prefix="#" c={c} />
          <SidebarSection label="Direct messages" items={["Slack Clone Bot", "Slack Clone User"]} avatar c={c} />
        </Stack>

        {/* main pane */}
        <Stack sx={{ flex: 1, minWidth: 0, bgcolor: c.mainBg }}>
          <Stack
            direction="row" alignItems="center" spacing={1}
            sx={{ px: 2, py: 1.25, borderBottom: `1px solid ${c.divider}` }}
          >
            <Iconify icon="solar:hashtag-linear" width={13} sx={{ color: c.subFg }} />
            <Typography sx={{ fontSize: 13, fontWeight: 700, color: c.mainFg }}>general</Typography>
            <Typography sx={{ fontSize: 11, color: c.subFg }}>
              · No topic set
            </Typography>
            <Box flex={1} />
            <Iconify icon="solar:users-group-two-rounded-linear" width={13} sx={{ color: c.subFg }} />
            <Typography sx={{ fontSize: 11, color: c.subFg }}>2</Typography>
          </Stack>

          {hasActivity ? (
            <Stack sx={{ flex: 1, overflow: "auto", py: 1, px: 2, gap: 1.5 }}>
              {/*
                Live agent messages, most-recent first. Each row mirrors
                Slack's own message shape (avatar + name + timestamp + body)
                so the sandbox reads as though the agent posted into a real
                workspace. `runLabel` renders as the timestamp — it's the
                real handle a user can chase back into a run.
              */}
              {channelPosts.length === 0 && urgentPosts.length === 0 && (
                <Stack alignItems="center" justifyContent="center" spacing={0.75} sx={{ flex: 1, color: c.subFg }}>
                  <Iconify icon="solar:chat-round-line-linear" width={22} sx={{ color: c.subFg }} />
                  <Typography sx={{ fontSize: 12, fontWeight: 600 }}>
                    Agent activity landed in other channels — {live.length} write{live.length === 1 ? "" : "s"} this session.
                  </Typography>
                </Stack>
              )}
              {[...channelPosts, ...urgentPosts].map((m, i) => (
                <Stack key={m.id} direction="row" spacing={1.25} alignItems="flex-start">
                  <Box sx={{
                    width: 30, height: 30, borderRadius: 0.5, flexShrink: 0,
                    bgcolor: "#7857FC", color: "#FFFFFF",
                    display: "grid", placeItems: "center", fontSize: 11, fontWeight: 700,
                  }}>A</Box>
                  <Box flex={1} minWidth={0}>
                    <Stack direction="row" alignItems="baseline" spacing={0.75}>
                      <Typography sx={{ fontSize: 12.5, fontWeight: 700, color: c.mainFg }}>{m.author}</Typography>
                      <Typography sx={{ fontSize: 9.5, fontWeight: 600, color: "#7857FC", textTransform: "uppercase", letterSpacing: 0.4 }}>
                        APP
                      </Typography>
                      <Typography sx={{ fontSize: 11, color: c.subFg }}>{m.channel} · {m.runLabel}</Typography>
                    </Stack>
                    <Typography sx={{ fontSize: 12.5, color: c.mainFg, mt: 0.125 }}>{m.text}</Typography>
                  </Box>
                </Stack>
              ))}
            </Stack>
          ) : (
            <Box sx={{
              flex: 1, display: "grid", placeItems: "center",
              color: c.subFg,
            }}>
              <Stack alignItems="center" spacing={0.75}>
                <Iconify icon="solar:chat-round-line-linear" width={22} sx={{ color: c.subFg }} />
                <Typography sx={{ fontSize: 12, fontWeight: 600 }}>
                  No messages in this channel yet.
                </Typography>
                <Typography sx={{ fontSize: 11, color: c.placeholder }}>
                  Sandbox seeded per run — messages appear once your agent posts them.
                </Typography>
              </Stack>
            </Box>
          )}

          {/* composer */}
          <Box sx={{
            m: 1.5, borderRadius: 1,
            border: `1px solid ${c.composerBorder}`, bgcolor: c.composerBg,
          }}>
            <Stack
              direction="row" spacing={1} sx={{ px: 1, py: 0.5, borderBottom: `1px solid ${c.divider}` }}
            >
              <ComposerIcon char="B" c={c} />
              <ComposerIcon char="I" italic c={c} />
              <ComposerIcon char="S" strike c={c} />
              <Iconify icon="solar:link-linear" width={12} sx={{ color: c.subFg }} />
            </Stack>
            <Typography sx={{ fontSize: 12, color: c.placeholder, px: 1.25, py: 1 }}>
              Message #general
            </Typography>
            <Stack
              direction="row" alignItems="center" spacing={0.75}
              sx={{ px: 1, py: 0.5, borderTop: `1px solid ${c.divider}` }}
            >
              <Iconify icon="solar:emoji-funny-square-linear" width={12} sx={{ color: c.subFg }} />
              <Iconify icon="solar:paperclip-linear" width={12} sx={{ color: c.subFg }} />
              <Box flex={1} />
              <Box sx={{
                width: 22, height: 20, borderRadius: 0.5, bgcolor: "#007A5A",
                display: "grid", placeItems: "center",
              }}>
                <Iconify icon="mdi:send" width={11} sx={{ color: "#FFFFFF" }} />
              </Box>
            </Stack>
          </Box>
        </Stack>
      </Stack>
    </Box>
  );
}

SlackSandboxMock.propTypes = {
  workspace: PropTypes.string,
  envState: PropTypes.object,
};

/* ── bits ────────────────────────────────────────────────────────────── */

function NavIcon({ icon }) {
  return (
    <Box sx={{
      width: 22, height: 22, borderRadius: 0.5,
      display: "grid", placeItems: "center",
      color: "rgba(255,255,255,0.7)",
      "&:hover": { bgcolor: "rgba(255,255,255,0.1)" },
    }}>
      <Iconify icon={icon} width={12} />
    </Box>
  );
}
NavIcon.propTypes = { icon: PropTypes.string };

function RailButton({ icon, label, active }) {
  return (
    <Stack alignItems="center" spacing={0.25} sx={{
      color: active ? "#FFFFFF" : "rgba(255,255,255,0.75)",
    }}>
      <Box sx={{
        width: 30, height: 30, borderRadius: 0.75,
        display: "grid", placeItems: "center",
        border: active ? "1.5px solid rgba(255,255,255,0.5)" : "none",
        bgcolor: active ? "rgba(255,255,255,0.08)" : "transparent",
      }}>
        <Iconify icon={icon} width={13} />
      </Box>
      <Typography sx={{ fontSize: 9, fontWeight: 600 }}>{label}</Typography>
    </Stack>
  );
}
RailButton.propTypes = { icon: PropTypes.string, label: PropTypes.string, active: PropTypes.bool };

function SidebarSection({ label, items, prefix, avatar, c }) {
  const channelFg = c?.channelFg || "#F4EDE4";
  return (
    <Box sx={{ py: 0.75 }}>
      <Stack
        direction="row" alignItems="center" spacing={0.5}
        sx={{ px: 1.5, py: 0.5, cursor: "pointer" }}
      >
        <Iconify icon="solar:alt-arrow-down-linear" width={9} sx={{ color: "rgba(255,255,255,0.6)" }} />
        <Typography sx={{ fontSize: 10.5, fontWeight: 600, color: "rgba(255,255,255,0.7)" }}>
          {label}
        </Typography>
        <Box flex={1} />
        <Iconify icon="mingcute:add-line" width={10} sx={{ color: "rgba(255,255,255,0.6)" }} />
      </Stack>
      <Stack>
        {items.map((it) => (
          <Stack key={it}
            direction="row" alignItems="center" spacing={0.75}
            sx={{ px: 1.5, py: 0.375 }}
          >
            {avatar ? (
              <Box sx={{
                width: 14, height: 14, borderRadius: 0.375, flexShrink: 0,
                bgcolor: "#B7295A", color: "#FFFFFF",
                fontSize: 8, fontWeight: 700,
                display: "grid", placeItems: "center",
              }}>ST</Box>
            ) : (
              <Typography sx={{ fontSize: 11, color: "rgba(255,255,255,0.75)", width: 10 }}>
                {prefix}
              </Typography>
            )}
            <Typography sx={{ fontSize: 12, color: channelFg }} noWrap>{it}</Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}
SidebarSection.propTypes = {
  label: PropTypes.string,
  items: PropTypes.array,
  prefix: PropTypes.string,
  avatar: PropTypes.bool,
  c: PropTypes.object,
};

function ComposerIcon({ char, italic, strike, c }) {
  return (
    <Typography sx={{
      fontSize: 10, fontWeight: 700, color: c?.subFg || "#616061",
      fontStyle: italic ? "italic" : "normal",
      textDecoration: strike ? "line-through" : "none",
      width: 12, textAlign: "center",
    }}>
      {char}
    </Typography>
  );
}
ComposerIcon.propTypes = {
  char: PropTypes.string, italic: PropTypes.bool, strike: PropTypes.bool, c: PropTypes.object,
};
