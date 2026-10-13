import { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  List,
  ListItem,
  ListItemText,
  Stack,
  Typography,
} from "@mui/material";

const WORKSPACE_AUDIT_SCHEMA = "omega-workspace-audit/v1";
const PAGE_SIZE = 50;
const INTEGRITY_STATES = new Set([
  "not_started",
  "empty",
  "verified",
  "unavailable",
  "broken",
]);
const REQUIRED_EXCLUSIONS = [
  "authorization-decisions",
  "tool-invocations",
  "customer-configuration-mutations",
  "legacy-unscoped-events",
];

const isRecord = (value) =>
  Boolean(value && typeof value === "object" && !Array.isArray(value));
const nonEmpty = (value) =>
  typeof value === "string" &&
  value.trim().length > 0 &&
  value === value.trim();
const optionalString = (value) =>
  value === undefined || value === null || nonEmpty(value);
const nonnegativeInteger = (value) => Number.isSafeInteger(value) && value >= 0;
const optionalInteger = (value) =>
  value === undefined || nonnegativeInteger(value);
const isIso = (value) => nonEmpty(value) && Number.isFinite(Date.parse(value));

function scopeMatches(actual, expected) {
  return (
    isRecord(actual) &&
    ["accountId", "orgId", "workspaceId"].every(
      (key) =>
        nonEmpty(actual[key]) && (!expected || actual[key] === expected[key]),
    )
  );
}

function validIntegrity(integrity, expectedSnapshot) {
  if (
    !isRecord(integrity) ||
    integrity.schemaVersion !== WORKSPACE_AUDIT_SCHEMA ||
    !INTEGRITY_STATES.has(integrity.state)
  )
    return false;
  if (![true, false, null].includes(integrity.ok)) return false;
  if (!(integrity.checkedAt === null || isIso(integrity.checkedAt)))
    return false;
  if (!(integrity.length === null || nonnegativeInteger(integrity.length)))
    return false;
  if (!(integrity.headHash === null || nonEmpty(integrity.headHash)))
    return false;
  if (integrity.verification !== "server-hmac") return false;
  if (!(integrity.snapshot === null || nonEmpty(integrity.snapshot)))
    return false;
  if (integrity.snapshot !== expectedSnapshot) return false;
  if (
    integrity.state === "verified" &&
    (integrity.ok !== true ||
      !nonnegativeInteger(integrity.length) ||
      integrity.length < 1 ||
      !nonEmpty(integrity.headHash) ||
      !nonEmpty(integrity.snapshot))
  )
    return false;
  if (integrity.state === "broken" && integrity.ok !== false) return false;
  if (integrity.state === "unavailable" && integrity.ok !== null) return false;
  if (
    integrity.state === "not_started" &&
    (integrity.ok !== null ||
      integrity.length !== 0 ||
      integrity.headHash !== null ||
      integrity.snapshot !== null)
  )
    return false;
  if (
    integrity.state === "empty" &&
    (integrity.ok !== true ||
      integrity.length !== 0 ||
      integrity.headHash !== null ||
      !nonEmpty(integrity.snapshot))
  )
    return false;
  if (integrity.firstBreak !== undefined) {
    if (
      !isRecord(integrity.firstBreak) ||
      !nonEmpty(integrity.firstBreak.reason)
    )
      return false;
    if (
      !optionalInteger(integrity.firstBreak.index) ||
      !optionalString(integrity.firstBreak.entryId)
    )
      return false;
  }
  return true;
}

function validEntry(entry) {
  if (!isRecord(entry)) return false;
  if (
    !nonEmpty(entry.entryId) ||
    !Number.isSafeInteger(entry.sequence) ||
    entry.sequence < 0
  )
    return false;
  if (
    !isIso(entry.recordedAt) ||
    !nonEmpty(entry.sourceEventId) ||
    !nonEmpty(entry.type) ||
    !isIso(entry.occurredAt) ||
    !nonEmpty(entry.agentId)
  )
    return false;
  if (
    !optionalString(entry.taskId) ||
    !optionalInteger(entry.taskSeq) ||
    !optionalInteger(entry.agentVersion) ||
    !optionalInteger(entry.deploymentVersion)
  )
    return false;
  if (
    !optionalString(entry.approvalId) ||
    !optionalString(entry.approvalDecision)
  )
    return false;
  if (
    !(entry.previousHash === null || nonEmpty(entry.previousHash)) ||
    !nonEmpty(entry.hash) ||
    entry.alg !== "hmac-sha256"
  )
    return false;
  return true;
}

function validCoverage(coverage) {
  if (
    !isRecord(coverage) ||
    coverage.source !== "managed-lifecycle" ||
    coverage.complete !== false
  )
    return false;
  if (
    !(coverage.since === null || isIso(coverage.since)) ||
    !Array.isArray(coverage.exclusions)
  )
    return false;
  return REQUIRED_EXCLUSIONS.every((value) =>
    coverage.exclusions.includes(value),
  );
}

/**
 * The authenticated server establishes native scope on the first page. An
 * optional trusted native scope can narrow it further; host selector IDs are
 * never native identities. Later pages must retain scope and snapshot.
 */
function validateWorkspaceAuditPage(
  result,
  expectedScope,
  expectedSnapshot = undefined,
  seenEntryIds = new Set(),
) {
  if (result?.available !== true)
    throw new Error(result?.reason || "The workspace audit could not be read.");
  if (
    result.schemaVersion !== WORKSPACE_AUDIT_SCHEMA ||
    !scopeMatches(result.scope, expectedScope)
  )
    throw new Error(
      "The workspace audit scope does not match the selected workspace.",
    );
  if (
    !Array.isArray(result.entries) ||
    !validCoverage(result.coverage) ||
    !validIntegrity(result.integrity, result.snapshot)
  )
    throw new Error("The workspace audit response is malformed.");
  if (
    !(result.snapshot === null || nonEmpty(result.snapshot)) ||
    (expectedSnapshot !== undefined && result.snapshot !== expectedSnapshot)
  )
    throw new Error(
      "The workspace audit high-water snapshot changed while paging.",
    );
  if (
    !isRecord(result.page) ||
    !Number.isSafeInteger(result.page.limit) ||
    result.page.limit < 1 ||
    typeof result.page.hasMore !== "boolean"
  )
    throw new Error("The workspace audit page metadata is malformed.");
  if (result.page.hasMore && !nonEmpty(result.page.nextCursor))
    throw new Error("The workspace audit omitted its next cursor.");
  if (!result.page.hasMore && result.page.nextCursor !== undefined)
    throw new Error(
      "The workspace audit returned a cursor after the final page.",
    );
  if (
    result.entries.length > result.page.limit ||
    (result.integrity.state !== "verified" && result.entries.length > 0)
  )
    throw new Error(
      "The workspace audit entries do not match its integrity state.",
    );
  if (
    result.page.hasMore &&
    (result.entries.length === 0 || result.snapshot === null)
  )
    throw new Error(
      "The workspace audit cannot page without a populated snapshot.",
    );
  const ids = new Set(seenEntryIds);
  let previousSequence = -1;
  for (const entry of result.entries) {
    if (
      !validEntry(entry) ||
      ids.has(entry.entryId) ||
      entry.sequence <= previousSequence
    )
      throw new Error(
        "The workspace audit entries are malformed or out of order.",
      );
    ids.add(entry.entryId);
    previousSequence = entry.sequence;
    if (entry.sequence >= result.integrity.length)
      throw new Error(
        "The workspace audit entry exceeds its snapshot high-water mark.",
      );
  }
  return { ...result, entryIds: ids };
}

function validateIntegrityResult(result, expectedSnapshot) {
  if (result?.available !== true)
    throw new Error(
      result?.reason || "Server integrity verification is unavailable.",
    );
  const integrity =
    result.integrity && isRecord(result.integrity) ? result.integrity : result;
  if (!validIntegrity(integrity, expectedSnapshot))
    throw new Error(
      "The server integrity verdict does not match this audit snapshot.",
    );
  return integrity;
}

function stateLabel(state) {
  return (
    {
      not_started: "Not started",
      empty: "Empty chain",
      verified: "Verified",
      unavailable: "Unavailable",
      broken: "Broken",
    }[state] || "Unknown"
  );
}

function stateColor(state) {
  return state === "verified"
    ? "success"
    : state === "broken"
      ? "error"
      : state === "unavailable"
        ? "warning"
        : "default";
}

function resultError(error) {
  return error instanceof Error
    ? error.message
    : "The workspace audit could not be read.";
}

export default function WorkspaceAuditPanel({
  client,
  scopeKey,
  expectedScope,
  pageSize = PAGE_SIZE,
}) {
  const [pages, setPages] = useState([]);
  const [integrity, setIntegrity] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState(null);
  const [integrityError, setIntegrityError] = useState(null);
  const [loadedContext, setLoadedContext] = useState(null);
  const generation = useRef(0);
  const current = useRef({ client, scopeKey });
  const { accountId, orgId, workspaceId } = expectedScope || {};
  const trustedExpectedScope = useMemo(
    () =>
      accountId === undefined &&
      orgId === undefined &&
      workspaceId === undefined
        ? undefined
        : { accountId, orgId, workspaceId },
    [accountId, orgId, workspaceId],
  );
  current.current = { client, scopeKey };

  useEffect(() => {
    const id = ++generation.current;
    setPages([]);
    setIntegrity(null);
    setError(null);
    setIntegrityError(null);
    setLoadedContext(null);
    setLoading(true);
    setLoadingMore(false);
    setVerifying(false);
    const snapshot = { client, scopeKey };
    const matches = () =>
      id === generation.current &&
      current.current.client === snapshot.client &&
      current.current.scopeKey === snapshot.scopeKey;
    if (
      !client ||
      typeof client.getTenantAudit !== "function" ||
      !nonEmpty(scopeKey)
    ) {
      setLoading(false);
      setError("Select an organization and workspace to view the audit.");
      return () => {
        generation.current += 1;
      };
    }
    void client
      .getTenantAudit({ limit: pageSize })
      .then((result) => {
        if (!matches()) return;
        try {
          const page = validateWorkspaceAuditPage(result, trustedExpectedScope);
          setPages([page]);
          setIntegrity(page.integrity);
          setLoadedContext({ client, scopeKey });
        } catch (cause) {
          setError(resultError(cause));
        } finally {
          setLoading(false);
        }
      })
      .catch((cause) => {
        if (!matches()) return;
        setError(resultError(cause));
        setLoading(false);
      });
    return () => {
      generation.current += 1;
    };
  }, [client, pageSize, scopeKey, trustedExpectedScope]);

  // Hide a previous scope's records during the render before its reset effect
  // runs, as well as rejecting any later response from that generation.
  const visiblePages =
    loadedContext?.client === client && loadedContext.scopeKey === scopeKey
      ? pages
      : [];
  const latest = visiblePages[visiblePages.length - 1] || null;
  const entries = visiblePages.flatMap((page) => page.entries);
  const snapshot = visiblePages[0]?.snapshot;
  const visibleIntegrity = visiblePages.length > 0 ? integrity : null;
  const hasMore = latest?.page.hasMore === true;

  const loadMore = async () => {
    if (!latest?.page.nextCursor || loadingMore) return;
    const id = generation.current;
    const expectedSnapshot = visiblePages[0]?.snapshot;
    const expectedScopeKey = current.current.scopeKey;
    setLoadingMore(true);
    try {
      const result = await client.getTenantAudit({
        limit: pageSize,
        cursor: latest.page.nextCursor,
      });
      if (
        id !== generation.current ||
        expectedScopeKey !== current.current.scopeKey ||
        client !== current.current.client
      )
        return;
      const page = validateWorkspaceAuditPage(
        result,
        visiblePages[0].scope,
        expectedSnapshot,
        new Set(entries.map((entry) => entry.entryId)),
      );
      const highwater = visiblePages[0].integrity;
      if (
        page.integrity.length !== highwater.length ||
        page.integrity.headHash !== highwater.headHash
      )
        throw new Error(
          "The workspace audit high-water length or head changed while paging.",
        );
      if (
        page.entries.length &&
        latest.entries.length &&
        page.entries.at(-1).sequence >= latest.entries[0].sequence
      )
        throw new Error(
          "The workspace audit pages overlap or are out of order.",
        );
      setPages((previous) => [...previous, page]);
    } catch (cause) {
      if (
        id === generation.current &&
        expectedScopeKey === current.current.scopeKey &&
        client === current.current.client
      )
        setError(resultError(cause));
    } finally {
      if (
        id === generation.current &&
        expectedScopeKey === current.current.scopeKey &&
        client === current.current.client
      )
        setLoadingMore(false);
    }
  };

  const verify = async () => {
    if (
      verifying ||
      !client ||
      typeof client.getTenantAuditIntegrity !== "function" ||
      snapshot === undefined ||
      snapshot === null
    )
      return;
    const id = generation.current;
    const expectedScopeKey = current.current.scopeKey;
    setVerifying(true);
    setIntegrityError(null);
    try {
      const result = await client.getTenantAuditIntegrity(snapshot);
      if (
        id !== generation.current ||
        expectedScopeKey !== current.current.scopeKey ||
        client !== current.current.client
      )
        return;
      const verdict = validateIntegrityResult(result, snapshot);
      if (verdict.state === "verified" || verdict.state === "empty") {
        const highwater = visiblePages[0].integrity;
        if (
          verdict.length !== highwater.length ||
          verdict.headHash !== highwater.headHash
        )
          throw new Error(
            "The server integrity verdict changed this audit snapshot's length or head.",
          );
      }
      setIntegrity(verdict);
    } catch (cause) {
      if (
        id === generation.current &&
        expectedScopeKey === current.current.scopeKey &&
        client === current.current.client
      ) {
        setIntegrity({
          schemaVersion: WORKSPACE_AUDIT_SCHEMA,
          state: "unavailable",
          ok: null,
          checkedAt: null,
          length: null,
          headHash: null,
          verification: "server-hmac",
          snapshot,
        });
        setIntegrityError(resultError(cause));
      }
    } finally {
      if (
        id === generation.current &&
        expectedScopeKey === current.current.scopeKey &&
        client === current.current.client
      )
        setVerifying(false);
    }
  };

  return (
    <Card variant="outlined" component="section" aria-label="Workspace audit">
      <CardContent>
        <Stack spacing={1.5}>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            justifyContent="space-between"
            gap={1}
          >
            <Box>
              <Typography variant="h6">Workspace audit</Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 0.5 }}
              >
                Server-owned managed lifecycle records for this workspace.
                Coverage is intentionally partial; a valid chain does not make
                this a complete audit.
              </Typography>
            </Box>
            <Chip
              size="small"
              color={stateColor(
                error ? "unavailable" : visibleIntegrity?.state,
              )}
              variant="outlined"
              label={stateLabel(
                error ? "unavailable" : visibleIntegrity?.state,
              )}
            />
          </Stack>
          {loading ? (
            <Typography color="text.secondary">
              Loading owner-bound audit…
            </Typography>
          ) : null}
          {error ? <Alert severity="error">{error}</Alert> : null}
          <Typography variant="subtitle2">Coverage limits</Typography>
          <Typography variant="body2" color="text.secondary">
            Managed lifecycle only. Excluded permanently:{" "}
            {REQUIRED_EXCLUSIONS.join(", ")}.
          </Typography>
          {!loading && !error && visiblePages.length > 0 ? (
            <>
              <Alert severity="info">
                <strong>Server-verified HMAC</strong> reports the server&apos;s
                verdict for this snapshot. Verification checks it again; this is
                not offline proof.
              </Alert>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1}
                alignItems={{ sm: "center" }}
              >
                <Button
                  variant="outlined"
                  onClick={() => void verify()}
                  disabled={
                    verifying || snapshot === undefined || snapshot === null
                  }
                >
                  {verifying ? "Verifying chain…" : "Verify this snapshot"}
                </Button>
                <Typography variant="caption" color="text.secondary">
                  Snapshot: {snapshot || "empty"} · {entries.length} record
                  {entries.length === 1 ? "" : "s"}
                </Typography>
              </Stack>
              {integrityError ? (
                <Alert severity="warning">{integrityError}</Alert>
              ) : null}
              {visibleIntegrity?.state === "broken" &&
              visibleIntegrity.firstBreak ? (
                <Alert severity="error">
                  Chain break: {visibleIntegrity.firstBreak.reason}
                </Alert>
              ) : null}
              <Divider />
              {entries.length === 0 ? (
                <Typography color="text.secondary">
                  No managed lifecycle records are available for this workspace.
                </Typography>
              ) : (
                <List dense disablePadding aria-label="Workspace audit entries">
                  {entries.map((entry) => (
                    <ListItem key={entry.entryId} divider>
                      <ListItemText
                        primary={`${entry.type} · ${entry.entryId}`}
                        secondary={`${entry.occurredAt} · agent ${entry.agentId} · sequence ${entry.sequence}${entry.taskId ? ` · task ${entry.taskId}` : ""}`}
                      />
                    </ListItem>
                  ))}
                </List>
              )}
              {hasMore ? (
                <Button
                  variant="text"
                  onClick={() => void loadMore()}
                  disabled={loadingMore}
                >
                  {loadingMore ? "Loading more…" : "Load more records"}
                </Button>
              ) : null}
            </>
          ) : null}
        </Stack>
      </CardContent>
    </Card>
  );
}

WorkspaceAuditPanel.propTypes = {
  client: PropTypes.shape({
    getTenantAudit: PropTypes.func,
    getTenantAuditIntegrity: PropTypes.func,
  }).isRequired,
  scopeKey: PropTypes.string.isRequired,
  expectedScope: PropTypes.shape({
    accountId: PropTypes.string.isRequired,
    orgId: PropTypes.string.isRequired,
    workspaceId: PropTypes.string.isRequired,
  }),
  pageSize: PropTypes.number,
};
