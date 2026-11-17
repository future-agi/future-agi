import React, { useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { REMOVAL_CATEGORIES } from "./constants";
import { useAddBlock, useHubMe, useLiftBlock, useReviewBlocklist } from "./api/queries";
import { formatDate, shortDigest } from "./lib/format";
import { HubEmpty, HubError, HubSection, HubSkeletonCards } from "./components/HubStates";
import MaintainerGate from "./components/MaintainerGate";

// The blocklist (SPEC §3.4, §5.5): removed entries' digests, plus bare
// digests found outside the Hub. The Hub publishes it (omega-hub-blocklist/v1)
// and the CLI refuses anything on it, so the page names no CLI command.
export default function HubBlocklistPage() {
  const me = useHubMe();
  const blocks = useReviewBlocklist(Boolean(me.data?.isMaintainer));
  const add = useAddBlock();
  const lift = useLiftBlock();
  const [digest, setDigest] = useState("");
  const [category, setCategory] = useState(REMOVAL_CATEGORIES[0]);
  const [note, setNote] = useState("");
  const rows = blocks.data || [];
  return (
    <MaintainerGate title="Blocklist" documentTitle="Blocklist" description="Entries, versions and bundles that may not be installed or run, by reference, digest or bundle hash." testId="hub-blocklist">
      <HubSection title="Block a digest">
        <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", sm: "minmax(0, 1fr) 200px" } }}>
          <TextField size="small" label="Digest (sha256:…) or bundle SHA-256" value={digest} onChange={(e) => setDigest(e.target.value.trim())} />
          <TextField select size="small" label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
            {REMOVAL_CATEGORIES.map((c) => (
              <MenuItem key={c} value={c}>{c}</MenuItem>
            ))}
          </TextField>
          <TextField size="small" label="Private note" value={note} onChange={(e) => setNote(e.target.value)} sx={{ gridColumn: "1 / -1" }} />
        </Box>
        {add.isError && <Alert severity="error">{add.error.message}</Alert>}
        <Box>
          <Button
            size="small"
            variant="contained"
            color="error"
            disabled={!digest || !note.trim() || add.isPending}
            onClick={() =>
              add.mutate(
                { ...(digest.startsWith("sha256:") ? { digest } : { bundleSha256: digest }), category, note: note.trim() },
                { onSuccess: () => { setDigest(""); setNote(""); } },
              )
            }
          >
            Block
          </Button>
        </Box>
      </HubSection>
      {blocks.isLoading ? (
        <HubSkeletonCards count={2} />
      ) : blocks.isError ? (
        <HubError error={blocks.error} onRetry={() => blocks.refetch()} />
      ) : rows.length === 0 ? (
        <HubEmpty title="The blocklist is empty" description="Removing an entry adds its digests here." />
      ) : (
        <Box component="ul" sx={{ m: 0, p: 0, listStyle: "none", border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          {rows.map((row, index) => (
            <Box component="li" key={row.id} sx={{ display: "flex", gap: 1.5, alignItems: "center", px: 2, py: 1.25, borderTop: index ? "1px solid" : 0, borderColor: "divider", flexWrap: "wrap", opacity: row.liftedAt ? 0.6 : 1 }}>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography typography="s2" fontWeight={600} sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}>
                  {row.entry ? `${row.entry}${row.version ? `@${row.version}` : ""}` : shortDigest(row.digest || row.bundleSha256, 16)}
                </Typography>
                <Typography typography="s3" color="text.secondary">
                  {`${row.category} · added ${formatDate(row.addedAt)}${row.addedBy ? ` by ${row.addedBy}` : ""}${row.liftedAt ? ` · lifted ${formatDate(row.liftedAt)}` : ""}${row.note ? ` · ${row.note}` : ""}`}
                </Typography>
              </Box>
              {!row.liftedAt && (
                <Button size="small" disabled={lift.isPending} onClick={() => lift.mutate(row.id)}>Lift</Button>
              )}
            </Box>
          ))}
        </Box>
      )}
    </MaintainerGate>
  );
}
