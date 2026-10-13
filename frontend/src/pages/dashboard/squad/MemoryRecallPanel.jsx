import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

const SCHEMA_VERSION = "omega-os-memory/v1";
const DROPPED_LIMIT = 50;

function scopesFrom(result) {
  if (
    result?.available !== true ||
    result.schemaVersion !== SCHEMA_VERSION ||
    !Array.isArray(result.items)
  )
    return [];
  const paths = new Set();
  return result.items.filter((scope) => {
    if (
      !scope ||
      typeof scope.path !== "string" ||
      !scope.path.trim() ||
      scope.path !== scope.path.trim() ||
      paths.has(scope.path)
    )
      return false;
    paths.add(scope.path);
    return true;
  });
}

function validPack(result, scope, budget) {
  if (result?.available !== true)
    throw new Error(result?.reason || "The memory context could not be read.");
  const pack = result.recall;
  if (
    result.schemaVersion !== SCHEMA_VERSION ||
    !pack ||
    pack.scope !== scope ||
    typeof pack.access?.granted !== "boolean" ||
    !Number.isSafeInteger(pack.tokenBudget) ||
    pack.tokenBudget < 1 ||
    pack.tokenBudget > budget ||
    !Number.isSafeInteger(pack.tokensUsed) ||
    pack.tokensUsed < 0 ||
    pack.tokensUsed > pack.tokenBudget ||
    !Array.isArray(pack.items) ||
    !Array.isArray(pack.dropped) ||
    pack.items.some(
      (item) =>
        !item ||
        typeof item !== "object" ||
        Array.isArray(item) ||
        typeof item.contentPreview !== "string" ||
        !item.citation ||
        typeof item.citation !== "object" ||
        Array.isArray(item.citation),
    ) ||
    pack.dropped.some(
      (item) =>
        !item ||
        typeof item !== "object" ||
        Array.isArray(item) ||
        (item.contentPreview !== undefined &&
          typeof item.contentPreview !== "string"),
    ) ||
    !Number.isSafeInteger(pack.droppedTotal) ||
    pack.droppedTotal < pack.dropped.length
  ) {
    throw new Error(
      "The server returned a memory context that does not match this request.",
    );
  }
  return pack;
}

const withheld = (item) =>
  item?.citation?.redacted === true ||
  item?.citation?.tombstoned === true ||
  item?.redacted === true ||
  item?.tombstoned === true ||
  ["redacted", "tombstone", "tombstoned"].includes(item?.reason) ||
  ["redacted", "tombstone", "tombstoned"].includes(item?.status) ||
  ["redacted", "tombstone", "tombstoned"].includes(item?.kind);
const text = (value, fallback = "Not reported") =>
  typeof value === "string" && value.length > 0 ? value : fallback;

export default function MemoryRecallPanel({
  client,
  scopesResult,
  maxTokenBudget = 8192,
}) {
  const cap =
    Number.isSafeInteger(maxTokenBudget) && maxTokenBudget > 0
      ? Math.min(maxTokenBudget, 8192)
      : 8192;
  const scopes = scopesFrom(scopesResult);
  const [selectedScope, setSelectedScope] = useState("");
  const scope = scopes.some((candidate) => candidate.path === selectedScope)
    ? selectedScope
    : scopes[0]?.path || "";
  const [budgetInput, setBudgetInput] = useState(String(Math.min(2000, cap)));
  const budget = /^\d+$/.test(budgetInput) ? Number(budgetInput) : NaN;
  const budgetValid =
    Number.isSafeInteger(budget) && budget >= 1 && budget <= cap;
  const [result, setResult] = useState(null);
  const [pending, setPending] = useState(false);
  const requestId = useRef(0);
  const current = useRef(null);
  current.current = { client, scopesResult, scope, budgetInput, cap };
  useEffect(() => {
    requestId.current += 1;
    setPending(false);
    setResult(null);
    return () => {
      requestId.current += 1;
    };
  }, [client, scopesResult, scope, budgetInput, cap]);

  const visible =
    result?.client === client &&
    result.scopesResult === scopesResult &&
    result.scope === scope &&
    result.budgetInput === budgetInput &&
    result.cap === cap
      ? result
      : null;
  const pack = visible?.pack;
  const usable =
    scopesResult?.available === true &&
    scopesResult.schemaVersion === SCHEMA_VERSION &&
    scopes.length > 0 &&
    typeof client?.getMemoryRecall === "function";

  const preview = async () => {
    if (!usable || !budgetValid || pending) return;
    const generation = ++requestId.current;
    const snapshot = current.current;
    const matches = () =>
      generation === requestId.current &&
      current.current.client === snapshot.client &&
      current.current.scopesResult === snapshot.scopesResult &&
      current.current.scope === snapshot.scope &&
      current.current.budgetInput === snapshot.budgetInput &&
      current.current.cap === snapshot.cap;
    setResult(null);
    setPending(true);
    try {
      const response = await client.getMemoryRecall({
        scope,
        tokenBudget: budget,
        limit: DROPPED_LIMIT,
      });
      if (!matches()) return;
      setResult({ ...snapshot, pack: validPack(response, scope, budget) });
    } catch (error) {
      if (matches())
        setResult({
          ...snapshot,
          error:
            error instanceof Error
              ? error.message
              : "The memory context could not be read.",
        });
    } finally {
      if (matches()) setPending(false);
    }
  };

  const dropped =
    pack?.access.granted === true ? pack.dropped.slice(0, DROPPED_LIMIT) : [];
  const dropCounts = dropped.reduce((counts, item) => {
    const reason = text(item?.reason, "unknown");
    counts[reason] = (counts[reason] || 0) + 1;
    return counts;
  }, Object.create(null));
  return (
    <Card
      variant="outlined"
      component="section"
      aria-label="Preview memory context"
    >
      <CardContent>
        <Typography variant="h6">Preview memory context</Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ mt: 0.5, mb: 2 }}
        >
          Preview the stored memory pack from a selected scope within a token
          budget. The pack is ordered by freshness and decay; it is not semantic
          search or a guarantee of exact model context.
        </Typography>
        <Stack spacing={1.5}>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
            <TextField
              fullWidth
              select
              SelectProps={{ native: true }}
              label="Memory scope"
              value={scope}
              disabled={!usable}
              onChange={(event) => {
                setSelectedScope(event.target.value);
                setResult(null);
              }}
            >
              {scopes.length === 0 ? (
                <option value="">No available scopes</option>
              ) : (
                scopes.map((entry) => (
                  <option value={entry.path} key={entry.path}>
                    {entry.path}
                    {typeof entry.kind === "string" ? ` · ${entry.kind}` : ""}
                    {Number.isSafeInteger(entry.memoryCount) &&
                    entry.memoryCount >= 0
                      ? ` · ${entry.memoryCount} memories`
                      : ""}
                  </option>
                ))
              )}
            </TextField>
            <TextField
              type="number"
              label="Token budget"
              value={budgetInput}
              onChange={(event) => {
                setBudgetInput(event.target.value);
                setResult(null);
              }}
              error={!budgetValid}
              helperText={
                !budgetValid
                  ? `Use a whole number from 1 to ${cap}.`
                  : `Maximum ${cap} tokens`
              }
              inputProps={{ min: 1, max: cap, step: 1 }}
              sx={{ minWidth: 190 }}
            />
          </Stack>
          <Box>
            <Button
              variant="outlined"
              onClick={() => void preview()}
              disabled={!usable || !budgetValid || pending}
            >
              {pending ? "Reading context…" : "Preview context"}
            </Button>
          </Box>
          {!usable ? (
            <Alert severity="info">
              {scopesResult?.available === false
                ? scopesResult.reason || "Memory scopes are unavailable."
                : scopes.length === 0
                  ? "No memory scopes are visible in this workspace."
                  : "Memory context preview is unavailable."}
            </Alert>
          ) : null}
          {visible?.error ? (
            <Alert severity="error">{visible.error}</Alert>
          ) : null}
          {pack ? (
            <Box component="section" aria-label="Memory context result">
              <Alert severity={pack.access.granted ? "success" : "warning"}>
                Access {pack.access.granted ? "granted" : "denied"}
                {pack.access.reason ? `: ${pack.access.reason}` : ""}
              </Alert>
              <Typography variant="body2" sx={{ mt: 1 }}>
                {pack.tokensUsed} / {pack.tokenBudget} tokens · Packed{" "}
                {text(pack.packedAt)}
              </Typography>
              {pack.access.granted ? (
                <>
                  {pack.items.length === 0 ? (
                    <Typography color="text.secondary" sx={{ mt: 1 }}>
                      No memories fit this context.
                    </Typography>
                  ) : (
                    <Stack spacing={1} sx={{ mt: 1.5 }}>
                      {pack.items.map((item, index) => (
                        <Box
                          key={`${item.citation?.eventId || "memory"}-${index}`}
                          sx={{
                            p: 1.5,
                            border: 1,
                            borderColor: "divider",
                            borderRadius: 1,
                          }}
                        >
                          <Typography
                            variant="body2"
                            sx={{
                              whiteSpace: "pre-wrap",
                              overflowWrap: "anywhere",
                            }}
                          >
                            {withheld(item)
                              ? "Content withheld."
                              : text(
                                  item.contentPreview,
                                  "Preview unavailable.",
                                )}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{
                              display: "block",
                              mt: 0.75,
                              overflowWrap: "anywhere",
                            }}
                          >
                            Tier: {text(item.citation?.tier)} · By:{" "}
                            {text(item.citation?.producedBy)} · At:{" "}
                            {text(item.citation?.at)}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ overflowWrap: "anywhere" }}
                          >
                            Citation: {text(item.citation?.eventId)}
                          </Typography>
                        </Box>
                      ))}
                    </Stack>
                  )}
                  <Typography variant="subtitle2" sx={{ mt: 2 }}>
                    Dropped candidates: {pack.droppedTotal}
                  </Typography>
                  <Stack
                    direction="row"
                    spacing={1}
                    useFlexGap
                    flexWrap="wrap"
                    sx={{ mt: 0.75 }}
                  >
                    {Object.entries(dropCounts).map(([reason, count]) => (
                      <Chip
                        key={reason}
                        size="small"
                        label={`${reason}: ${count}`}
                      />
                    ))}
                  </Stack>
                  {pack.droppedTotal > dropped.length ? (
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ mt: 1 }}
                    >
                      Showing {dropped.length} of {pack.droppedTotal} dropped
                      candidates. Reason counts describe the returned portion.
                    </Typography>
                  ) : null}
                  {dropped.length ? (
                    <Stack spacing={1} sx={{ mt: 1 }}>
                      {dropped.map((item, index) => (
                        <Box
                          key={`${item.eventId || "dropped"}-${index}`}
                          sx={{
                            pl: 1.5,
                            borderLeft: 2,
                            borderColor: "divider",
                          }}
                        >
                          <Typography variant="body2">
                            {text(item.reason, "unknown")} ·{" "}
                            {text(item.eventId)}
                          </Typography>
                          {withheld(item) ? (
                            <Typography variant="body2" color="text.secondary">
                              Content withheld.
                            </Typography>
                          ) : item.contentPreview ? (
                            <Typography
                              variant="body2"
                              sx={{
                                whiteSpace: "pre-wrap",
                                overflowWrap: "anywhere",
                              }}
                            >
                              {item.contentPreview}
                            </Typography>
                          ) : null}
                        </Box>
                      ))}
                    </Stack>
                  ) : null}
                </>
              ) : null}
            </Box>
          ) : null}
        </Stack>
      </CardContent>
    </Card>
  );
}

MemoryRecallPanel.propTypes = {
  client: PropTypes.shape({ getMemoryRecall: PropTypes.func }).isRequired,
  scopesResult: PropTypes.object,
  maxTokenBudget: PropTypes.number,
};
