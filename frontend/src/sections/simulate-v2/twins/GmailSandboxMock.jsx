import PropTypes from "prop-types";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import Iconify from "src/components/iconify";

const GMAIL_BLUE = "#1A73E8";

/*
  Gmail palette per theme. Dark uses Gmail Dark's palette (#1F1F1F
  base, #2A2A2A rows, #E8EAED text). The blue accent + compose pill
  stay tuned to Gmail Dark's actual accents.
*/
function gmailPalette(dark) {
  return dark ? {
    bg: "#1F1F1F", pane: "#282A2C", rowUnread: "#2A2A2C", rowRead: "#232426",
    border: "#3C4043", fg: "#E8EAED", subFg: "#BDC1C6", muted: "#9AA0A6",
    composeBg: "#004A77", composeFg: "#C2E7FF",
    activeLabelBg: "#1F3A5F", activeLabelFg: "#8AB4F8",
    hoverBg: "#3C4043",
    supportBg: "#164329", supportFg: "#8DD1A6",
    escalatedBg: "#4A2320", escalatedFg: "#E29A93",
    renewalsBg: "#2B2454", renewalsFg: "#B39DFF",
  } : {
    bg: "#F6F8FC", pane: "#FFFFFF", rowUnread: "#FFFFFF", rowRead: "#F6F8FC",
    border: "#F1F3F4", fg: "#202124", subFg: "#5F6368", muted: "#5F6368",
    composeBg: "#C2E7FF", composeFg: "#001D35",
    activeLabelBg: "#D3E3FD", activeLabelFg: "#001D35",
    hoverBg: "#EAECEF",
    supportBg: "#DBEDDA", supportFg: "#0F5132",
    escalatedBg: "#F8D7DA", escalatedFg: "#842029",
    renewalsBg: "#E9E5FF", renewalsFg: "#4C3AA0",
  };
}

/**
 * Gmail-shaped inline sandbox preview — renders in either theme so
 * the sandbox reads as the real product a user opens on their own
 * machine, not a themed re-skin.
 */
export default function GmailSandboxMock() {
  const dark = useTheme().palette.mode === "dark";
  const c = gmailPalette(dark);
  const rows = [
    { from: "Priya at Acme", subject: "Refund for order #A-8842", snippet: "Hi — the order shipped 8 days ago but nothing has arrived. Could I get a refund…", label: "Support", unread: true },
    { from: "Beacon CSM", subject: "Q4 renewal — what would help you say yes?", snippet: "Following up on our call last week. Wanted to share three options for renewal…", label: "Renewals" },
    { from: "Legal · Compliance", subject: "Please do not reply — internal escalation", snippet: "Flagging a customer complaint we need to route through legal before responding…", label: "Escalated", unread: true },
    { from: "Sam (Product)", subject: "Sync tomorrow at 3pm?", snippet: "Want to walk you through the checkout redesign. Should take ~20 min…", label: null },
    { from: "Priya at Acme", subject: "Re: Refund for order #A-8842", snippet: "Actually — order #A-8843 too. Same shipping issue…", label: "Support", unread: true },
    { from: "Automated", subject: "Weekly usage report", snippet: "Your team used 24,301 API calls this week (+8% vs. last week)…", label: null },
  ];

  return (
    <Box sx={{
      borderRadius: 1.5, overflow: "hidden",
      border: "1px solid", borderColor: "divider",
      bgcolor: c.bg, color: c.fg,
      height: 800, display: "flex", flexDirection: "column",
    }}>
      {/* top bar */}
      <Stack direction="row" alignItems="center" spacing={1.5}
        sx={{ height: 44, px: 2, flexShrink: 0, bgcolor: c.bg }}
      >
        <Iconify icon="solar:hamburger-menu-linear" width={16} sx={{ color: c.subFg }} />
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Iconify icon="logos:google-gmail" width={20} />
          <Typography sx={{ fontSize: 18, color: c.subFg, ml: 0.5 }}>Gmail</Typography>
        </Stack>
        <Box sx={{
          flex: 1, maxWidth: 620, height: 32, ml: 3, px: 1.5, borderRadius: 3,
          bgcolor: c.pane, display: "flex", alignItems: "center", gap: 1,
        }}>
          <Iconify icon="solar:magnifer-linear" width={14} sx={{ color: c.subFg }} />
          <Typography sx={{ fontSize: 12.5, color: c.subFg }}>Search mail</Typography>
        </Box>
        <Box flex={1} />
        <Iconify icon="solar:settings-linear" width={16} sx={{ color: c.subFg }} />
        <Box sx={{
          width: 26, height: 26, borderRadius: "50%",
          bgcolor: GMAIL_BLUE, color: "#FFFFFF",
          display: "grid", placeItems: "center",
          fontSize: 11, fontWeight: 700,
        }}>A</Box>
      </Stack>

      <Stack direction="row" sx={{ flex: 1, minHeight: 0 }}>
        {/* labels rail */}
        <Stack sx={{ width: 200, flexShrink: 0, py: 1, bgcolor: c.bg }} spacing={0.25}>
          <Box sx={{ px: 1.5, mb: 1 }}>
            <Stack direction="row" alignItems="center" spacing={1}
              sx={{ bgcolor: c.composeBg, borderRadius: 3, px: 1.5, py: 1, width: "fit-content" }}
            >
              <Iconify icon="solar:pen-linear" width={14} sx={{ color: c.composeFg }} />
              <Typography sx={{ fontSize: 13, fontWeight: 600, color: c.composeFg }}>Compose</Typography>
            </Stack>
          </Box>
          <LabelRow icon="solar:inbox-linear" label="Inbox" count={3} active c={c} />
          <LabelRow icon="solar:star-linear" label="Starred" c={c} />
          <LabelRow icon="solar:clock-circle-linear" label="Snoozed" c={c} />
          <LabelRow icon="solar:round-arrow-right-up-linear" label="Sent" c={c} />
          <LabelRow icon="solar:pen-2-linear" label="Drafts" count={1} c={c} />
          <LabelRow icon="solar:alt-arrow-down-linear" label="More" muted c={c} />
          <Box sx={{ height: 8 }} />
          <Box sx={{ px: 1.5, pb: 0.5 }}>
            <Typography sx={{ fontSize: 10.5, fontWeight: 700, color: c.muted, letterSpacing: 0.4 }}>
              LABELS
            </Typography>
          </Box>
          <LabelRow dot="#0F5132" label="Support" count={2} c={c} />
          <LabelRow dot="#C2603F" label="Escalated" count={1} c={c} />
          <LabelRow dot="#7857FC" label="Renewals" count={1} c={c} />
        </Stack>

        {/* inbox list */}
        <Stack sx={{ flex: 1, minWidth: 0, bgcolor: c.pane, borderRadius: "8px 0 0 0" }}>
          <Stack direction="row" alignItems="center" spacing={2}
            sx={{ px: 2, py: 1, borderBottom: `1px solid ${c.border}` }}
          >
            <Iconify icon="solar:checkbox-linear" width={14} sx={{ color: c.subFg }} />
            <Iconify icon="solar:refresh-linear" width={14} sx={{ color: c.subFg }} />
            <Iconify icon="solar:menu-dots-linear" width={14} sx={{ color: c.subFg }} />
            <Box flex={1} />
            <Typography sx={{ fontSize: 11.5, color: c.subFg }}>1–6 of 6</Typography>
          </Stack>
          <Stack sx={{ flex: 1, overflow: "hidden" }}>
            {rows.map((r, i) => <InboxRow key={i} row={r} c={c} />)}
          </Stack>
        </Stack>
      </Stack>
    </Box>
  );
}

/* ── bits ────────────────────────────────────────────────────────────── */

function LabelRow({ icon, dot, label, count, active, muted, c }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.25}
      sx={{
        pl: 2.25, pr: 1.5, py: 0.5,
        borderRadius: "0 12px 12px 0", mr: 1,
        bgcolor: active ? c.activeLabelBg : "transparent",
        "&:hover": { bgcolor: active ? c.activeLabelBg : c.hoverBg },
      }}
    >
      {icon ? (
        <Iconify icon={icon} width={13} sx={{ color: muted ? c.subFg : c.fg, flexShrink: 0 }} />
      ) : (
        <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: dot, flexShrink: 0 }} />
      )}
      <Typography sx={{
        fontSize: 12.5, flex: 1,
        color: muted ? c.subFg : (active ? c.activeLabelFg : c.fg),
        fontWeight: active ? 700 : 500,
      }}>
        {label}
      </Typography>
      {count && (
        <Typography sx={{ fontSize: 11.5, color: active ? c.activeLabelFg : c.subFg, fontWeight: active ? 700 : 500 }}>
          {count}
        </Typography>
      )}
    </Stack>
  );
}
LabelRow.propTypes = {
  icon: PropTypes.string, dot: PropTypes.string, label: PropTypes.string,
  count: PropTypes.number, active: PropTypes.bool, muted: PropTypes.bool,
  c: PropTypes.object,
};

function InboxRow({ row, c }) {
  const labelColor = ({
    Support: { bg: c.supportBg, fg: c.supportFg },
    Escalated: { bg: c.escalatedBg, fg: c.escalatedFg },
    Renewals: { bg: c.renewalsBg, fg: c.renewalsFg },
  })[row.label];
  return (
    <Stack direction="row" alignItems="center" spacing={1.5}
      sx={{
        px: 2, py: 1, borderBottom: `1px solid ${c.border}`, cursor: "pointer",
        bgcolor: row.unread ? c.rowUnread : c.rowRead,
        "&:hover": { boxShadow: `inset 0 0 0 1px ${c.border}` },
      }}
    >
      <Iconify icon="solar:star-linear" width={13} sx={{ color: c.subFg, flexShrink: 0 }} />
      <Typography sx={{
        fontSize: 12.5, width: 140, flexShrink: 0,
        fontWeight: row.unread ? 700 : 500,
        color: row.unread ? c.fg : c.subFg,
      }} noWrap>
        {row.from}
      </Typography>
      {labelColor && (
        <Typography sx={{
          fontSize: 10, fontWeight: 700,
          px: 0.75, py: 0.125, borderRadius: 0.5,
          bgcolor: labelColor.bg, color: labelColor.fg,
          flexShrink: 0,
        }}>
          {row.label}
        </Typography>
      )}
      <Stack direction="row" spacing={0.75} sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{
          fontSize: 12.5, fontWeight: row.unread ? 700 : 500,
          color: row.unread ? c.fg : c.subFg,
        }} noWrap>
          {row.subject}
        </Typography>
        <Typography sx={{ fontSize: 12.5, color: c.subFg, flex: 1 }} noWrap>
          — {row.snippet}
        </Typography>
      </Stack>
      <Typography sx={{
        fontSize: 11, color: row.unread ? c.fg : c.subFg,
        fontWeight: row.unread ? 700 : 500,
        flexShrink: 0,
      }}>
        just now
      </Typography>
    </Stack>
  );
}
InboxRow.propTypes = { row: PropTypes.object, c: PropTypes.object };
