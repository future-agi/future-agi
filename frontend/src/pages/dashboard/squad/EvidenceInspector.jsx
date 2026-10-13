import { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Grid,
  List,
  ListItem,
  ListItemText,
  Stack,
  Typography,
} from "@mui/material";

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function unwrap(result, key) {
  if (!result?.available) return null;
  if (isRecord(result[key])) return result[key];
  return result;
}

function collection(value, keys) {
  if (!isRecord(value)) return [];
  for (const key of keys) {
    if (Array.isArray(value[key])) return value[key];
  }
  return [];
}

function label(value, fallback) {
  if (typeof value === "string" && value.trim()) return value;
  if (!isRecord(value)) return fallback;
  return (
    value.summary ||
    value.title ||
    value.name ||
    value.kind ||
    value.type ||
    value.id ||
    fallback
  );
}

function compact(value) {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "Unknown";
  try {
    const serialized = JSON.stringify(value);
    return serialized.length > 180
      ? `${serialized.slice(0, 177)}…`
      : serialized;
  } catch {
    return "Remote value unavailable";
  }
}

function EvidenceColumn({ title, tone, items, empty }) {
  return (
    <Card variant="outlined" sx={{ height: "100%" }}>
      <CardContent>
        <Typography variant="subtitle2" color={tone}>
          {title}
        </Typography>
        {items.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {empty}
          </Typography>
        ) : (
          <List dense disablePadding sx={{ mt: 0.5 }}>
            {items.slice(0, 8).map((item, index) => (
              <ListItem
                key={`${item.title}-${index}`}
                disableGutters
                divider={index < Math.min(items.length, 8) - 1}
              >
                <ListItemText primary={item.title} secondary={item.detail} />
              </ListItem>
            ))}
          </List>
        )}
        {items.length > 8 ? (
          <Typography variant="caption" color="text.secondary">
            {items.length - 8} more retained remotely.
          </Typography>
        ) : null}
      </CardContent>
    </Card>
  );
}

EvidenceColumn.propTypes = {
  title: PropTypes.string.isRequired,
  tone: PropTypes.string.isRequired,
  items: PropTypes.arrayOf(
    PropTypes.shape({ title: PropTypes.string, detail: PropTypes.string }),
  ).isRequired,
  empty: PropTypes.string.isRequired,
};

function proofStatus(proofPack) {
  if (!proofPack)
    return {
      label: "Unknown",
      color: "warning",
      detail: "No proof pack loaded.",
    };
  if (proofPack.available === false)
    return {
      label: "Unverified",
      color: "error",
      detail:
        proofPack.reason || "The remote proof pack could not be verified.",
    };
  if (proofPack.verification?.ok === true) {
    const isolation = proofPack.verification.isolation?.status;
    return {
      label: isolation === "verified" ? "Verified + isolated" : "Verified",
      color: isolation === "verified" ? "success" : "warning",
      detail:
        isolation === "verified"
          ? "The remote proof and task-bound cleanup evidence verified offline."
          : "The proof seal verified; task isolation remains unknown.",
    };
  }
  return {
    label: "Unverified",
    color: "error",
    detail: "The remote proof pack is incomplete or failed integrity checks.",
  };
}

export default function EvidenceInspector({
  runId,
  evidence,
  proofPack,
  events,
  observations,
  streamStatus,
}) {
  const [copied, setCopied] = useState(false);
  const [artifactError, setArtifactError] = useState("");
  const evidenceBody = unwrap(evidence, "evidence");
  const proofBody = unwrap(proofPack, "proofPack");
  const status = proofStatus(proofPack);
  const sections = useMemo(() => {
    const observed = [];
    events
      .slice(-8)
      .forEach((event) =>
        observed.push({
          title: `Event: ${event.event || "message"}`,
          detail: event.id ? `Remote event ${event.id}` : "Remote stream event",
        }),
      );
    observations
      .slice(-8)
      .forEach((observation) =>
        observed.push({
          title: label(observation, "Run observation"),
          detail:
            observation.occurredAt ||
            observation.createdAt ||
            "Remote observation",
        }),
      );
    collection(proofBody?.receipts, ["groundTruth"])
      .slice(0, 8)
      .forEach((receipt) =>
        observed.push({
          title: `Ground truth: ${label(receipt, "receipt")}`,
          detail: compact(receipt.authority || receipt.provider || receipt),
        }),
      );
    collection(proofBody, ["artifacts"])
      .slice(0, 8)
      .forEach((artifact) =>
        observed.push({
          title: `Artifact: ${label(artifact, "artifact")}`,
          detail: artifact.sha256
            ? `sha256:${artifact.sha256.slice(0, 12)}…`
            : "Remote artifact reference",
        }),
      );

    const interpreted = [];
    if (typeof proofBody?.answer === "string" && proofBody.answer.trim())
      interpreted.push({ title: "Remote answer", detail: proofBody.answer });
    collection(proofBody?.receipts, ["promotionRefs"]).forEach((receipt) =>
      interpreted.push({
        title: "Promotion reference",
        detail: compact(receipt),
      }),
    );
    if (proofBody?.reconciliation !== undefined)
      interpreted.push({
        title: "Reconciliation",
        detail: compact(proofBody.reconciliation),
      });
    if (evidenceBody?.interpretation !== undefined)
      interpreted.push({
        title: "Remote interpretation",
        detail: compact(evidenceBody.interpretation),
      });

    const unknown = [];
    if (!evidence?.available)
      unknown.push({
        title: "Evidence not loaded",
        detail: evidence?.reason || "Load evidence from the remote run.",
      });
    if (!proofPack?.available)
      unknown.push({ title: "Proof verification", detail: status.detail });
    if (
      proofPack?.available &&
      proofPack.verification?.isolation?.status !== "verified"
    )
      unknown.push({
        title: "Task isolation",
        detail:
          "The proof pack does not contain verified task-bound cleanup evidence.",
      });
    const limitations = collection(evidenceBody, ["limitations"]);
    limitations.forEach((item) =>
      unknown.push({ title: "Remote limitation", detail: compact(item) }),
    );
    if (unknown.length === 0)
      unknown.push({
        title: "No unknowns reported",
        detail: "The remote result did not publish additional limitations.",
      });
    return { observed, interpreted, unknown };
  }, [
    evidence,
    evidenceBody,
    events,
    observations,
    proofBody,
    proofPack,
    status.detail,
  ]);

  const prompt = `Investigate remote Omega run ${runId}. Separate observed evidence from interpretation and unknowns. Cite retained evidence ids and validate the proof/isolation status before proposing the next action.`;
  const copyPrompt = async () => {
    try {
      await globalThis.navigator?.clipboard?.writeText(prompt);
      setCopied(true);
      globalThis.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  const downloadArtifact = (artifact) => {
    setArtifactError("");
    if (
      proofPack?.verification?.ok !== true ||
      artifact?.storage?.embedded !== true ||
      typeof artifact.storage.content !== "string"
    ) {
      setArtifactError(
        "Only embedded artifacts from a verified proof pack can be downloaded in the browser.",
      );
      return;
    }
    if (artifact.storage.content.length > 8_000_000) {
      setArtifactError(
        "This embedded artifact exceeds the bounded browser download limit.",
      );
      return;
    }
    try {
      const binary = atob(artifact.storage.content);
      const bytes = Uint8Array.from(binary, (character) =>
        character.charCodeAt(0),
      );
      const url = URL.createObjectURL(
        new Blob([bytes], {
          type: artifact.contentType || "application/octet-stream",
        }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.name || "omega-artifact";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch {
      setArtifactError(
        "The verified artifact could not be decoded for browser download.",
      );
    }
  };

  const artifacts =
    proofBody && Array.isArray(proofBody.artifacts) ? proofBody.artifacts : [];

  return (
    <Card variant="outlined" sx={{ mt: 1.5 }}>
      <CardContent>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ sm: "center" }}
          gap={1}
        >
          <Box>
            <Typography variant="subtitle2">Investigation view</Typography>
            <Typography variant="caption" color="text.secondary">
              Observed evidence → remote interpretation → unknowns and
              validation.
            </Typography>
          </Box>
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Chip size="small" color={status.color} label={status.label} />
            <Chip
              size="small"
              variant="outlined"
              label={`Stream ${streamStatus}`}
            />
            <Button size="small" onClick={() => void copyPrompt()}>
              {copied ? "Copied" : "Copy follow-up"}
            </Button>
          </Stack>
        </Stack>
        <Alert
          severity={status.color === "error" ? "warning" : "info"}
          sx={{ mt: 1.5 }}
        >
          {status.detail}
        </Alert>
        <Grid container spacing={1.5} sx={{ mt: 0.25 }}>
          <Grid item xs={12} md={4}>
            <EvidenceColumn
              title="Observed"
              tone="success.main"
              items={sections.observed}
              empty="Load remote evidence or proof to inspect observations."
            />
          </Grid>
          <Grid item xs={12} md={4}>
            <EvidenceColumn
              title="Interpreted"
              tone="info.main"
              items={sections.interpreted}
              empty="The remote run has not published an interpretation."
            />
          </Grid>
          <Grid item xs={12} md={4}>
            <EvidenceColumn
              title="Unknowns"
              tone="warning.main"
              items={sections.unknown}
              empty="No unknowns were published."
            />
          </Grid>
        </Grid>
        {artifacts.length > 0 ? (
          <Box sx={{ mt: 1.5 }}>
            <Typography variant="subtitle2">Verified artifacts</Typography>
            <List dense disablePadding sx={{ mt: 0.5 }}>
              {artifacts.slice(0, 8).map((artifact) => (
                <ListItem
                  key={artifact.name || artifact.sha256}
                  divider
                  secondaryAction={
                    artifact.storage?.embedded === true &&
                    proofPack?.verification?.ok === true ? (
                      <Button
                        size="small"
                        onClick={() => downloadArtifact(artifact)}
                      >
                        Download
                      </Button>
                    ) : null
                  }
                >
                  <ListItemText
                    primary={artifact.name || "Remote artifact"}
                    secondary={`${artifact.bytes ?? "unknown"} bytes · ${artifact.storage?.embedded === true ? "embedded and verified" : "remote reference only"}`}
                  />
                </ListItem>
              ))}
            </List>
            {artifactError ? (
              <Alert severity="warning" sx={{ mt: 1 }}>
                {artifactError}
              </Alert>
            ) : null}
          </Box>
        ) : null}
      </CardContent>
    </Card>
  );
}

EvidenceInspector.propTypes = {
  runId: PropTypes.string.isRequired,
  evidence: PropTypes.shape({
    available: PropTypes.bool,
    reason: PropTypes.string,
  }),
  proofPack: PropTypes.shape({
    available: PropTypes.bool,
    reason: PropTypes.string,
    verification: PropTypes.object,
  }),
  events: PropTypes.arrayOf(PropTypes.object).isRequired,
  observations: PropTypes.arrayOf(PropTypes.object).isRequired,
  streamStatus: PropTypes.string.isRequired,
};
