import PropTypes from "prop-types";
import { Alert, Box, Checkbox, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from "@mui/material";
import EmptyState from "src/components/empty-state";
import TechnicalDetails from '../components/TechnicalDetails';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";

// Intent: an author sees every selected member and blocker before committing intake.
// Hierarchy: whole-closure limits, followed by the complete member inventory.
// Palette/depth/surfaces: flat table, divider and semantic warning; no capacity gauge.
// Typography/spacing: m3/s1/s3 and shared row heights, 12px groups.
export default function CompatibilityInventory({ items = [], selectedKeys = [], onSelectionChange, rejected = [], skipped = [], closure, readOnly = false }) {
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="s1">Only supported text files can be imported.</Typography>
    {closure?.blocked && <Alert severity="error">These files exceed the context limits. Choose a smaller set before importing.</Alert>}
    <TechnicalDetails title="Import limits and details">
      {closure ? <Typography variant="s3">Complete compiled tree: {closure.fileCount}/200 files · {closure.totalBytes}/2097152 UTF-8 bytes · {closure.linkCount}/20 links, including generated descriptor and mount prefixes.</Typography> : <Typography variant="s3">Whole-context capacity is not yet checked. A supported leaf inventory alone does not certify the compiled context.</Typography>}
      <Typography variant="s3" color="text.secondary">Inventory does not execute source files, change production, issue credentials or certify native-agent delivery.</Typography>
    </TechnicalDetails>
    {items.length ? <TableContainer sx={{ maxWidth: "100%" }}>
      <Table size="small" aria-label="Complete compatibility inventory">
        <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
          <TableCell>Include</TableCell><TableCell>Root and identity</TableCell><TableCell>Files</TableCell><TableCell>Compatibility</TableCell>
        </TableRow></TableHead>
        <TableBody>{items.map((item) => <TableRow hover key={item.key} sx={{ height: ROW_HEIGHT.default }}>
          <TableCell><Checkbox size="small" inputProps={{ "aria-label": `Include ${item.name || item.root}` }} checked={selectedKeys.includes(item.key)} disabled={readOnly || item.blocked || !onSelectionChange} onChange={(event) => onSelectionChange(event.target.checked ? [...selectedKeys, item.key] : selectedKeys.filter((key) => key !== item.key))} /></TableCell>
          <TableCell sx={{ overflowWrap: "anywhere" }}><Typography variant="s1" display="block">{item.name || item.root}</Typography><Typography variant="s3" display="block" color="text.secondary">{item.root} · {item.kind}</Typography></TableCell>
          <TableCell><Typography variant="s1" display="block">{item.fileCount ?? item.files?.length ?? "Unknown"}</Typography><Typography variant="s3" display="block">{item.totalBytes ?? "Unknown"} bytes</Typography></TableCell>
          <TableCell><Typography variant="s1">{item.blocked ? "Blocked" : "Review required"}</Typography>
            {(item.blockers || []).map((reason) => <Typography key={reason} variant="s3" display="block">{reason}</Typography>)}
            {!!item.files?.length && <TechnicalDetails title="Inspect all paths">{item.files.map((file) => <Typography key={file.path} variant="s3" display="block" sx={{ overflowWrap: "anywhere" }}>{file.path} · {file.sizeBytes ?? "unknown"} bytes</Typography>)}</TechnicalDetails>}
          </TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </TableContainer> : <EmptyState variant="no-results" title="No supported roots detected" description="Review the rejected inventory. No files have been imported." />}
    {rejected.length > 0 && <Alert severity="error"><Typography variant="m3">Rejected members</Typography>{rejected.map((item, index) => <Typography key={`${item.path}-${index}`} variant="s3" display="block" sx={{ overflowWrap: "anywhere" }}>{item.path}: {item.reason}</Typography>)}</Alert>}
    {skipped.length > 0 && <Box><Typography variant="m3">Skipped housekeeping</Typography>{skipped.map((item, index) => <Typography key={`${item.path}-${index}`} variant="s3" display="block" sx={{ overflowWrap: "anywhere" }}>{item.path}: {item.reason}</Typography>)}</Box>}
  </Stack>;
}
CompatibilityInventory.propTypes = { items: PropTypes.array, selectedKeys: PropTypes.array, onSelectionChange: PropTypes.func, rejected: PropTypes.array, skipped: PropTypes.array, closure: PropTypes.object, readOnly: PropTypes.bool };
