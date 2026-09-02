import PropTypes from "prop-types";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  Notion palette — two variants. Light-first uses Notion's own real
  chrome; the dark variant matches Notion Dark (bg #191919, sidebar
  #202020, near-white text) so the mock reads as "the same product,
  in dark mode" rather than as our re-skinned light UI dropped on a
  dark page.
*/
function palette(dark) {
  return dark ? {
    bg: "#191919",
    panel: "#202020",
    hover: "#2E2E2E",
    border: "#2E2E2E",
    fg: "#E6E6E6",
    fgMuted: "#9B9B9B",
    tag: "#2E2E2E",
    tagFg: "#9B9B9B",
    pillGreenBg: "#1F3A2A",
    pillGreenFg: "#4CB782",
    initial: "#2E2E2E",
  } : {
    bg: "#FFFFFF",
    panel: "#F7F7F5",
    hover: "#EFEFEC",
    border: "#EAEAE8",
    fg: "#37352F",
    fgMuted: "#787774",
    tag: "#EFEFEC",
    tagFg: "#9B9A97",
    pillGreenBg: "#DBEDDA",
    pillGreenFg: "#0F5132",
    initial: "#DFDFDD",
  };
}

/**
 * Notion-shaped inline sandbox preview. Renders faithfully in either
 * light or dark theme by picking the palette from `theme.palette.mode`
 * so the sandbox always looks like the real product rather than a
 * themed skin.
 */
export default function NotionSandboxMock({ workspace = "Default Workspace" }) {
  const dark = useTheme().palette.mode === "dark";
  const c = palette(dark);
  return (
    <Box sx={{
      borderRadius: 1.5, overflow: "hidden",
      border: "1px solid", borderColor: "divider",
      bgcolor: c.bg, color: c.fg,
      height: 800, display: "flex", flexDirection: "column",
    }}>
      {/* top bar */}
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{
        height: 38, px: 1.5, flexShrink: 0,
        borderBottom: `1px solid ${c.border}`, bgcolor: c.bg,
      }}>
        <Iconify icon="solar:alt-arrow-left-linear" width={12} sx={{ color: c.fgMuted }} />
        <Iconify icon="solar:alt-arrow-right-linear" width={12} sx={{ color: c.fgMuted }} />
        <Typography sx={{ fontSize: 12, color: c.fg, ml: 0.5 }}>
          Playbooks · Refund policy
        </Typography>
        <Box flex={1} />
        <Typography sx={{ fontSize: 11, color: c.fgMuted }}>Edited just now</Typography>
        <Iconify icon="solar:share-linear" width={11} sx={{ color: c.fgMuted }} />
        <Iconify icon="solar:menu-dots-linear" width={11} sx={{ color: c.fgMuted }} />
      </Stack>

      <Stack direction="row" sx={{ flex: 1, minHeight: 0 }}>
        {/* pages sidebar */}
        <Stack sx={{
          width: 240, flexShrink: 0,
          bgcolor: c.panel, borderRight: `1px solid ${c.border}`,
        }}>
          <Stack direction="row" alignItems="center" spacing={0.75}
            sx={{ px: 1.5, py: 1.25, borderBottom: `1px solid ${c.border}` }}
          >
            <Box sx={{
              width: 18, height: 18, borderRadius: 0.5,
              bgcolor: c.initial, color: c.fg,
              display: "grid", placeItems: "center",
              fontSize: 10, fontWeight: 700,
            }}>W</Box>
            <Typography sx={{ fontSize: 12, fontWeight: 600, flex: 1, color: c.fg }} noWrap>
              {workspace}
            </Typography>
            <Iconify icon="solar:alt-arrow-down-linear" width={9} sx={{ color: c.fgMuted }} />
          </Stack>

          <Stack sx={{ px: 0.5, py: 1 }} spacing={0.125}>
            <NavItem icon="solar:magnifer-linear" label="Search" c={c} />
            <NavItem icon="solar:clock-circle-linear" label="Updates" c={c} />
            <NavItem icon="solar:settings-linear" label="Settings & members" c={c} />
          </Stack>

          <Box sx={{ px: 1.5, pt: 1.25, pb: 0.5 }}>
            <Typography sx={{ fontSize: 10.5, fontWeight: 600, color: c.fgMuted, letterSpacing: 0.5 }}>
              PRIVATE
            </Typography>
          </Box>
          <Stack sx={{ px: 0.5 }} spacing={0.125}>
            <PageItem emoji="📘" label="Playbooks" open c={c}>
              <PageItem emoji="↩️" label="Refund policy" active nested c={c} />
              <PageItem emoji="🚨" label="Escalation runbook" nested c={c} />
              <PageItem emoji="📞" label="Outage response" nested c={c} />
            </PageItem>
            <PageItem emoji="🚀" label="Launch database" tag="DB" c={c} />
            <PageItem emoji="💰" label="Pricing FAQ" c={c} />
            <PageItem emoji="📝" label="Meeting notes" c={c} />
          </Stack>

          <Box flex={1} />

          <Stack sx={{ px: 0.5, py: 1, borderTop: `1px solid ${c.border}` }} spacing={0.125}>
            <NavItem icon="solar:trash-bin-minimalistic-linear" label="Trash" c={c} />
            <NavItem icon="mingcute:add-line" label="Add a page" c={c} />
          </Stack>
        </Stack>

        {/* page body */}
        <Stack sx={{ flex: 1, minWidth: 0, bgcolor: c.bg }}>
          <Box sx={{ px: 6, pt: 4, pb: 2 }}>
            <Typography sx={{ fontSize: 48, mb: 0.5 }}>↩️</Typography>
            <Typography sx={{ fontSize: 32, fontWeight: 700, color: c.fg, lineHeight: 1.2 }}>
              Refund policy
            </Typography>
            <Stack direction="row" alignItems="center" spacing={2} sx={{ mt: 1.5 }}>
              <PropField label="Owner" value="Support Lead" c={c} />
              <PropField label="Last review" value="Sep 2026" c={c} />
              <PropField label="Status" value="Live" pill={c.pillGreenBg} pillColor={c.pillGreenFg} c={c} />
            </Stack>
          </Box>
          <Box sx={{ px: 6, py: 1.5, flex: 1, borderTop: `1px solid ${c.border}`, overflow: "hidden" }}>
            <BlockLine c={c}>
              Refunds for orders that shipped over 30 days ago are handled case-by-case with the Support Lead.
            </BlockLine>
            <BlockLine bullet c={c}>Under 30 days · same payment method · no manager approval needed</BlockLine>
            <BlockLine bullet c={c}>30–60 days · needs Support Lead review before issuing</BlockLine>
            <BlockLine bullet c={c}>Over 60 days · escalate to Finance, no direct refund</BlockLine>
            <BlockLine heading c={c}>Steps</BlockLine>
            <BlockLine numbered n={1} c={c}>Verify order in the CRM (Stripe or QuickBooks)</BlockLine>
            <BlockLine numbered n={2} c={c}>Confirm shipping status was Delivered or Cancelled</BlockLine>
            <BlockLine numbered n={3} c={c}>Issue refund via the original payment method</BlockLine>
          </Box>
        </Stack>
      </Stack>
    </Box>
  );
}
NotionSandboxMock.propTypes = { workspace: PropTypes.string };

/* ── bits ────────────────────────────────────────────────────────────── */

function NavItem({ icon, label, c }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}
      sx={{ px: 1, py: 0.5, borderRadius: 0.5, "&:hover": { bgcolor: c.hover } }}
    >
      <Iconify icon={icon} width={12} sx={{ color: c.fgMuted }} />
      <Typography sx={{ fontSize: 12, color: c.fg }}>{label}</Typography>
    </Stack>
  );
}
NavItem.propTypes = { icon: PropTypes.string, label: PropTypes.string, c: PropTypes.object };

function PageItem({ emoji, label, active, open, nested, tag, children, c }) {
  return (
    <>
      <Stack direction="row" alignItems="center" spacing={0.5}
        sx={{
          pl: nested ? 2.5 : 1, pr: 1, py: 0.5, borderRadius: 0.5,
          bgcolor: active ? c.hover : "transparent",
          "&:hover": { bgcolor: c.hover },
        }}
      >
        <Iconify
          icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
          width={9} sx={{ color: c.fgMuted, visibility: children ? "visible" : "hidden" }}
        />
        <Typography sx={{ fontSize: 12, flexShrink: 0 }}>{emoji}</Typography>
        <Typography sx={{ fontSize: 12, color: c.fg, flex: 1 }} noWrap>{label}</Typography>
        {tag && (
          <Typography sx={{
            fontSize: 9, fontWeight: 700, color: c.tagFg,
            px: 0.5, borderRadius: 0.25, bgcolor: c.tag,
          }}>{tag}</Typography>
        )}
      </Stack>
      {open && children}
    </>
  );
}
PageItem.propTypes = {
  emoji: PropTypes.string, label: PropTypes.string,
  active: PropTypes.bool, open: PropTypes.bool, nested: PropTypes.bool,
  tag: PropTypes.string, children: PropTypes.node, c: PropTypes.object,
};

function PropField({ label, value, pill, pillColor, c }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Typography sx={{ fontSize: 11, color: c.fgMuted }}>{label}</Typography>
      {pill ? (
        <Typography sx={{
          fontSize: 10.5, fontWeight: 600, color: pillColor || c.fg,
          px: 0.75, py: 0.125, borderRadius: 0.5, bgcolor: pill,
        }}>{value}</Typography>
      ) : (
        <Typography sx={{ fontSize: 11, color: c.fg, fontWeight: 500 }}>{value}</Typography>
      )}
    </Stack>
  );
}
PropField.propTypes = {
  label: PropTypes.string, value: PropTypes.string,
  pill: PropTypes.string, pillColor: PropTypes.string, c: PropTypes.object,
};

function BlockLine({ children, bullet, heading, numbered, n, c }) {
  if (heading) {
    return (
      <Typography sx={{ fontSize: 20, fontWeight: 700, color: c.fg, mt: 2, mb: 0.5 }}>
        {children}
      </Typography>
    );
  }
  return (
    <Stack direction="row" spacing={1} sx={{ my: 0.5 }}>
      {bullet && (
        <Typography sx={{ fontSize: 12, color: c.fg, flexShrink: 0, mt: "1px" }}>•</Typography>
      )}
      {numbered && (
        <Typography sx={{ fontSize: 12, color: c.fg, flexShrink: 0, mt: "1px", minWidth: 14 }}>
          {n}.
        </Typography>
      )}
      <Typography sx={{ fontSize: 12.5, color: c.fg, lineHeight: 1.55 }}>{children}</Typography>
    </Stack>
  );
}
BlockLine.propTypes = {
  children: PropTypes.node, bullet: PropTypes.bool,
  heading: PropTypes.bool, numbered: PropTypes.bool, n: PropTypes.number,
  c: PropTypes.object,
};
