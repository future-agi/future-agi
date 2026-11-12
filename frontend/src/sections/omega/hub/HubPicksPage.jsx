import React, { useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { PICK_WHY } from "./constants";
import { useAddPick, useHubMe, useRemovePick, useReviewPicks } from "./api/queries";
import { formatDate, mondayOf } from "./lib/format";
import { HubEmpty, HubError, HubSection, HubSkeletonCards } from "./components/HubStates";
import MaintainerGate from "./components/MaintainerGate";

// Weekly maintainer picks (SPEC §6.4): at most 6 a week, each with a 20–140
// character why that the card shows.
export default function HubPicksPage() {
  const me = useHubMe();
  const picks = useReviewPicks(Boolean(me.data?.isMaintainer));
  const add = useAddPick();
  const remove = useRemovePick();
  const [entry, setEntry] = useState("");
  const [week, setWeek] = useState(mondayOf());
  const [why, setWhy] = useState("");
  const whyOk = why.trim().length >= PICK_WHY[0] && why.trim().length <= PICK_WHY[1];
  const rows = picks.data || [];
  return (
    <MaintainerGate title="Maintainer picks" documentTitle="Picks" description="The strip at the top of the Hub, at most six a week." testId="hub-picks-admin">
      <HubSection title="Add a pick">
        <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", sm: "1fr 160px" } }}>
          <TextField size="small" label="Entry (publisher/name)" value={entry} onChange={(e) => setEntry(e.target.value.trim())} />
          <TextField size="small" label="Week (a Monday)" type="date" value={week} onChange={(e) => setWeek(e.target.value)} InputLabelProps={{ shrink: true }} />
          <TextField size="small" label="Why (shown on the card)" value={why} onChange={(e) => setWhy(e.target.value)} inputProps={{ maxLength: PICK_WHY[1] }} helperText={`${why.trim().length} / ${PICK_WHY[1]}, at least ${PICK_WHY[0]}`} sx={{ gridColumn: "1 / -1" }} />
        </Box>
        {add.isError && <Alert severity="error">{add.error.message}</Alert>}
        <Box>
          <Button size="small" variant="contained" disabled={!entry.includes("/") || !whyOk || add.isPending} onClick={() => add.mutate({ entry, week, why: why.trim() }, { onSuccess: () => { setEntry(""); setWhy(""); } })}>
            Add pick
          </Button>
        </Box>
      </HubSection>
      {picks.isLoading ? (
        <HubSkeletonCards count={2} />
      ) : picks.isError ? (
        <HubError error={picks.error} onRetry={() => picks.refetch()} />
      ) : rows.length === 0 ? (
        <HubEmpty title="No picks yet" />
      ) : (
        <Box component="ul" sx={{ m: 0, p: 0, listStyle: "none", border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          {rows.map((pick, index) => (
            <Box component="li" key={pick.id} sx={{ display: "flex", gap: 1.5, alignItems: "center", px: 2, py: 1.25, borderTop: index ? "1px solid" : 0, borderColor: "divider", flexWrap: "wrap" }}>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography typography="s1" fontWeight={600}>{pick.entry}</Typography>
                <Typography typography="s2" color="text.secondary">{`Week of ${formatDate(pick.week)} · ${pick.why}`}</Typography>
              </Box>
              <Button size="small" color="error" disabled={remove.isPending} onClick={() => remove.mutate(pick.id)}>Remove</Button>
            </Box>
          ))}
        </Box>
      )}
    </MaintainerGate>
  );
}
