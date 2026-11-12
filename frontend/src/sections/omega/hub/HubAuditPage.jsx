import React from "react";
import Box from "@mui/material/Box";
import Pagination from "@mui/material/Pagination";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSearchParams } from "react-router-dom";

import { useHubMe, useReviewAudit } from "./api/queries";
import { formatDateTime } from "./lib/format";
import { HubEmpty, HubError, HubSkeletonCards } from "./components/HubStates";
import MaintainerGate from "./components/MaintainerGate";

const PAGE_SIZE = 50;

// The append-only audit log (SPEC §8): every state change, with its actor.
// Seeded approvals carry `via: dev-seed`.
export default function HubAuditPage() {
  const me = useHubMe();
  const [search, setSearch] = useSearchParams();
  const params = {
    action: search.get("action") || undefined,
    targetType: search.get("targetType") || undefined,
    targetId: search.get("targetId") || undefined,
    page: search.get("page") || undefined,
    page_size: PAGE_SIZE,
  };
  const audit = useReviewAudit(params, Boolean(me.data?.isMaintainer));
  const rows = audit.data?.results || [];
  const set = (key, value) => {
    const next = new URLSearchParams(search);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setSearch(next, { replace: true });
  };
  return (
    <MaintainerGate
      title="Audit log"
      documentTitle="Audit"
      description="Every Hub state change, newest first. It cannot be edited."
      testId="hub-audit"
    >
      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
        <TextField
          size="small"
          label="Action"
          placeholder="review.approved"
          value={params.action || ""}
          onChange={(e) => set("action", e.target.value.trim())}
        />
        <TextField
          size="small"
          label="Target type"
          placeholder="submission"
          value={params.targetType || ""}
          onChange={(e) => set("targetType", e.target.value.trim())}
        />
        <TextField
          size="small"
          label="Target id"
          value={params.targetId || ""}
          onChange={(e) => set("targetId", e.target.value.trim())}
        />
      </Box>
      {audit.isLoading ? (
        <HubSkeletonCards count={2} />
      ) : audit.isError ? (
        <HubError error={audit.error} onRetry={() => audit.refetch()} />
      ) : rows.length === 0 ? (
        <HubEmpty title="No events match" />
      ) : (
        <>
          <Box
            component="ul"
            data-testid="hub-audit-rows"
            sx={{
              m: 0,
              p: 0,
              listStyle: "none",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
            }}
          >
            {rows.map((row, index) => (
              <Box
                component="li"
                key={row.id}
                sx={{
                  display: "grid",
                  gridTemplateColumns: {
                    xs: "1fr",
                    sm: "130px 210px minmax(0, 1fr)",
                  },
                  gap: 1,
                  px: 2,
                  py: 1,
                  borderTop: index ? "1px solid" : 0,
                  borderColor: "divider",
                }}
              >
                <Typography typography="s3" color="text.tertiary">
                  {formatDateTime(row.at)}
                </Typography>
                <Typography
                  typography="s2"
                  fontWeight={600}
                  sx={{ fontFamily: "monospace" }}
                >
                  {row.action}
                </Typography>
                <Typography
                  typography="s2"
                  color="text.secondary"
                  sx={{ overflowWrap: "anywhere" }}
                >
                  {`${row.actor || row.actorKind} · ${row.targetType} ${row.targetId}${row.data && Object.keys(row.data).length ? ` · ${JSON.stringify(row.data)}` : ""}`}
                </Typography>
              </Box>
            ))}
          </Box>
          {audit.data.count > PAGE_SIZE && (
            <Box sx={{ display: "flex", justifyContent: "center" }}>
              <Pagination
                size="small"
                count={Math.ceil(audit.data.count / PAGE_SIZE)}
                page={Number(params.page) || 1}
                onChange={(_, page) => set("page", String(page))}
              />
            </Box>
          )}
        </>
      )}
    </MaintainerGate>
  );
}
