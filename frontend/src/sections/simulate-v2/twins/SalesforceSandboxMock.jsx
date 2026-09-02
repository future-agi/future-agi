import PropTypes from "prop-types";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import Iconify from "src/components/iconify";

const SF_BLUE = "#00A1E0";
const SF_DARK = "#032D60";

/*
  Salesforce palette. Light uses SF Lightning defaults (grey #F3F3F3
  wash + white cards + navy app bar). Dark uses Lightning's own dark
  mode: near-black app bar and rows, off-white text, SF blue accent
  preserved as the interactive tint.
*/
function sfPalette(dark) {
  return dark ? {
    bg: "#0D0D0D", pane: "#1A1A1A", subPane: "#1F1F1F",
    fg: "#E8E8E8", subFg: "#A5A5A5",
    border: "#2E2E2E", rowHover: "#22282F",
    appBar: "#0A1D2C",
    stageProposalBg: "#0F2942", stageProposalFg: "#8ECEFF",
    stageNegBg: "#3B2A08", stageNegFg: "#F6C36B",
    stageWonBg: "#153D24", stageWonFg: "#8DD1A6",
    stageDefaultBg: "#26292E", stageDefaultFg: "#C4C7C5",
    warnFg: "#F5A66A",
  } : {
    bg: "#F3F3F3", pane: "#FFFFFF", subPane: "#FAFAF9",
    fg: "#181818", subFg: "#706E6B",
    border: "#DDDBDA", rowHover: "#F3F9FF",
    appBar: SF_DARK,
    stageProposalBg: "#D6ECFB", stageProposalFg: SF_DARK,
    stageNegBg: "#FFECBD", stageNegFg: "#8E5C00",
    stageWonBg: "#DBEDDA", stageWonFg: "#0F5132",
    stageDefaultBg: "#F1F1F1", stageDefaultFg: "#3E3E3C",
    warnFg: "#B54800",
  };
}

/**
 * Salesforce Lightning-shaped inline sandbox preview. Renders in
 * either theme so the sandbox reads as the real product the user
 * would open in their own workspace. Rows are drawn from the shape
 * a RevOps twin template produces — Acme + Beacon Corp + Zenith +
 * Cirrus with Q4 opportunities in various stages.
 */
export default function SalesforceSandboxMock() {
  const dark = useTheme().palette.mode === "dark";
  const c = sfPalette(dark);
  const accounts = [
    { name: "Acme", owner: "You", opp: "Acme — Q4 renewal", stage: "Proposal", closeIn: "15d", value: "$48,000", nextStep: "Send proposal" },
    { name: "Beacon Corp", owner: "Priya S.", opp: "Beacon Corp — Q4 renewal", stage: "Discovery", closeIn: "42d", value: "$120,000", nextStep: "— none set —", warn: true },
    { name: "Zenith", owner: "You", opp: "Zenith — expansion", stage: "Negotiation", closeIn: "8d", value: "$62,500", nextStep: "Confirm redlines" },
    { name: "Cirrus", owner: "Marcus L.", opp: "Cirrus — Q4 renewal", stage: "Closed Won", closeIn: "—", value: "$34,500", nextStep: "Handoff to CS", won: true },
  ];
  return (
    <Box sx={{
      borderRadius: 1.5, overflow: "hidden",
      border: "1px solid", borderColor: "divider",
      bgcolor: c.bg, color: c.fg,
      height: 800, display: "flex", flexDirection: "column",
    }}>
      {/* app bar */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{
        height: 42, px: 1.5, flexShrink: 0,
        bgcolor: c.appBar, color: "#FFFFFF",
      }}>
        <Iconify icon="solar:hamburger-menu-linear" width={14} sx={{ color: "#FFFFFF" }} />
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Iconify icon="logos:salesforce" width={22} />
          <Typography sx={{ fontSize: 12, fontWeight: 700, color: "#FFFFFF" }}>Sales</Typography>
        </Stack>
        <Box sx={{
          flex: 1, maxWidth: 420, height: 26, px: 1, borderRadius: 0.5,
          bgcolor: "rgba(255,255,255,0.14)",
          display: "flex", alignItems: "center", gap: 0.75,
        }}>
          <Iconify icon="solar:magnifer-linear" width={12} sx={{ color: "rgba(255,255,255,0.7)" }} />
          <Typography sx={{ fontSize: 11.5, color: "rgba(255,255,255,0.75)" }}>Search Salesforce</Typography>
        </Box>
        <Box flex={1} />
        <Iconify icon="solar:bell-linear" width={13} sx={{ color: "rgba(255,255,255,0.85)" }} />
        <Iconify icon="solar:settings-linear" width={13} sx={{ color: "rgba(255,255,255,0.85)" }} />
        <Box sx={{
          width: 22, height: 22, borderRadius: "50%",
          bgcolor: "#FBB03B", color: "#181818",
          display: "grid", placeItems: "center",
          fontSize: 10, fontWeight: 700,
        }}>V</Box>
      </Stack>

      {/* nav tabs strip */}
      <Stack direction="row" spacing={0} sx={{
        px: 1, flexShrink: 0, borderBottom: `1px solid ${c.border}`,
        bgcolor: c.pane,
      }}>
        <NavTab label="Home" c={c} />
        <NavTab label="Accounts" active c={c} />
        <NavTab label="Opportunities" c={c} />
        <NavTab label="Contacts" c={c} />
        <NavTab label="Leads" c={c} />
        <NavTab label="Reports" c={c} />
        <NavTab label="Dashboards" c={c} />
      </Stack>

      {/* main content */}
      <Stack direction="row" sx={{ flex: 1, minHeight: 0, bgcolor: c.bg }}>
        <Stack sx={{
          flex: 1, minWidth: 0, m: 1.5, borderRadius: 0.75,
          bgcolor: c.pane, border: `1px solid ${c.border}`, overflow: "hidden",
        }}>
          {/* list-view header */}
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{
            px: 2, py: 1, borderBottom: `1px solid ${c.border}`,
          }}>
            <Iconify icon="solar:case-linear" width={14} sx={{ color: SF_BLUE }} />
            <Typography sx={{ fontSize: 13, fontWeight: 700, color: c.fg }}>Accounts</Typography>
            <Typography sx={{ fontSize: 11, color: c.subFg }}>· 4 items · Sorted by Close date</Typography>
            <Box flex={1} />
            <Iconify icon="solar:filter-linear" width={12} sx={{ color: c.subFg }} />
            <Iconify icon="solar:refresh-linear" width={12} sx={{ color: c.subFg }} />
            <Box sx={{
              px: 1, py: 0.375, borderRadius: 0.375,
              bgcolor: SF_BLUE, color: "#FFFFFF",
              fontSize: 10.5, fontWeight: 700,
            }}>New</Box>
          </Stack>

          {/* column headers */}
          <Stack direction="row" alignItems="center"
            sx={{
              px: 2, py: 0.75, borderBottom: `1px solid ${c.border}`,
              bgcolor: c.subPane,
            }}
          >
            <Col width={120} head c={c}>Account</Col>
            <Col width={160} head c={c}>Opportunity</Col>
            <Col width={100} head c={c}>Stage</Col>
            <Col width={70} head c={c}>Close</Col>
            <Col width={80} head align="right" c={c}>Amount</Col>
            <Col width={160} head flex c={c}>Next Step</Col>
          </Stack>

          {/* rows */}
          <Stack sx={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
            {accounts.map((a) => (
              <Stack key={a.name} direction="row" alignItems="center"
                sx={{
                  px: 2, py: 1, borderBottom: `1px solid ${c.border}`,
                  "&:hover": { bgcolor: c.rowHover },
                }}
              >
                <Col width={120} c={c}>
                  <Typography sx={{ fontSize: 12, color: SF_BLUE, fontWeight: 600 }}>{a.name}</Typography>
                  <Typography sx={{ fontSize: 10.5, color: c.subFg }}>{a.owner}</Typography>
                </Col>
                <Col width={160} c={c}>
                  <Typography sx={{ fontSize: 12, color: SF_BLUE }} noWrap>{a.opp}</Typography>
                </Col>
                <Col width={100} c={c}>
                  <StagePill stage={a.stage} won={a.won} c={c} />
                </Col>
                <Col width={70} c={c}>
                  <Typography sx={{ fontSize: 12, color: c.fg }}>{a.closeIn}</Typography>
                </Col>
                <Col width={80} align="right" c={c}>
                  <Typography sx={{ fontSize: 12, color: c.fg, fontWeight: 600 }}>{a.value}</Typography>
                </Col>
                <Col width={160} flex c={c}>
                  <Typography sx={{
                    fontSize: 12, color: a.warn ? c.warnFg : c.fg,
                    fontStyle: a.warn ? "italic" : "normal",
                  }} noWrap>
                    {a.nextStep}
                  </Typography>
                </Col>
              </Stack>
            ))}
          </Stack>
        </Stack>
      </Stack>
    </Box>
  );
}

/* ── bits ────────────────────────────────────────────────────────────── */

function NavTab({ label, active, c }) {
  return (
    <Box sx={{
      px: 1.5, py: 1,
      borderBottom: active ? `3px solid ${SF_BLUE}` : "3px solid transparent",
      color: active ? SF_BLUE : c.fg,
      fontSize: 11.5, fontWeight: active ? 700 : 500,
    }}>
      {label}
    </Box>
  );
}
NavTab.propTypes = { label: PropTypes.string, active: PropTypes.bool, c: PropTypes.object };

function Col({ width, children, head, align, flex, c }) {
  return (
    <Box sx={{
      width: flex ? undefined : width, flex: flex ? 1 : "0 0 auto",
      minWidth: 0, pr: 1, textAlign: align || "left",
    }}>
      {head ? (
        <Typography sx={{
          fontSize: 10.5, fontWeight: 700, color: c.subFg,
          textTransform: "uppercase", letterSpacing: 0.4,
        }}>
          {children}
        </Typography>
      ) : children}
    </Box>
  );
}
Col.propTypes = {
  width: PropTypes.number, children: PropTypes.node,
  head: PropTypes.bool, align: PropTypes.string, flex: PropTypes.bool,
  c: PropTypes.object,
};

function StagePill({ stage, won, c }) {
  const palette = won
    ? { bg: c.stageWonBg, fg: c.stageWonFg }
    : stage === "Proposal" ? { bg: c.stageProposalBg, fg: c.stageProposalFg }
    : stage === "Negotiation" ? { bg: c.stageNegBg, fg: c.stageNegFg }
    : { bg: c.stageDefaultBg, fg: c.stageDefaultFg };
  return (
    <Typography sx={{
      display: "inline-block",
      fontSize: 10.5, fontWeight: 700,
      px: 0.75, py: 0.125, borderRadius: 0.5,
      bgcolor: palette.bg, color: palette.fg,
    }}>
      {stage}
    </Typography>
  );
}
StagePill.propTypes = { stage: PropTypes.string, won: PropTypes.bool, c: PropTypes.object };
