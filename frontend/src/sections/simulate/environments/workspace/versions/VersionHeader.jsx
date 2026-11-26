import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { fDateTime } from "src/utils/format-time";
import { VersionBadge } from "../EnvVersionPin";
import { VERSIONS_COPY } from "../workspace.constants";
import { PreflightBadge } from "../agents/PreflightStatus";

const MONO = "ui-monospace, Menlo, monospace";

// A version's label, badges, origin, summary and when and by whom it was made.
export default function VersionHeader({ version }) {
  const commit =
    version.origin === "push" && version.source?.commit_sha?.slice(0, 7);
  const origin = version.origin
    ? [VERSIONS_COPY.origin[version.origin] || version.origin, commit]
        .filter(Boolean)
        .join(" · ")
    : null;
  const meta = [
    fDateTime(version.createdAt),
    version.createdBy && VERSIONS_COPY.by(version.createdBy),
  ]
    .filter(Boolean)
    .join(" · ");
  const status = VERSIONS_COPY.status[version.status];
  return (
    <>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.625}
        flexWrap="wrap"
        rowGap={0.375}
      >
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 500,
            fontFamily: MONO,
          }}
        >
          {version.label || VERSIONS_COPY.unlabelled}
          {version.name && (
            <Box component="span" sx={{ fontFamily: "fontFamily" }}>
              {` · ${version.name}`}
            </Box>
          )}
        </Typography>
        {version.isDefault && (
          <VersionBadge label={VERSIONS_COPY.default} />
        )}
        {status && <VersionBadge label={status} />}
        <PreflightBadge status={version.preflightStatus} />
      </Stack>
      {origin && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {origin}
        </Typography>
      )}
      {version.summary && version.summary !== version.name && (
        <Typography
          sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
        >
          {version.summary}
        </Typography>
      )}
      {meta && (
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            mt: 0.25,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {meta}
        </Typography>
      )}
    </>
  );
}
VersionHeader.propTypes = {
  version: PropTypes.object.isRequired,
};
