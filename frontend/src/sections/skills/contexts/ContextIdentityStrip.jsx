import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

// Intent/hierarchy: distinguish selected, latest and production identities at a glance.
// Palette/depth: neutral paper, muted provenance, one divider; no pass coloration.
// Type/spacing: m3/s1/s3 and 12px groups which stack at narrow widths.
export default function ContextIdentityStrip({ viewed, latestVersion, productionVersion, draft, observations }) {
  const identities = [
    ["Viewed", viewed ? `v${viewed.version ?? viewed.number}` : "Not published", viewed?.digest],
    ["Latest", latestVersion == null ? "Not published" : `v${latestVersion}`],
    ["Production", productionVersion == null ? "Not assigned" : `v${productionVersion}`],
    ...(draft ? [["Your draft", `r${draft.revision} · origin ${draft.baseVersion == null ? "unpublished" : `v${draft.baseVersion}`}`]] : []),
  ];
  return <Stack spacing={1.5} sx={{ borderBottom: 1, borderColor: "divider", pb: 1.5, minWidth: 0 }}>
    <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ flexWrap: "wrap" }}>
      {identities.map(([label, value, digest]) => <Box key={label} sx={{ minWidth: 0, flex: 1 }}>
        <Typography variant="s3" display="block" color="text.secondary">{label}</Typography>
        <Typography variant="m3" display="block">{value}</Typography>
        {digest && <Typography variant="s3" display="block" sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}>{digest}</Typography>}
      </Box>)}
    </Stack>
    <Typography variant="s3" color="text.secondary">Installed: {observations?.installed || "Not observed"} · Delivered: {observations?.delivered || "Not observed"}. A fetched release does not prove model delivery.</Typography>
  </Stack>;
}
ContextIdentityStrip.propTypes = { viewed: PropTypes.object, latestVersion: PropTypes.number, productionVersion: PropTypes.number, draft: PropTypes.object, observations: PropTypes.object };
