import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";

function verifiedLink(receipt) {
  if (
    receipt?.status !== "verified" ||
    !Number.isFinite(Date.parse(receipt.verifiedAt)) ||
    typeof receipt.projectId !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/.test(receipt.projectId) ||
    typeof receipt.projectName !== "string" ||
    !receipt.projectName.trim() ||
    typeof receipt.traceId !== "string" ||
    !/^[a-f0-9]{32}$/.test(receipt.traceId)
  )
    return undefined;
  const trace = receipt.traceId;
  const uuid = `${trace.slice(0, 8)}-${trace.slice(8, 12)}-${trace.slice(12, 16)}-${trace.slice(16, 20)}-${trace.slice(20)}`;
  const link = `/dashboard/observe/${encodeURIComponent(receipt.projectId)}/trace/${uuid}`;
  return receipt.deepLink === link ? link : undefined;
}

export default function ObserveExportLinks({ runId, response }) {
  if (
    !response?.available ||
    response.schemaVersion !== "omega-workspace-observations/v1" ||
    response.conversationId !== runId ||
    !Array.isArray(response.observeExports) ||
    response.observeExports.length === 0
  )
    return null;
  const links = response.observeExports.flatMap((receipt) => {
    const href = verifiedLink(receipt);
    return href ? [{ href, name: receipt.projectName }] : [];
  });
  const unique = [
    ...new Map(links.map((entry) => [entry.href, entry])).values(),
  ];
  return (
    <Box sx={{ mt: 1.5 }}>
      <Typography variant="subtitle2">Observe traces</Typography>
      {unique.length > 0 ? (
        <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mt: 0.75 }}>
          {unique.map(({ href, name }) => (
            <Button
              key={href}
              size="small"
              variant="outlined"
              component="a"
              href={href}
              target="_blank"
              rel="noreferrer"
            >
              Open {name}
            </Button>
          ))}
        </Stack>
      ) : (
        <Typography variant="body2" color="text.secondary">
          The trace is not yet verified in Observe.
        </Typography>
      )}
    </Box>
  );
}

ObserveExportLinks.propTypes = {
  runId: PropTypes.string.isRequired,
  response: PropTypes.object,
};
