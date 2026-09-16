import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Divider, Stack, Tooltip, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { BUILD_TONES } from "../buildTones";
import { READ_AUDIT_COPY } from "../readAudit.constants";
import OriginChip from "../../components/OriginChip";
import SectionIssue from "./SectionIssue";

// One dimension card in the read audit: a header (icon, title, count pill,
// subtitle info tooltip) over either the fact rows, an inline issue block, or the
// empty copy. Ported verbatim from the designer's AgentReadReceipt.jsx
// FactSection, amber hex from BUILD_TONES and empty copy from READ_AUDIT_COPY.
export default function FactSection({ icon, title, subtitle, count, facts = [], issue, onRetry }) {
  const isIssued = !!issue;
  const showFacts = !isIssued && facts.length > 0;
  return (
    <Box
      sx={{
        height: "100%", display: "flex", flexDirection: "column",
        border: "1px solid", borderColor: "divider", borderRadius: 1.5, overflow: "hidden",
      }}
    >
      {/* card header */}
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 1.75, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Box
          sx={{
            width: 22, height: 22, borderRadius: 0.75, display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha(isIssued ? BUILD_TONES.amber : t.palette.text.primary, isIssued ? 0.14 : (t.palette.mode === "dark" ? 0.08 : 0.06)),
            color: isIssued ? BUILD_TONES.amber : "text.secondary",
          }}
        >
          <Iconify icon={icon} width={13} />
        </Box>
        <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightBold", minWidth: 0 }}>{title}</Typography>
        {!isIssued && count != null && (
          <Box sx={{ px: 0.625, height: 16, borderRadius: 0.5, display: "inline-flex", alignItems: "center", flexShrink: 0, bgcolor: (t) => alpha(t.palette.text.primary, 0.08) }}>
            <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>{count}</Typography>
          </Box>
        )}
        {subtitle && (
          <Tooltip arrow title={subtitle}>
            <Box sx={{ ml: "auto", display: "flex", flexShrink: 0, color: "text.disabled" }}>
              <Iconify icon="solar:info-circle-linear" width={13} />
            </Box>
          </Tooltip>
        )}
      </Stack>

      {/* card body */}
      <Box sx={{ flex: 1, minHeight: 0, px: 1.75, py: isIssued ? 1.5 : 0.25 }}>
        {isIssued ? (
          <SectionIssue issue={issue} onRetry={onRetry} />
        ) : showFacts ? (
          <Stack divider={<Divider sx={{ borderColor: (t) => alpha(t.palette.divider, 0.6) }} />}>
            {facts.map((f) => (
              <Stack
                key={f.name}
                direction="row" alignItems="center" spacing={1.25}
                sx={{
                  py: 0.75, mx: -0.75, px: 0.75, borderRadius: 0.75,
                  transition: "background-color .1s ease",
                  "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.03) },
                }}
              >
                <Typography
                  noWrap
                  sx={{ typography: "s2", fontWeight: "fontWeightMedium", fontFamily: "ui-monospace, Menlo, monospace", flex: 1, minWidth: 0, color: "text.primary" }}
                >
                  {f.name}
                </Typography>
                {f.warning && (
                  <Tooltip arrow title={f.warning}>
                    <Box sx={{ display: "flex", alignItems: "center", color: BUILD_TONES.amber, flexShrink: 0 }}>
                      <Iconify icon="solar:danger-triangle-bold" width={13} />
                    </Box>
                  </Tooltip>
                )}
                {f.note && (
                  <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>{f.note}</Typography>
                )}
                <OriginChip origin={f.origin} showPath={false} />
              </Stack>
            ))}
          </Stack>
        ) : (
          <Typography sx={{ typography: "s3", color: "text.subtitle", py: 1 }}>
            {READ_AUDIT_COPY.empty}
          </Typography>
        )}
      </Box>
    </Box>
  );
}
FactSection.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.string,
  subtitle: PropTypes.string,
  count: PropTypes.number,
  facts: PropTypes.arrayOf(PropTypes.shape({
    name: PropTypes.string,
    origin: PropTypes.string,
    note: PropTypes.string,
    warning: PropTypes.string,
  })),
  issue: PropTypes.shape({
    severity: PropTypes.string,
    icon: PropTypes.string,
    message: PropTypes.string,
    hint: PropTypes.string,
    retryLabel: PropTypes.string,
  }),
  onRetry: PropTypes.func,
};
