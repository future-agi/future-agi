import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  Grid,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { createOmegaSquadClient } from "src/api/omega-squad/remoteClient";
import WebhookPanel from "./WebhookPanel";
import AgentTeamsPanel from "./AgentTeamsPanel";
import EvidenceInspector from "./EvidenceInspector";
import GovernancePanel from "./GovernancePanel";
import { AgentEditor } from "./AgentEditor";
import { examplesError } from "./agentExamples";
import ConnectorPanel from "./ConnectorPanel";
import IntegrationDiscoveryPanel from "./IntegrationDiscoveryPanel";
import RunConversationMessages from "./RunConversationMessages";
import useAgentEnvironment from "./useAgentEnvironment";
import useAgentModels from "./useAgentModels";
import {
  PRIVATE_AGENT_SHARING,
  readAgentSharing,
  agentSharingLabel,
  useAgentSharingDirectory,
} from "./agentSharing";
import useOmegaSquadConfig from "./useOmegaSquadConfig";
import { importedDefinitionDraft } from "./agentDefinition";
import AgentDefinitionActions from "./AgentDefinitionActions";
import MemoryRecallPanel from "./MemoryRecallPanel";
import ToolPolicyPanel from "./ToolPolicyPanel";
import SkillLibraryPanel from "./SkillLibraryPanel";
import WorkspaceAuditPanel from "./WorkspaceAuditPanel";
import TaskTreeInspector from "./TaskTreeInspector";
import { normalizeSkillRefs, useAgentSkills } from "./useAgentSkills";
import ObserveExportLinks from "./ObserveExportLinks";
import SquadLaunchNav from "./SquadLaunchNav";

const FLEET_STATUS_META = {
  idle: { label: "Ready", color: "success" },
  working: { label: "Working", color: "info" },
  paused: { label: "Paused", color: "warning" },
  needs_attention: { label: "Needs attention", color: "error" },
  offline: { label: "Standing by", color: "default" },
};

function unavailableReason(result) {
  if (result?.reason) return result.reason;
  if (result?.code === "NOT_CONFIGURED")
    return "The remote Omega endpoint is not configured.";
  return "The remote Omega control plane is unavailable.";
}

function isOperatorDenied(result) {
  if (result?.available !== false) return false;
  return (
    [401, 403, 404].includes(result.status) ||
    [
      "AUTH_REQUIRED",
      "HTTP_401",
      "HTTP_403",
      "HTTP_404",
      "FORBIDDEN",
      "route_not_allowed",
      "OPERATOR_ONLY",
    ].includes(result.code)
  );
}

function requestKey(prefix) {
  const random = globalThis.crypto?.randomUUID?.();
  return `${prefix}-${random || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}

const CONFIRMABLE_RUN_CONTROLS = new Set(["cancel", "stop", "kill", "retry"]);

function confirmRunControl(action) {
  if (
    !CONFIRMABLE_RUN_CONTROLS.has(action) ||
    typeof globalThis.confirm !== "function"
  )
    return true;
  return globalThis.confirm(`Confirm remote run action: ${action}?`);
}

function confirmFollowUp() {
  if (typeof globalThis.confirm !== "function") return true;
  return globalThis.confirm(
    "Send this follow-up instruction to the remote run?",
  );
}

function Metric({ label, value, detail }) {
  return (
    <Card variant="outlined" sx={{ height: "100%" }}>
      <CardContent>
        <Typography variant="overline" color="text.secondary">
          {label}
        </Typography>
        <Typography variant="h4" sx={{ mt: 0.5 }}>
          {value}
        </Typography>
        {detail ? (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {detail}
          </Typography>
        ) : null}
      </CardContent>
    </Card>
  );
}

function AgentCard({ agent, selected, onSelect }) {
  const status = agent.status || "offline";
  const statusMeta = FLEET_STATUS_META[status] || {
    label: status,
    color: "default",
  };
  return (
    <Card
      variant="outlined"
      sx={{
        cursor: "pointer",
        borderColor: selected ? "primary.main" : "divider",
        boxShadow: selected ? 2 : 0,
        transition: "border-color 120ms ease, box-shadow 120ms ease",
      }}
      onClick={() => onSelect(agent.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onSelect(agent.id);
      }}
    >
      <CardContent>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="flex-start"
          gap={1}
        >
          <Box minWidth={0}>
            <Typography variant="h6" noWrap title={agent.name}>
              {agent.name}
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ mt: 0.75 }}
            >
              {agent.purpose || agent.role || "Managed Omega agent"}
            </Typography>
          </Box>
          <Chip
            size="small"
            color={statusMeta.color}
            label={statusMeta.label}
          />
        </Stack>
        <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
          <Chip size="small" variant="outlined" label="remote workspace" />
          {agent.currentWork ? (
            <Chip size="small" variant="outlined" label={agent.currentWork} />
          ) : null}
        </Stack>
      </CardContent>
    </Card>
  );
}

function emptyAgentDraft() {
  return {
    name: "",
    role: "",
    purpose: "",
    responsibilities: "",
    workMode: "on_demand",
    modelId: "",
    savedModelId: "",
    modelChanged: false,
    skills: [],
    skillsChanged: false,
    skillsLoaded: true,
    sharing: { ...PRIVATE_AGENT_SHARING, accountIds: [] },
    sharingChanged: false,
    sharingRequired: false,
    environmentId: "",
    environmentRevision: "",
    environmentChanged: false,
    examples: [],
    examplesLoaded: true,
    examplesChanged: false,
    expectedVersion: null,
  };
}

function agentAuthoringFingerprint(draft) {
  return JSON.stringify({
    name: draft.name.trim(),
    role: draft.role.trim(),
    purpose: draft.purpose.trim(),
    workMode: draft.workMode,
    responsibilities:
      draft.sourceResponsibilities?.join("\n") === draft.responsibilities
        ? draft.sourceResponsibilities
        : draft.responsibilities
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean),
  });
}

function agentToDraft(agent) {
  if (!agent) return emptyAgentDraft();
  const examplesLoaded = examplesError(agent.examples) === "";
  const draft = {
    name: agent.name || "",
    role: agent.role || "",
    purpose: agent.purpose || "",
    responsibilities: Array.isArray(agent.responsibilities)
      ? agent.responsibilities.join("\n")
      : "",
    sourceResponsibilities: Array.isArray(agent.responsibilities)
      ? [...agent.responsibilities]
      : undefined,
    workMode: agent.workMode || "on_demand",
    modelId: typeof agent.modelId === "string" ? agent.modelId : "",
    savedModelId: typeof agent.modelId === "string" ? agent.modelId : "",
    modelChanged: false,
    skills:
      Array.isArray(agent.skills) && normalizeSkillRefs(agent.skills).validShape
        ? agent.skills.map((ref) => ({ ...ref }))
        : [],
    skillsChanged: false,
    skillsLoaded:
      Array.isArray(agent.skills) &&
      normalizeSkillRefs(agent.skills).validShape,
    sharing: readAgentSharing(agent.sharing),
    sharingChanged: false,
    sharingRequired: Boolean(readAgentSharing(agent.sharing)),
    environmentId: agent.environment?.id || agent.environmentId || "",
    environmentRevision:
      agent.environment?.revision ?? agent.environmentRevision ?? "",
    examples: examplesLoaded
      ? agent.examples.map((example) => ({ ...example }))
      : [],
    examplesLoaded,
    examplesChanged: false,
    expectedVersion:
      Number.isSafeInteger(agent.version) && agent.version > 0
        ? agent.version
        : null,
    environmentChanged: false,
  };
  return { ...draft, authoredBaseline: agentAuthoringFingerprint(draft) };
}

function rollbackReceipt(result, label) {
  if (result?.receipt && typeof result.receipt === "object")
    return { label, kind: "receipt", value: result.receipt };
  const headers = result?.transport?.headers;
  if (headers && typeof headers === "object") {
    const id =
      headers["x-omega-receipt-id"] ||
      headers["x-omega-operation-id"] ||
      headers["x-operation-id"] ||
      headers["x-request-id"];
    if (id)
      return {
        label,
        kind: "transport",
        value: { id, status: result.transport.status },
      };
  }
  return {
    label,
    kind: "accepted",
    value: { status: result?.transport?.status || "remote" },
  };
}

function AgentHistoryPanel({ agent, detail, capabilities, client, onChanged }) {
  const [state, setState] = useState({
    loading: false,
    revisions: null,
    deployments: null,
    error: null,
    receipt: null,
  });
  const [busy, setBusy] = useState("");
  const historyRequestRef = useRef(0);
  useEffect(() => {
    setState({
      loading: false,
      revisions: null,
      deployments: null,
      error: null,
      receipt: null,
    });
    setBusy("");
    return () => {
      historyRequestRef.current += 1;
    };
  }, [agent?.id, client]);
  const permissions = Array.isArray(capabilities?.permittedActions)
    ? capabilities.permittedActions
    : [];
  const selectedDetail =
    detail?.available && detail.agent?.id === agent?.id ? detail.agent : null;
  const canEdit =
    permissions.includes("update") ||
    selectedDetail?.capabilities?.canEdit === true;
  const canControl =
    permissions.includes("control") ||
    selectedDetail?.capabilities?.canStart === true ||
    selectedDetail?.capabilities?.canPause === true;
  const inspect = async () => {
    if (!agent?.id) return;
    const generation = ++historyRequestRef.current;
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const [revisions, deployments] = await Promise.all([
        client.listWorkforceAgentRevisions(agent.id),
        client.listWorkforceAgentDeployments(agent.id),
      ]);
      if (generation !== historyRequestRef.current) return;
      const failure = [revisions, deployments].find(
        (result) => !result?.available,
      );
      setState({
        loading: false,
        revisions,
        deployments,
        error: failure ? unavailableReason(failure) : null,
        receipt: null,
      });
    } catch {
      if (generation === historyRequestRef.current)
        setState({
          loading: false,
          revisions: null,
          deployments: null,
          error: "Couldn't read this agent's history. Try again.",
          receipt: null,
        });
    }
  };
  const rollbackRevision = async (revision) => {
    if (!agent?.id || !canEdit || !Number.isInteger(revision?.version)) return;
    if (
      typeof globalThis.confirm === "function" &&
      !globalThis.confirm(
        `Roll back ${agent.name} to definition revision ${revision.version}? This creates a new current revision.`,
      )
    )
      return;
    const expectedVersion = state.revisions?.currentVersion;
    if (!Number.isInteger(expectedVersion)) {
      setState((current) => ({
        ...current,
        error:
          "The remote response did not publish a current revision; refresh before rolling back.",
      }));
      return;
    }
    setBusy(`revision-${revision.version}`);
    const result = await client.rollbackWorkforceAgentRevision(
      agent.id,
      revision.version,
      { expectedVersion },
      requestKey(`agent-${agent.id}-revision-rollback-${revision.version}`),
    );
    setBusy("");
    if (!result.available) {
      setState((current) => ({
        ...current,
        error: unavailableReason(result),
        receipt: null,
      }));
      return;
    }
    const receipt = rollbackReceipt(
      result,
      `Definition revision ${revision.version} rollback`,
    );
    await Promise.all([inspect(), onChanged?.(agent.id)]);
    setState((current) => ({ ...current, error: null, receipt }));
  };
  const rollbackDeployment = async (deployment) => {
    if (
      !agent?.id ||
      !canControl ||
      !Number.isInteger(deployment?.version) ||
      deployment.active
    )
      return;
    if (
      typeof globalThis.confirm === "function" &&
      !globalThis.confirm(
        `Roll back ${agent.name}'s deployment to version ${deployment.version}? The active remote process will be replaced.`,
      )
    )
      return;
    const current = state.deployments?.deployment;
    const expectedDeploymentVersion = current?.version;
    const expectedProcessId = current?.processId;
    if (
      !Number.isInteger(expectedDeploymentVersion) ||
      typeof expectedProcessId !== "string" ||
      expectedProcessId.length === 0
    ) {
      setState((currentState) => ({
        ...currentState,
        error:
          "The remote response did not publish the active deployment fence; refresh before rolling back.",
      }));
      return;
    }
    setBusy(`deployment-${deployment.version}`);
    const result = await client.rollbackWorkforceAgentDeployment(
      agent.id,
      deployment.version,
      {
        expectedDeploymentVersion,
        expectedProcessId,
        reason: `Customer requested rollback to deployment version ${deployment.version}.`,
      },
      requestKey(`agent-${agent.id}-deployment-rollback-${deployment.version}`),
    );
    setBusy("");
    if (!result.available) {
      setState((current) => ({
        ...current,
        error: unavailableReason(result),
        receipt: null,
      }));
      return;
    }
    const receipt = rollbackReceipt(
      result,
      `Deployment version ${deployment.version} rollback`,
    );
    await Promise.all([inspect(), onChanged?.(agent.id)]);
    setState((current) => ({ ...current, error: null, receipt }));
  };
  const revisions = collection(state.revisions, "revisions");
  const deployments = collection(state.deployments, "deployments");
  return (
    <Box sx={{ mt: 2 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        gap={1}
      >
        <Box>
          <Typography variant="subtitle2">
            Definition and deployment history
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Inspect immutable remote lineage before choosing a rollback.
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          onClick={() => void inspect()}
          disabled={state.loading || !agent?.id}
        >
          {state.loading ? "Reading…" : "Inspect history"}
        </Button>
      </Stack>
      {state.error ? (
        <Alert severity="error" sx={{ mt: 1.5 }}>
          {state.error}
        </Alert>
      ) : null}
      {state.receipt ? (
        <Alert severity="success" sx={{ mt: 1.5 }}>
          {state.receipt.label}:{" "}
          {state.receipt.kind === "receipt"
            ? "remote receipt recorded"
            : state.receipt.kind === "transport"
              ? `remote operation ${state.receipt.value.id}`
              : "remote operation accepted"}
          .
          <Typography
            component="pre"
            variant="caption"
            sx={{ mt: 0.5, whiteSpace: "pre-wrap" }}
          >
            {JSON.stringify(state.receipt.value)}
          </Typography>
        </Alert>
      ) : null}
      {state.revisions || state.deployments ? (
        <Stack spacing={1.5} sx={{ mt: 1.5 }}>
          <Card variant="outlined">
            <CardContent>
              <Typography variant="subtitle2">Definition revisions</Typography>
              {state.revisions?.available === false ? (
                <Alert severity="info" sx={{ mt: 1 }}>
                  {unavailableReason(state.revisions)}
                </Alert>
              ) : revisions.length === 0 ? (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 1 }}
                >
                  No revisions were returned by remote Omega.
                </Typography>
              ) : (
                <List dense disablePadding sx={{ mt: 0.5 }}>
                  {revisions.map((revision) => (
                    <ListItem
                      key={revision.version || revision.updatedAt}
                      divider
                      secondaryAction={
                        <Button
                          size="small"
                          onClick={() => void rollbackRevision(revision)}
                          disabled={
                            !canEdit ||
                            revision.current ||
                            busy === `revision-${revision.version}`
                          }
                        >
                          {busy === `revision-${revision.version}`
                            ? "Rolling back…"
                            : revision.current
                              ? "Current"
                              : "Roll back"}
                        </Button>
                      }
                    >
                      <ListItemText
                        primary={`Revision ${revision.version}${revision.current ? " · current" : ""}${revision.deployed ? " · deployed" : ""}`}
                        secondary={
                          [
                            revision.name,
                            revision.role,
                            revision.updatedAt || revision.createdAt,
                            revision.purpose,
                          ]
                            .filter(Boolean)
                            .join(" · ") || "Remote definition revision"
                        }
                      />
                    </ListItem>
                  ))}
                </List>
              )}
            </CardContent>
          </Card>
          <Card variant="outlined">
            <CardContent>
              <Typography variant="subtitle2">Deployment lineage</Typography>
              {state.deployments?.available === false ? (
                <Alert severity="info" sx={{ mt: 1 }}>
                  {unavailableReason(state.deployments)}
                </Alert>
              ) : deployments.length === 0 ? (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 1 }}
                >
                  No deployment transitions were returned by remote Omega.
                </Typography>
              ) : (
                <List dense disablePadding sx={{ mt: 0.5 }}>
                  {deployments.map((deployment, index) => (
                    <ListItem
                      key={`${deployment.processId || "deployment"}-${deployment.version || index}`}
                      divider
                      secondaryAction={
                        <Button
                          size="small"
                          onClick={() => void rollbackDeployment(deployment)}
                          disabled={
                            !canControl ||
                            deployment.active ||
                            busy === `deployment-${deployment.version}`
                          }
                        >
                          {busy === `deployment-${deployment.version}`
                            ? "Rolling back…"
                            : deployment.active
                              ? "Active"
                              : "Roll back"}
                        </Button>
                      }
                    >
                      <ListItemText
                        primary={`Version ${deployment.version || "unknown"}${deployment.active ? " · active" : ""}`}
                        secondary={
                          [
                            deployment.action,
                            deployment.processId,
                            deployment.processStatus,
                            deployment.at,
                          ]
                            .filter(Boolean)
                            .join(" · ") || "Remote deployment transition"
                        }
                      />
                    </ListItem>
                  ))}
                </List>
              )}
            </CardContent>
          </Card>
        </Stack>
      ) : null}
    </Box>
  );
}

AgentHistoryPanel.propTypes = {
  agent: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  detail: PropTypes.shape({
    available: PropTypes.bool,
    agent: PropTypes.object,
  }),
  capabilities: PropTypes.shape({
    permittedActions: PropTypes.arrayOf(PropTypes.string),
  }),
  client: PropTypes.object.isRequired,
  onChanged: PropTypes.func,
};

export function AgentControlPanel({
  agent,
  detail,
  capabilities,
  detailLoading,
  onCreate,
  onUpdate,
  onControl,
  client,
  onChanged,
  environmentResult,
  createRequest = 0,
  importRequest = 0,
}) {
  const [editorMode, setEditorMode] = useState("");
  const [draft, setDraft] = useState(emptyAgentDraft);
  const [busy, setBusy] = useState(false);
  const [editorError, setEditorError] = useState("");
  const [editTargetId, setEditTargetId] = useState("");
  const [definitionNotice, setDefinitionNotice] = useState("");
  const permissions = Array.isArray(capabilities?.permittedActions)
    ? capabilities.permittedActions
    : [];
  const canCreate =
    capabilities?.canCreateAgent === true || permissions.includes("create");
  const detailMatchesAgent =
    !detail?.agent?.id || detail.agent.id === agent?.id;
  const canEdit =
    !detailLoading &&
    detailMatchesAgent &&
    (permissions.includes("update") ||
      detail?.agent?.capabilities?.canEdit === true);
  const canControl =
    permissions.includes("control") ||
    detail?.agent?.capabilities?.canStart === true ||
    detail?.agent?.capabilities?.canPause === true;
  const selectedDetail =
    detail?.available && detailMatchesAgent ? detail.agent || detail : null;

  useEffect(() => {
    if (!editorMode && selectedDetail) setDraft(agentToDraft(selectedDetail));
  }, [editorMode, selectedDetail]);
  useEffect(() => {
    setDefinitionNotice("");
  }, [agent?.id]);
  useEffect(() => {
    if (createRequest > 0 && !editorMode) beginCreate();
    // The request counter is owned by the parent and changes only for an explicit user action.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [createRequest]);

  const beginCreate = () => {
    if (!canCreate || busy || editorMode) return;
    setEditorError("");
    setEditTargetId("");
    setDraft({ ...emptyAgentDraft(), sharingRequired: sharingSupported });
    setEditorMode("create");
  };
  const beginEdit = () => {
    if (!canEdit || !agent?.id || busy || editorMode) return;
    setEditorError("");
    setEditTargetId(agent.id);
    setDraft({
      ...agentToDraft(selectedDetail || agent),
      ...(!selectedDetail
        ? { expectedVersion: null, examplesLoaded: false }
        : {}),
    });
    setEditorMode("edit");
  };
  const loadDefinitionDraft = (portable, duplicate) => {
    if (!canCreate || busy || editorMode) return;
    setEditorError("");
    setEditTargetId("");
    setDraft({
      ...emptyAgentDraft(),
      ...importedDefinitionDraft(portable, duplicate),
      sharingRequired: sharingSupported,
    });
    setEditorMode("create");
  };
  const editorAllowed =
    editorMode === "create" ? canCreate : canEdit && editTargetId === agent?.id;
  const environmentSelection = useAgentEnvironment({
    client,
    result: environmentResult,
    draft,
    allowed: editorAllowed,
    open: Boolean(editorMode),
  });
  const environmentValid =
    (editorMode === "edit" && !draft.environmentChanged) ||
    environmentSelection.valid;
  const modelSelection = useAgentModels({
    client,
    open: Boolean(editorMode),
    allowed: editorAllowed,
    modelId: draft.modelId,
  });
  const modelValid =
    (editorMode === "edit" && !draft.modelChanged) || modelSelection.valid;
  const skillsSupported = capabilities?.skillSelection === true;
  const skillsAllowed =
    editorAllowed &&
    skillsSupported &&
    (editorMode === "create" || draft.skillsLoaded);
  const skillSelection = useAgentSkills({
    client,
    open: Boolean(editorMode),
    allowed: skillsAllowed,
    refs: draft.skills,
  });
  const skillsValid =
    (editorMode === "edit" && !draft.skillsChanged) ||
    draft.skills.length === 0 ||
    (skillsSupported && skillSelection.currentValid);
  const sharingSupported =
    Array.isArray(capabilities?.sharingModes) &&
    ["private", "workspace", "selected"].every((mode) =>
      capabilities.sharingModes.includes(mode),
    );
  const sharingCanManage =
    sharingSupported &&
    editorAllowed &&
    (editorMode === "create" ||
      (selectedDetail?.capabilities?.canManageSharing === true &&
        Boolean(readAgentSharing(selectedDetail.sharing))));
  const sharingDirectory = useAgentSharingDirectory({
    client,
    active:
      Boolean(editorMode) &&
      sharingCanManage &&
      draft.sharing?.mode === "selected",
    selectedAccountIds: draft.sharing?.accountIds,
  });
  const sharingValid =
    (!draft.sharingRequired || sharingSupported) &&
    (!sharingSupported ||
      (editorMode === "edit" && !draft.sharingChanged) ||
      Boolean(
        readAgentSharing(draft.sharing) &&
          (draft.sharing.mode !== "selected" ||
            (sharingDirectory.available &&
              draft.sharing.accountIds.every((id) =>
                sharingDirectory.knownCandidates.some(
                  (candidate) => candidate.accountId === id,
                ),
              ))),
      ));
  const handleDraftChange = (changes) => {
    setDraft((current) => ({
      ...current,
      ...changes,
      ...(Object.hasOwn(changes, "modelId") ? { modelChanged: true } : {}),
      ...(Object.hasOwn(changes, "skills")
        ? skillsAllowed
          ? { skillsChanged: true }
          : { skills: current.skills, skillsChanged: current.skillsChanged }
        : {}),
      ...(Object.hasOwn(changes, "sharing")
        ? sharingCanManage
          ? { sharingChanged: true }
          : { sharing: current.sharing, sharingChanged: current.sharingChanged }
        : {}),
      ...(Object.hasOwn(changes, "examples")
        ? current.examplesLoaded
          ? { examplesChanged: true }
          : { examples: current.examples, examplesChanged: false }
        : {}),
      ...(Object.hasOwn(changes, "environmentId") ||
      Object.hasOwn(changes, "environmentRevision")
        ? { environmentChanged: true }
        : {}),
    }));
  };
  const submit = async () => {
    if (
      busy ||
      !editorMode ||
      !editorAllowed ||
      !draft.name.trim() ||
      !draft.role.trim() ||
      !draft.purpose.trim()
    )
      return;
    if (!environmentValid) {
      setEditorError(
        "Choose an exact immutable environment revision before saving.",
      );
      return;
    }
    if (!modelValid) {
      setEditorError("Choose a currently available agent model before saving.");
      return;
    }
    if (!sharingValid || (draft.sharingChanged && !sharingCanManage)) {
      setEditorError(
        "Choose a permitted access policy and current workspace members before saving.",
      );
      return;
    }
    if (!skillsValid || (draft.skillsChanged && !skillsAllowed)) {
      setEditorError(
        "Choose active workspace skill revisions or clear the selection before saving.",
      );
      return;
    }
    if (
      editorMode === "edit" &&
      (!Number.isSafeInteger(draft.expectedVersion) ||
        draft.expectedVersion < 1)
    ) {
      setEditorError(
        "Read the latest agent definition, then reopen Edit before saving.",
      );
      return;
    }
    if (editorMode === "create" || draft.examplesChanged) {
      const error = examplesError(draft.examples);
      if (!draft.examplesLoaded || error) {
        setEditorError(
          error || "Read the complete examples before changing them.",
        );
        return;
      }
    }
    const definitionPayload = {
      name: draft.name.trim(),
      role: draft.role.trim(),
      purpose: draft.purpose.trim(),
      responsibilities:
        draft.sourceResponsibilities?.join("\n") === draft.responsibilities
          ? [...draft.sourceResponsibilities]
          : draft.responsibilities
              .split("\n")
              .map((line) => line.trim())
              .filter(Boolean),
      workMode: draft.workMode,
      ...(draft.modelId && (editorMode === "create" || draft.modelChanged)
        ? { modelId: draft.modelId }
        : {}),
      ...(skillsSupported &&
      ((editorMode === "create" && draft.skills.length > 0) ||
        (editorMode === "edit" && draft.skillsChanged))
        ? { skills: draft.skills.map(({ id, revision }) => ({ id, revision })) }
        : {}),
      ...(sharingSupported && (editorMode === "create" || draft.sharingChanged)
        ? {
            sharing: {
              mode: draft.sharing.mode,
              accountIds: [...draft.sharing.accountIds],
            },
          }
        : {}),
      ...((editorMode === "create" || draft.environmentChanged) &&
      draft.environmentId
        ? {
            environmentId: draft.environmentId,
            environmentRevision: draft.environmentRevision,
          }
        : editorMode === "edit" && draft.environmentChanged
          ? { environmentId: null, environmentRevision: null }
          : {}),
      ...(editorMode === "create"
        ? { connectorBindingIds: [] }
        : { expectedVersion: draft.expectedVersion }),
      ...((editorMode === "create" && draft.examples.length > 0) ||
      (editorMode === "edit" && draft.examplesChanged)
        ? {
            examples: draft.examples.map(({ prompt, response }) => ({
              prompt: prompt.trim(),
              response: response.trim(),
            })),
          }
        : {}),
    };
    const sharingOnly =
      editorMode === "edit" &&
      draft.sharingChanged &&
      draft.authoredBaseline === agentAuthoringFingerprint(draft) &&
      !draft.modelChanged &&
      !draft.skillsChanged &&
      !draft.examplesChanged &&
      !draft.environmentChanged;
    const payload = sharingOnly
      ? {
          expectedVersion: draft.expectedVersion,
          sharing: definitionPayload.sharing,
        }
      : definitionPayload;
    setBusy(true);
    setEditorError("");
    try {
      const response =
        editorMode === "edit"
          ? await onUpdate(editTargetId, payload)
          : await onCreate(payload);
      if (response?.available) {
        if (editorMode === "edit")
          setDefinitionNotice(
            draft.sharingChanged
              ? "Access updated immediately. Deploy separately to apply any other definition changes."
              : "Definition saved. Pause a running agent, then start it to deploy these changes.",
          );
        setEditorMode("");
      } else setEditorError(unavailableReason(response));
    } catch {
      setEditorError(
        "We couldn't save this agent. Your draft is still here; try again.",
      );
    } finally {
      setBusy(false);
    }
  };
  const control = async (action) => {
    if (!agent?.id) return;
    setBusy(true);
    await onControl(agent.id, action);
    setBusy(false);
  };

  const editor = editorMode ? (
    <AgentEditor
      key={`${editorMode}-${editTargetId}`}
      mode={editorMode}
      draft={draft}
      onChange={handleDraftChange}
      environmentSelection={environmentSelection}
      environmentValid={environmentValid}
      modelSelection={modelSelection}
      modelValid={modelValid}
      skillsSupported={skillsSupported}
      skillsAllowed={skillsAllowed}
      skillSelection={skillSelection}
      skillsValid={skillsValid && (!draft.skillsChanged || skillsAllowed)}
      sharingSupported={sharingSupported}
      sharingCanManage={sharingCanManage}
      sharingDirectory={sharingDirectory}
      sharingValid={sharingValid && (!draft.sharingChanged || sharingCanManage)}
      onCancel={() => {
        setEditorMode("");
        setEditorError("");
      }}
      onSubmit={() => void submit()}
      busy={busy}
      error={editorError}
      allowed={editorAllowed}
      assistantClient={client}
    />
  ) : null;

  if (!agent && !canCreate && !editorMode)
    return (
      <Typography color="text.secondary" sx={{ mt: 2 }}>
        Select a managed agent to inspect its lifecycle.
      </Typography>
    );
  if (!agent) {
    return (
      <Box sx={{ mt: 2 }}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ sm: "center" }}
          gap={1}
        >
          <Typography color="text.secondary">
            Create a tenant-scoped managed agent to add it to this squad.
          </Typography>
          <Button
            variant="contained"
            onClick={beginCreate}
            disabled={busy || Boolean(editorMode) || !canCreate}
          >
            Create agent
          </Button>
        </Stack>
        <AgentDefinitionActions
          client={client}
          canCreate={canCreate}
          disabled={busy || Boolean(editorMode)}
          onDraft={loadDefinitionDraft}
          importRequest={importRequest}
        />
        {editor}
      </Box>
    );
  }

  const status = agent.status || "offline";
  const canStart =
    canControl && ["offline", "paused", "needs_attention"].includes(status);
  const canPause = canControl && ["idle", "working"].includes(status);
  return (
    <Box sx={{ mt: 2 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        gap={1}
      >
        <Box>
          <Typography variant="subtitle1" fontWeight={600}>
            Managed-agent lifecycle
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {detailLoading
              ? "Reading the remote agent record…"
              : `${agent.name} · ${agent.role || "managed agent"}${agent.reason ? ` · ${agent.reason.replaceAll("_", " ")}` : ""}`}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          {canCreate ? (
            <Button
              size="small"
              variant="outlined"
              onClick={beginCreate}
              disabled={busy || Boolean(editorMode)}
            >
              Create agent
            </Button>
          ) : null}
          <Button
            size="small"
            onClick={beginEdit}
            disabled={!canEdit || busy || Boolean(editorMode)}
          >
            Edit
          </Button>
          {canStart ? (
            <Button
              size="small"
              variant="contained"
              onClick={() => void control("start")}
              disabled={busy}
            >
              {status === "offline" ? "Deploy / start" : "Resume deployment"}
            </Button>
          ) : null}
          {canPause ? (
            <Button
              size="small"
              variant="outlined"
              onClick={() => void control("pause")}
              disabled={busy}
            >
              Pause
            </Button>
          ) : null}
        </Stack>
      </Stack>
      {definitionNotice ? (
        <Alert severity="info" sx={{ mt: 1.5 }}>
          {definitionNotice}
        </Alert>
      ) : null}
      {readAgentSharing(selectedDetail?.sharing) ? (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          Access: {agentSharingLabel(selectedDetail.sharing)}.
        </Typography>
      ) : null}
      {Array.isArray(selectedDetail?.skills) ? (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          Saved skill revisions: {selectedDetail.skills.length}. Active
          deployment:{" "}
          {Array.isArray(selectedDetail.deployedSkills)
            ? selectedDetail.deployedSkills.length
            : "Not reported"}
          .
        </Typography>
      ) : null}
      {typeof selectedDetail?.modelId === "string" ? (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ mt: 1.5, overflowWrap: "anywhere" }}
        >
          Saved model: {selectedDetail.modelId}. Active deployment model:{" "}
          {typeof selectedDetail.deployedModelId === "string"
            ? selectedDetail.deployedModelId
            : "Not reported"}
          .
        </Typography>
      ) : null}
      <AgentDefinitionActions
        agentId={agent.id}
        client={client}
        canCreate={canCreate}
        disabled={busy || Boolean(editorMode)}
        onDraft={loadDefinitionDraft}
        importRequest={importRequest}
      />
      {detail?.available === false ? (
        <Alert severity="info" sx={{ mt: 1.5 }}>
          {unavailableReason(detail)}
        </Alert>
      ) : null}
      {selectedDetail?.activity?.length ? (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 1 }}
        >
          Latest activity:{" "}
          {selectedDetail.activity[0].summary || "Remote lifecycle update"} (
          {selectedDetail.activity[0].occurredAt || "time unavailable"})
        </Typography>
      ) : null}
      {editor}
      <AgentHistoryPanel
        key={agent.id}
        agent={agent}
        detail={detail}
        capabilities={capabilities}
        client={client}
        onChanged={onChanged}
      />
      {typeof client?.getWorkforceAgentTools === "function" ? (
        <ToolPolicyPanel
          agentId={agent.id}
          client={client}
          canEdit={canEdit}
          onSaved={() => onChanged?.(agent.id)}
        />
      ) : null}
    </Box>
  );
}

AgentControlPanel.propTypes = {
  agent: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    role: PropTypes.string,
    status: PropTypes.string,
    reason: PropTypes.string,
  }),
  detail: PropTypes.shape({
    available: PropTypes.bool,
    agent: PropTypes.object,
  }),
  capabilities: PropTypes.shape({
    canCreateAgent: PropTypes.bool,
    permittedActions: PropTypes.arrayOf(PropTypes.string),
    sharingModes: PropTypes.arrayOf(PropTypes.string),
    skillSelection: PropTypes.bool,
  }),
  detailLoading: PropTypes.bool.isRequired,
  onCreate: PropTypes.func.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onControl: PropTypes.func.isRequired,
  client: PropTypes.object.isRequired,
  onChanged: PropTypes.func,
  environmentResult: PropTypes.shape({
    available: PropTypes.bool,
    environments: PropTypes.arrayOf(PropTypes.object),
  }),
  createRequest: PropTypes.number,
  importRequest: PropTypes.number,
};

function RunRow({ run, busyId, onControl, onSelect }) {
  const id = run.id || run.conversationId;
  const status = run.status || run.phase || "unknown";
  const normalizedStatus = String(status).toLowerCase();
  const terminal = [
    "done",
    "completed",
    "complete",
    "succeeded",
    "success",
    "failed",
    "error",
    "stopped",
    "cancelled",
    "canceled",
  ].includes(normalizedStatus);
  const paused = ["paused", "parked", "interrupted"].includes(normalizedStatus);
  return (
    <Box sx={{ py: 1.25 }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", sm: "center" }}
        gap={1}
      >
        <Box minWidth={0}>
          <Typography variant="body2" fontWeight={600} noWrap title={id}>
            {run.title || id || "Remote run"}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {run.updatedAt || run.createdAt || "Remote managed run"}
          </Typography>
        </Box>
        <Stack direction="row" spacing={0.75} alignItems="center">
          <Chip size="small" variant="outlined" label={status} />
          <Button size="small" onClick={() => onSelect(id)} disabled={!id}>
            Details
          </Button>
          {!terminal && !paused ? (
            <Button
              size="small"
              onClick={() => onControl(id, "interrupt")}
              disabled={!id || busyId === id}
            >
              Pause
            </Button>
          ) : null}
          {paused ? (
            <Button
              size="small"
              onClick={() => onControl(id, "continue")}
              disabled={!id || busyId === id}
            >
              Resume
            </Button>
          ) : null}
          {!terminal ? (
            <Button
              size="small"
              color="error"
              onClick={() => onControl(id, "cancel")}
              disabled={!id || busyId === id}
            >
              Cancel
            </Button>
          ) : null}
          {!terminal ? (
            <Button
              size="small"
              color="error"
              onClick={() => onControl(id, "stop")}
              disabled={!id || busyId === id}
            >
              Stop
            </Button>
          ) : null}
          {!terminal ? (
            <Button
              size="small"
              color="error"
              onClick={() => onControl(id, "kill")}
              disabled={!id || busyId === id}
            >
              Kill
            </Button>
          ) : null}
          {["failed", "error", "stopped", "cancelled", "canceled"].includes(
            normalizedStatus,
          ) ? (
            <Button
              size="small"
              onClick={() => onControl(id, "retry")}
              disabled={!id || busyId === id}
            >
              Retry
            </Button>
          ) : null}
        </Stack>
      </Stack>
    </Box>
  );
}

function parseSseEvent(block) {
  const fields = block.split(/\r?\n/);
  let id;
  let event = "message";
  const data = [];
  fields.forEach((line) => {
    if (!line || line.startsWith(":")) return;
    const separator = line.indexOf(":");
    const key = separator === -1 ? line : line.slice(0, separator);
    const value =
      separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (key === "id") id = value;
    if (key === "event") event = value || "message";
    if (key === "data") data.push(value);
  });
  if (data.length === 0 && id === undefined) return null;
  const raw = data.join("\n");
  let value = raw;
  try {
    value = raw ? JSON.parse(raw) : null;
  } catch {
    /* preserve text event data */
  }
  return { id, event, data: value, raw };
}

async function consumeSse(response, onEvent, signal) {
  if (!response?.body?.getReader) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (!signal.aborted) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";
    blocks.map(parseSseEvent).filter(Boolean).forEach(onEvent);
  }
  buffer += decoder.decode();
  const finalEvent = parseSseEvent(buffer.trim());
  if (finalEvent) onEvent(finalEvent);
}

function collection(result, key, fallback = []) {
  if (!result?.available) return fallback;
  if (Array.isArray(result[key])) return result[key];
  if (Array.isArray(result.items)) return result.items;
  return fallback;
}

function remoteLabel(value, fallback = "Remote resource") {
  if (!value || typeof value !== "object") return fallback;
  return (
    value.name ||
    value.title ||
    value.label ||
    value.id ||
    value.key ||
    fallback
  );
}

function RemoteUnavailable({ result, children }) {
  if (!result) return children;
  if (result.available === false) {
    return <Alert severity="info">{unavailableReason(result)}</Alert>;
  }
  return children;
}

// EE returns PendingApproval rows with the decision authority nested under
// `approval`; the surrounding task/run ids are context, never approval ids.
function approvalRequest(row) {
  return row?.approval && typeof row.approval === "object" ? row.approval : row;
}

export function ApprovalPanel({ result, busyId, onResolve }) {
  const approvals = collection(result, "approvals");
  return (
    <RemoteUnavailable result={result}>
      {approvals.length === 0 ? (
        <Typography color="text.secondary">
          No pending approvals in this remote workspace.
        </Typography>
      ) : (
        <List disablePadding>
          {approvals.map((row) => {
            const approval = approvalRequest(row);
            const id = approval.id || approval.approvalId;
            const status = approval.status || approval.state || "pending";
            return (
              <ListItem
                key={id || remoteLabel(approval)}
                divider
                secondaryAction={
                  <Stack direction="row" spacing={1}>
                    <Button
                      size="small"
                      variant="contained"
                      onClick={() => onResolve(id, "approve")}
                      disabled={!id || busyId === id}
                    >
                      Approve
                    </Button>
                    <Button
                      size="small"
                      color="error"
                      variant="outlined"
                      onClick={() => onResolve(id, "deny")}
                      disabled={!id || busyId === id}
                    >
                      Reject
                    </Button>
                  </Stack>
                }
              >
                <ListItemText
                  primary={remoteLabel(approval, "Approval request")}
                  secondary={`${approval.detail || approval.reason || approval.description || approval.proposedAction?.summary || "Remote agent action requires review"} · ${status}`}
                />
              </ListItem>
            );
          })}
        </List>
      )}
    </RemoteUnavailable>
  );
}

function emptyEnvironmentDraft() {
  return {
    name: "",
    sandboxKind: "container",
    pinnedImageDigest: "",
    resourcePreset: "runtime-default",
    browserOrigins: "",
    credentialBindingIds: "",
  };
}

function environmentToDraft(environment) {
  if (!environment) return emptyEnvironmentDraft();
  return {
    name: environment.name || "",
    sandboxKind: environment.sandboxKind || "container",
    pinnedImageDigest: environment.pinnedImageDigest || "",
    resourcePreset: environment.resourcePreset || "runtime-default",
    browserOrigins: Array.isArray(environment.browserOrigins)
      ? environment.browserOrigins.join("\n")
      : "",
    credentialBindingIds: Array.isArray(environment.credentialBindingIds)
      ? environment.credentialBindingIds.join("\n")
      : "",
  };
}

function EnvironmentEditor({
  mode,
  draft,
  onChange,
  onCancel,
  onSubmit,
  busy,
}) {
  const isEdit = mode === "edit";
  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent>
        <Typography variant="subtitle1" fontWeight={600} sx={{ mb: 1.5 }}>
          {isEdit
            ? "Create an immutable environment revision"
            : "Create a remote environment"}
        </Typography>
        <Stack spacing={1.25}>
          <TextField
            fullWidth
            required
            label="Name"
            value={draft.name}
            onChange={(event) => onChange({ name: event.target.value })}
          />
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.25}>
            <TextField
              fullWidth
              select
              label="Sandbox"
              value={draft.sandboxKind}
              disabled
            >
              <MenuItem value="container">Remote container</MenuItem>
            </TextField>
            <TextField
              fullWidth
              select
              label="Resources"
              value={draft.resourcePreset}
              onChange={(event) =>
                onChange({ resourcePreset: event.target.value })
              }
            >
              {[
                ["runtime-default", "Runtime default"],
                ["small", "Small"],
                ["medium", "Medium"],
                ["large", "Large"],
              ].map(([value, label]) => (
                <MenuItem key={value} value={value}>
                  {label}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
          <TextField
            fullWidth
            required
            label="Pinned image digest"
            value={draft.pinnedImageDigest}
            onChange={(event) =>
              onChange({ pinnedImageDigest: event.target.value })
            }
            placeholder="sha256:…"
            helperText="A mutable tag is not accepted by the remote environment contract."
          />
          <TextField
            fullWidth
            multiline
            minRows={2}
            label="Browser origins"
            value={draft.browserOrigins}
            onChange={(event) =>
              onChange({ browserOrigins: event.target.value })
            }
            helperText="One exact HTTP(S) origin per line; leave empty unless the remote runtime needs browser access."
          />
          <TextField
            fullWidth
            multiline
            minRows={2}
            label="Credential binding IDs"
            value={draft.credentialBindingIds}
            onChange={(event) =>
              onChange({ credentialBindingIds: event.target.value })
            }
            helperText="Value-free server-owned binding IDs only. Never paste credentials here."
          />
          <Stack direction="row" justifyContent="flex-end" spacing={1}>
            <Button size="small" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              onClick={onSubmit}
              disabled={
                busy ||
                !draft.name.trim() ||
                !/^sha256:[a-f0-9]{64}$/u.test(draft.pinnedImageDigest.trim())
              }
            >
              {busy
                ? "Saving remotely…"
                : isEdit
                  ? "Create revision"
                  : "Create environment"}
            </Button>
          </Stack>
        </Stack>
      </CardContent>
    </Card>
  );
}

EnvironmentEditor.propTypes = {
  mode: PropTypes.oneOf(["create", "edit"]).isRequired,
  draft: PropTypes.shape({
    name: PropTypes.string,
    sandboxKind: PropTypes.string,
    pinnedImageDigest: PropTypes.string,
    resourcePreset: PropTypes.string,
    browserOrigins: PropTypes.string,
    credentialBindingIds: PropTypes.string,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  onSubmit: PropTypes.func.isRequired,
  busy: PropTypes.bool.isRequired,
};

export function EnvironmentPanel({
  result,
  revisionsById,
  revisionsLoadingId,
  onInspectRevisions,
  onControl,
  onCreate,
  onRevise,
  busyId,
}) {
  const environments = collection(result, "environments");
  const [editorMode, setEditorMode] = useState("");
  const [draft, setDraft] = useState(emptyEnvironmentDraft);

  const submit = async () => {
    const input = {
      name: draft.name.trim(),
      sandboxKind: "container",
      pinnedImageDigest: draft.pinnedImageDigest.trim(),
      resourcePreset: draft.resourcePreset,
      browserOrigins: draft.browserOrigins
        .split("\n")
        .map((value) => value.trim())
        .filter(Boolean),
      credentialBindingIds: draft.credentialBindingIds
        .split("\n")
        .map((value) => value.trim())
        .filter(Boolean),
    };
    const selected = environments.find(
      (environment) =>
        (environment.id || environment.environmentId) === draft.environmentId,
    );
    const response =
      editorMode === "edit" && selected
        ? await onRevise(selected.id || selected.environmentId, {
            ...input,
            baseRevision: selected.revision ?? selected.version,
          })
        : await onCreate(input);
    if (response?.available) {
      setEditorMode("");
      setDraft(emptyEnvironmentDraft());
    }
  };

  return (
    <RemoteUnavailable result={result}>
      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 2 }}>
        <Button
          variant="contained"
          onClick={() => {
            setDraft(emptyEnvironmentDraft());
            setEditorMode("create");
          }}
        >
          Create remote environment
        </Button>
      </Stack>
      {editorMode ? (
        <EnvironmentEditor
          mode={editorMode}
          draft={draft}
          onChange={(changes) =>
            setDraft((current) => ({ ...current, ...changes }))
          }
          onCancel={() => setEditorMode("")}
          onSubmit={() => void submit()}
          busy={busyId === "environment-editor"}
        />
      ) : null}
      {environments.length === 0 ? (
        <Typography color="text.secondary">
          No remote environments have been provisioned.
        </Typography>
      ) : (
        <Stack spacing={1.5}>
          {environments.map((environment) => {
            const id = environment.id || environment.environmentId;
            const state = environment.status || environment.state || "unknown";
            const normalizedState = String(state).toLowerCase();
            const paused = ["paused", "hibernated", "stopped"].includes(
              normalizedState,
            );
            const retired = ["retired", "destroyed"].includes(normalizedState);
            return (
              <Card variant="outlined" key={id || remoteLabel(environment)}>
                <CardContent sx={{ "&:last-child": { pb: 2 } }}>
                  <Stack
                    direction={{ xs: "column", sm: "row" }}
                    justifyContent="space-between"
                    alignItems={{ sm: "center" }}
                    gap={1}
                  >
                    <Box>
                      <Typography fontWeight={600}>
                        {remoteLabel(environment, "Remote environment")}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {environment.description ||
                          environment.runtime ||
                          "Tenant-scoped managed environment"}
                      </Typography>
                    </Box>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Chip size="small" variant="outlined" label={state} />
                      <Button
                        size="small"
                        onClick={() => {
                          setDraft({
                            ...environmentToDraft(environment),
                            environmentId: id,
                          });
                          setEditorMode("edit");
                        }}
                        disabled={
                          !id || retired || normalizedState === "active"
                        }
                      >
                        New revision
                      </Button>
                      <Button
                        size="small"
                        onClick={() => onInspectRevisions(id)}
                        disabled={!id || revisionsLoadingId === id}
                      >
                        {revisionsLoadingId === id ? "Reading…" : "Revisions"}
                      </Button>
                      <Button
                        size="small"
                        onClick={() =>
                          onControl(
                            id,
                            paused ? "activate" : "pause",
                            environment.revision ?? environment.version,
                          )
                        }
                        disabled={
                          !id ||
                          retired ||
                          !Number.isInteger(
                            environment.revision ?? environment.version,
                          )
                        }
                      >
                        {paused ? "Activate" : retired ? "Retired" : "Pause"}
                      </Button>
                    </Stack>
                  </Stack>
                  {id && revisionsById?.[id] ? (
                    <Box sx={{ mt: 1.5 }}>
                      <Typography variant="subtitle2">
                        Immutable revisions
                      </Typography>
                      {revisionsById[id].available === false ? (
                        <Alert severity="info" sx={{ mt: 1 }}>
                          {unavailableReason(revisionsById[id])}
                        </Alert>
                      ) : (
                        <List dense disablePadding sx={{ mt: 0.5 }}>
                          {collection(revisionsById[id], "revisions").map(
                            (revision) => (
                              <ListItem
                                key={
                                  revision.revision ||
                                  revision.version ||
                                  revision.createdAt
                                }
                                divider
                              >
                                <ListItemText
                                  primary={
                                    "Revision " +
                                    (revision.revision ??
                                      revision.version ??
                                      "unknown") +
                                    (revision.current ? " · current" : "")
                                  }
                                  secondary={
                                    [
                                      revision.name,
                                      revision.sandboxKind,
                                      revision.pinnedImageDigest,
                                      revision.createdAt,
                                    ]
                                      .filter(Boolean)
                                      .join(" · ") || "Remote revision metadata"
                                  }
                                />
                              </ListItem>
                            ),
                          )}
                        </List>
                      )}
                    </Box>
                  ) : null}
                </CardContent>
              </Card>
            );
          })}
        </Stack>
      )}
    </RemoteUnavailable>
  );
}

const SCHEDULE_CADENCE_PRESETS = [
  ["hourly", "Every hour"],
  ["daily", "Every day (09:00 UTC)"],
  ["weekdays", "Weekdays (09:00 UTC)"],
  ["weekly", "Every Monday (09:00 UTC)"],
  ["monthly", "First day of each month (09:00 UTC)"],
];

const SCHEDULE_KINDS = [
  "deck",
  "site",
  "sheet",
  "viz",
  "doc",
  "research",
  "design",
  "video",
  "audio",
  "image",
  "app",
];

function emptyScheduleDraft() {
  return {
    title: "",
    prompt: "",
    cadence: "daily",
    customCadence: "",
    kind: "",
    enabled: true,
    agentId: "",
    unsupportedAutomation: false,
    originalCadence: null,
    originalAutomation: null,
    originalAgentId: "",
  };
}

function scheduleToDraft(schedule) {
  if (!schedule) return emptyScheduleDraft();
  const cadence = schedule.cadence;
  const cadenceValue =
    typeof cadence === "string"
      ? cadence
      : cadence?.kind === "cron"
        ? cadence.expr
        : cadence?.kind === "interval" && Number.isFinite(cadence.ms)
          ? `every ${Math.max(1, Math.round(cadence.ms / 60000))}m`
          : "daily";
  const preset = SCHEDULE_CADENCE_PRESETS.some(
    ([value]) => value === cadenceValue,
  )
    ? cadenceValue
    : "custom";
  const automation = schedule.automation;
  const actions = Array.isArray(automation?.actions) ? automation.actions : [];
  const agentId =
    automation?.trigger?.kind === "time" &&
    actions.length === 1 &&
    actions[0]?.kind === "run_agent" &&
    typeof actions[0]?.agentId === "string"
      ? actions[0].agentId
      : "";
  return {
    title: schedule.title || "",
    prompt: schedule.prompt || "",
    cadence: preset,
    customCadence: preset === "custom" ? cadenceValue : "",
    kind: schedule.kind || "",
    enabled: schedule.enabled !== false,
    agentId,
    unsupportedAutomation:
      automation !== undefined &&
      (automation?.schemaVersion !== "omega-automation/v1" || !agentId),
    originalCadence: cadence,
    originalAutomation: automation || null,
    originalAgentId: agentId,
  };
}

function ScheduleEditor({
  mode,
  draft,
  agents,
  onChange,
  onCancel,
  onSubmit,
  busy,
}) {
  const isEdit = mode === "edit";
  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent>
        <Typography variant="subtitle1" fontWeight={600} sx={{ mb: 1.5 }}>
          {isEdit ? "Edit remote schedule" : "Create a remote schedule"}
        </Typography>
        <Stack spacing={1.5}>
          <TextField
            fullWidth
            disabled={draft.unsupportedAutomation}
            label="Title"
            value={draft.title}
            onChange={(event) => onChange({ title: event.target.value })}
            placeholder="Daily operations brief"
          />
          <TextField
            fullWidth
            disabled={draft.unsupportedAutomation}
            required
            multiline
            minRows={3}
            label="Instruction"
            value={draft.prompt}
            onChange={(event) => onChange({ prompt: event.target.value })}
            placeholder="Ask the managed agent to produce the daily brief…"
          />
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
            <TextField
              fullWidth
              disabled={draft.unsupportedAutomation}
              select
              label="Cadence"
              value={draft.cadence}
              onChange={(event) => onChange({ cadence: event.target.value })}
            >
              {SCHEDULE_CADENCE_PRESETS.map(([value, label]) => (
                <MenuItem key={value} value={value}>
                  {label}
                </MenuItem>
              ))}
              <MenuItem value="custom">Custom cron or interval</MenuItem>
            </TextField>
            <TextField
              fullWidth
              disabled={draft.unsupportedAutomation}
              select
              label="Deliverable"
              value={draft.kind}
              onChange={(event) => onChange({ kind: event.target.value })}
            >
              <MenuItem value="">No hint</MenuItem>
              {SCHEDULE_KINDS.map((kind) => (
                <MenuItem key={kind} value={kind}>
                  {kind}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
          {draft.cadence === "custom" ? (
            <TextField
              fullWidth
              disabled={draft.unsupportedAutomation}
              required
              label="Custom cadence"
              value={draft.customCadence}
              onChange={(event) =>
                onChange({ customCadence: event.target.value })
              }
              helperText="Use a five-field cron expression or an interval such as every 6h."
              placeholder="0 9 * * 1-5"
            />
          ) : null}
          {draft.unsupportedAutomation ? (
            <Alert severity="info">
              This schedule uses an external trigger or multiple actions, so it
              is read-only here.
            </Alert>
          ) : (
            <TextField
              fullWidth
              select
              label="Agent"
              value={draft.agentId}
              onChange={(event) => onChange({ agentId: event.target.value })}
              helperText={
                draft.agentId
                  ? "Uses the current active deployment"
                  : "Leave empty to use the workspace default agent."
              }
            >
              <MenuItem
                value=""
                disabled={isEdit && Boolean(draft.originalAgentId)}
              >
                Workspace default agent
              </MenuItem>
              {draft.agentId &&
              !agents.some((agent) => agent.id === draft.agentId) ? (
                <MenuItem value={draft.agentId} disabled>
                  {draft.agentId} (unavailable)
                </MenuItem>
              ) : null}
              {agents.map((agent) => (
                <MenuItem key={agent.id} value={agent.id}>
                  {agent.name || agent.id} · current deployment
                </MenuItem>
              ))}
            </TextField>
          )}
          {draft.agentId &&
          !agents.some((agent) => agent.id === draft.agentId) ? (
            <Alert severity="warning">
              The selected agent is no longer available with an active
              deployment. Refresh workforce before saving.
            </Alert>
          ) : null}
          <FormControlLabel
            control={
              <Switch
                disabled={draft.unsupportedAutomation}
                checked={draft.enabled}
                onChange={(event) =>
                  onChange({ enabled: event.target.checked })
                }
              />
            }
            label="Enabled immediately"
          />
          <Stack direction="row" justifyContent="flex-end" spacing={1}>
            <Button size="small" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              onClick={onSubmit}
              disabled={
                busy ||
                draft.unsupportedAutomation ||
                (Boolean(draft.agentId) &&
                  !agents.some((agent) => agent.id === draft.agentId)) ||
                !draft.prompt.trim() ||
                (draft.cadence === "custom" && !draft.customCadence.trim())
              }
            >
              {busy
                ? "Saving remotely…"
                : isEdit
                  ? "Save schedule"
                  : "Create schedule"}
            </Button>
          </Stack>
        </Stack>
      </CardContent>
    </Card>
  );
}

ScheduleEditor.propTypes = {
  mode: PropTypes.oneOf(["create", "edit"]).isRequired,
  draft: PropTypes.shape({
    title: PropTypes.string.isRequired,
    prompt: PropTypes.string.isRequired,
    cadence: PropTypes.string.isRequired,
    customCadence: PropTypes.string.isRequired,
    kind: PropTypes.string.isRequired,
    enabled: PropTypes.bool.isRequired,
    agentId: PropTypes.string.isRequired,
    unsupportedAutomation: PropTypes.bool.isRequired,
    originalAgentId: PropTypes.string,
  }).isRequired,
  agents: PropTypes.arrayOf(PropTypes.object).isRequired,
  onChange: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  onSubmit: PropTypes.func.isRequired,
  busy: PropTypes.bool.isRequired,
};

function ScheduleHistory({ detail, loading }) {
  if (loading) return <CircularProgress size={20} sx={{ mt: 2 }} />;
  if (!detail)
    return (
      <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
        Select a schedule to inspect its durable run history.
      </Typography>
    );
  if (!detail.available)
    return (
      <Alert severity="info" sx={{ mt: 2 }}>
        {unavailableReason(detail)}
      </Alert>
    );
  const schedule = detail.schedule || detail;
  const runs = Array.isArray(detail.runs) ? detail.runs : [];
  return (
    <Card variant="outlined" sx={{ mt: 2 }}>
      <CardContent>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ sm: "center" }}
          gap={1}
        >
          <Box>
            <Typography variant="subtitle1" fontWeight={600}>
              {remoteLabel(schedule, "Selected schedule")}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {schedule.enabled === false ? "Disabled" : "Enabled"} · next run{" "}
              {schedule.nextRunAt || "not reported"} ·{" "}
              {schedule.runCount ?? runs.length} recorded run
              {(schedule.runCount ?? runs.length) === 1 ? "" : "s"}
            </Typography>
          </Box>
          {schedule.lastRunStatus ? (
            <Chip
              size="small"
              variant="outlined"
              label={`Last run: ${schedule.lastRunStatus}`}
            />
          ) : null}
        </Stack>
        <Divider sx={{ my: 1.5 }} />
        {runs.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No firings have been recorded yet.
          </Typography>
        ) : (
          <List dense disablePadding>
            {runs.map((run) => (
              <ListItem
                key={run.id || `${run.firedAt}-${run.taskId || "run"}`}
                divider
              >
                <ListItemText
                  primary={`${run.status || "unknown"} · ${run.firedAt || "unknown time"}`}
                  secondary={
                    [
                      run.note,
                      run.execution?.agentId
                        ? `agent ${run.execution.agentId}`
                        : null,
                      Number.isInteger(run.execution?.agentVersion)
                        ? `agent v${run.execution.agentVersion}`
                        : null,
                      Number.isInteger(run.execution?.deploymentVersion)
                        ? `deployment v${run.execution.deploymentVersion}`
                        : null,
                      run.execution?.deploymentProcessId
                        ? `process ${run.execution.deploymentProcessId}`
                        : null,
                      run.conversationId
                        ? `conversation ${run.conversationId}`
                        : null,
                      run.receipt?.seq ? `receipt #${run.receipt.seq}` : null,
                      run.receipt?.hash
                        ? `hash ${String(run.receipt.hash).slice(0, 12)}…`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(" · ") || "Durable schedule firing"
                  }
                />
              </ListItem>
            ))}
          </List>
        )}
      </CardContent>
    </Card>
  );
}

ScheduleHistory.propTypes = {
  detail: PropTypes.shape({
    available: PropTypes.bool,
    schedule: PropTypes.object,
    runs: PropTypes.arrayOf(PropTypes.object),
  }),
  loading: PropTypes.bool.isRequired,
};

export function SchedulePanel({
  result,
  agents,
  client,
  busyId,
  selectedId,
  detail,
  detailLoading,
  onSelect,
  onRun,
  onCreate,
  onUpdate,
  onDelete,
}) {
  const schedules = collection(result, "schedules");
  const [editorMode, setEditorMode] = useState("");
  const [editingId, setEditingId] = useState("");
  const [draft, setDraft] = useState(emptyScheduleDraft);
  const [editorError, setEditorError] = useState("");
  const [checking, setChecking] = useState(false);
  const requestGeneration = useRef(0);
  const editorScope = useRef({ client, draft, editorMode, editingId, agents });
  editorScope.current = { client, draft, editorMode, editingId, agents };
  useEffect(
    () => () => {
      requestGeneration.current += 1;
    },
    [],
  );
  const changeDraft = (changes) => {
    requestGeneration.current += 1;
    setChecking(false);
    setEditorError("");
    setDraft((current) => ({
      ...current,
      ...changes,
      ...(Object.hasOwn(changes, "cadence") ||
      Object.hasOwn(changes, "customCadence")
        ? { originalCadence: null }
        : {}),
    }));
  };
  const closeEditor = () => {
    requestGeneration.current += 1;
    setChecking(false);
    setEditorMode("");
    setEditingId("");
    setEditorError("");
  };

  useEffect(() => {
    if (editorMode || !selectedId) return;
    const source = schedules.find(
      (schedule) => (schedule.id || schedule.scheduleId) === selectedId,
    );
    if (source) setDraft(scheduleToDraft(source));
  }, [editorMode, schedules, selectedId]);

  const beginCreate = () => {
    requestGeneration.current += 1;
    setChecking(false);
    setEditorError("");
    setEditingId("");
    setDraft(emptyScheduleDraft());
    setEditorMode("create");
  };
  const beginEdit = (schedule) => {
    requestGeneration.current += 1;
    setChecking(false);
    setEditorError("");
    const id = schedule.id || schedule.scheduleId;
    setEditingId(id || "");
    setDraft(
      scheduleToDraft(
        detail?.available && detail.schedule?.id === id
          ? detail.schedule
          : schedule,
      ),
    );
    setEditorMode("edit");
  };
  const submit = async () => {
    if (
      draft.unsupportedAutomation ||
      checking ||
      (draft.originalAgentId && !draft.agentId)
    )
      return;
    const generation = ++requestGeneration.current;
    const current = () =>
      generation === requestGeneration.current &&
      editorScope.current.client === client &&
      editorScope.current.draft === draft &&
      editorScope.current.editorMode === editorMode &&
      editorScope.current.editingId === editingId;
    setChecking(true);
    setEditorError("");
    try {
      let selectedAgent;
      if (draft.agentId) {
        const fresh = await client.listWorkforce();
        if (!current()) return;
        selectedAgent =
          fresh.available &&
          fresh.agents?.find(
            (agent) =>
              agent.id === draft.agentId &&
              ["idle", "working"].includes(agent.status),
          );
        if (
          !selectedAgent ||
          !fresh.capabilities?.permittedActions?.includes("run")
        ) {
          setEditorError(
            "The agent or your run permissions changed. Refresh the workforce and choose an available deployed agent.",
          );
          return;
        }
        const deployments = await client.listWorkforceAgentDeployments(
          draft.agentId,
        );
        if (!current()) return;
        if (
          !deployments.available ||
          deployments.agent?.id !== draft.agentId ||
          deployments.deployment?.active !== true ||
          !Number.isInteger(deployments.deployment.version)
        ) {
          setEditorError(
            "The selected agent has no verified active deployment. Deploy it, then refresh and try again.",
          );
          return;
        }
      }
      const original = draft.originalAutomation;
      const agentChanged = draft.agentId !== draft.originalAgentId;
      const automation = draft.agentId
        ? {
            ...(original || {}),
            schemaVersion: "omega-automation/v1",
            brief:
              original?.brief ||
              draft.title.trim() ||
              draft.prompt.trim().slice(0, 120),
            trigger: original?.trigger || {
              kind: "time",
              label: "Scheduled time",
            },
            actions: [
              {
                ...(original?.actions?.[0] || {
                  kind: "run_agent",
                  label: "Run selected agent",
                }),
                agentId: draft.agentId,
                ...(agentChanged
                  ? { agentName: selectedAgent.name || selectedAgent.id }
                  : {}),
              },
            ],
            attachments: (original?.attachments || []).map((attachment) =>
              agentChanged &&
              attachment.kind === "agent" &&
              attachment.id === draft.originalAgentId
                ? {
                    ...attachment,
                    id: draft.agentId,
                    name: selectedAgent.name || selectedAgent.id,
                  }
                : attachment,
            ),
          }
        : undefined;
      const payload = {
        prompt: draft.prompt.trim(),
        cadence:
          draft.originalCadence ||
          (draft.cadence === "custom"
            ? draft.customCadence.trim()
            : draft.cadence),
        enabled: draft.enabled,
        ...(draft.title.trim() ? { title: draft.title.trim() } : {}),
        ...(draft.kind
          ? { kind: draft.kind }
          : editorMode === "edit"
            ? { kind: null }
            : {}),
        ...(automation ? { automation } : {}),
      };
      if (!current()) return;
      if (
        draft.agentId &&
        !editorScope.current.agents.some(
          (agent) =>
            agent.id === draft.agentId &&
            ["idle", "working"].includes(agent.status),
        )
      ) {
        setEditorError(
          "The selected agent changed while checking its deployment. Refresh the workforce and choose it again.",
        );
        return;
      }
      const response =
        editorMode === "edit" && editingId
          ? await onUpdate(editingId, payload)
          : await onCreate(payload);
      if (!current()) return;
      if (response?.available) closeEditor();
      else setEditorError(unavailableReason(response));
    } catch {
      if (current())
        setEditorError(
          "The schedule could not be saved. Refresh the workforce and try again.",
        );
    } finally {
      if (current()) setChecking(false);
    }
  };
  return (
    <RemoteUnavailable result={result}>
      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 1.5 }}>
        <Button size="small" variant="contained" onClick={beginCreate}>
          Create schedule
        </Button>
      </Stack>
      {editorError ? (
        <Alert severity="error" sx={{ mb: 1.5 }}>
          {editorError}
        </Alert>
      ) : null}
      {editorMode ? (
        <ScheduleEditor
          mode={editorMode}
          draft={draft}
          agents={agents.filter((agent) =>
            ["idle", "working"].includes(agent.status),
          )}
          onChange={changeDraft}
          onCancel={closeEditor}
          onSubmit={() => void submit()}
          busy={busyId === "schedule-editor" || checking}
        />
      ) : null}
      {schedules.length === 0 ? (
        <Typography color="text.secondary">
          No schedules are configured for this workspace.
        </Typography>
      ) : (
        <List disablePadding>
          {schedules.map((schedule) => {
            const id = schedule.id || schedule.scheduleId;
            const state =
              schedule.status ||
              schedule.state ||
              (schedule.enabled === false ? "disabled" : "enabled");
            const cadence =
              typeof schedule.cadence === "string"
                ? schedule.cadence
                : schedule.cadence?.expr ||
                  schedule.cadence?.kind ||
                  schedule.cron ||
                  schedule.expression ||
                  "scheduled workflow";
            return (
              <ListItem
                key={id || remoteLabel(schedule)}
                divider
                selected={id === selectedId}
                onClick={() => onSelect(id)}
                sx={{ cursor: id ? "pointer" : "default" }}
                secondaryAction={
                  <Stack direction="row" spacing={0.5}>
                    <Button
                      size="small"
                      onClick={(event) => {
                        event.stopPropagation();
                        beginEdit(schedule);
                      }}
                      disabled={!id}
                    >
                      Edit
                    </Button>
                    <Button
                      size="small"
                      color="error"
                      onClick={(event) => {
                        event.stopPropagation();
                        onDelete(id);
                      }}
                      disabled={!id || busyId === id}
                    >
                      Delete
                    </Button>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={(event) => {
                        event.stopPropagation();
                        onRun(id);
                      }}
                      disabled={!id || busyId === id}
                    >
                      Run now
                    </Button>
                  </Stack>
                }
              >
                <ListItemText
                  primary={remoteLabel(schedule, "Remote schedule")}
                  secondary={`${cadence} · ${state}${schedule.lastRunStatus ? ` · last run ${schedule.lastRunStatus}` : ""}`}
                />
              </ListItem>
            );
          })}
        </List>
      )}
      <ScheduleHistory detail={detail} loading={detailLoading} />
    </RemoteUnavailable>
  );
}

export { ConnectorPanel };

function memoryProjection(result, key) {
  if (!result?.available) return null;
  const value = result[key];
  return value && typeof value === "object" ? value : result;
}

function MemoryPanel({
  client,
  scopesResult,
  graphResult,
  timelineResult,
  eventsResult,
  consolidationResult,
  eventDetail,
  lineageResult,
  selectedEventId,
  eventLoading,
  exportValue,
  onInspectEvent,
  onExport,
}) {
  const scopes = collection(scopesResult, "scopes");
  const graph = memoryProjection(graphResult, "graph");
  const timeline = memoryProjection(timelineResult, "timeline");
  const timelineEntries = Array.isArray(timeline?.entries)
    ? timeline.entries
    : [];
  const events = collection(eventsResult, "events");
  const consolidation = memoryProjection(consolidationResult, "consolidation");
  const detail = memoryProjection(eventDetail, "event");
  const lineage = memoryProjection(lineageResult, "lineage");
  return (
    <Stack spacing={2}>
      <MemoryRecallPanel client={client} scopesResult={scopesResult} />
      <RemoteUnavailable result={scopesResult}>
        <Typography variant="body2" color="text.secondary">
          {scopes.length > 0
            ? `${scopes.length} tenant memory scope${scopes.length === 1 ? "" : "s"} available.`
            : "No memory scopes are visible in this workspace."}
        </Typography>
        {scopes.length > 0 ? (
          <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mt: 1 }}>
            {scopes.map((scope) => (
              <Chip
                size="small"
                variant="outlined"
                key={scope.path || scope.id || scope.key || remoteLabel(scope)}
                label={scope.path || remoteLabel(scope, "Memory scope")}
              />
            ))}
          </Stack>
        ) : null}
      </RemoteUnavailable>
      <RemoteUnavailable result={graphResult}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          gap={1}
        >
          <Typography variant="body2" color="text.secondary">
            {graph
              ? `${graph.nodes?.length ?? graph.nodeCount ?? 0} nodes · ${graph.edges?.length ?? graph.edgeCount ?? 0} links in the remote graph.`
              : "The remote memory graph is empty."}
          </Typography>
          <Button size="small" variant="outlined" onClick={onExport}>
            Export memory
          </Button>
        </Stack>
        {exportValue ? (
          <Typography
            component="pre"
            variant="caption"
            sx={{
              mt: 1,
              maxHeight: 160,
              overflow: "auto",
              p: 1.5,
              bgcolor: "action.hover",
              borderRadius: 1,
            }}
          >
            {exportValue}
          </Typography>
        ) : null}
      </RemoteUnavailable>
      <RemoteUnavailable result={timelineResult}>
        <Box>
          <Typography variant="subtitle2">Memory timeline</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Tenant-scoped writes and promotions, with sequence and provenance
            preserved by the remote log.
          </Typography>
          {timelineEntries.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No timeline entries are visible in this workspace.
            </Typography>
          ) : (
            <List dense disablePadding>
              {timelineEntries
                .slice(-20)
                .reverse()
                .map((entry) => (
                  <ListItem
                    key={`${entry.eventId || entry.id || "entry"}-${entry.seq || entry.at || "unknown"}`}
                    divider
                  >
                    <ListItemText
                      primary={`${entry.type || entry.kind || "memory"} · ${entry.eventId || entry.id || "unknown event"}`}
                      secondary={
                        [
                          entry.at || entry.recordedAt,
                          entry.scope,
                          entry.targetEventId
                            ? `target ${entry.targetEventId}`
                            : null,
                          entry.seq !== undefined ? `seq ${entry.seq}` : null,
                        ]
                          .filter(Boolean)
                          .join(" · ") || "Remote timeline entry"
                      }
                    />
                  </ListItem>
                ))}
            </List>
          )}
        </Box>
      </RemoteUnavailable>
      <RemoteUnavailable result={eventsResult}>
        <Box>
          <Typography variant="subtitle2">Recent memory events</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Select an event to inspect its remote record and lineage.
          </Typography>
          {events.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No memory events are visible in this workspace.
            </Typography>
          ) : (
            <List dense disablePadding>
              {events.slice(0, 20).map((event) => {
                const id = event.id || event.eventId;
                return (
                  <ListItem
                    key={id || remoteLabel(event)}
                    selected={id === selectedEventId}
                    divider
                    button
                    onClick={() => onInspectEvent(id)}
                  >
                    <ListItemText
                      primary={`${remoteLabel(event, "Memory event")} · ${event.kind || event.type || "record"}`}
                      secondary={
                        [event.at || event.recordedAt, event.scope, event.tier]
                          .filter(Boolean)
                          .join(" · ") ||
                        event.contentPreview ||
                        "Remote memory event"
                      }
                    />
                  </ListItem>
                );
              })}
            </List>
          )}
        </Box>
      </RemoteUnavailable>
      {eventLoading ? <CircularProgress size={20} /> : null}
      {eventDetail && !eventDetail.available ? (
        <Alert severity="info">{unavailableReason(eventDetail)}</Alert>
      ) : null}
      {detail ? (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2">Event provenance</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {[
                detail.id || selectedEventId,
                detail.kind || detail.type,
                detail.scope,
                detail.tier,
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
            {detail.contentPreview || detail.content?.text ? (
              <Typography variant="body2" sx={{ mt: 1 }}>
                {detail.contentPreview || detail.content.text}
              </Typography>
            ) : null}
            {lineage ? (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 1 }}
              >
                Lineage: {lineage.nodes?.length ?? 0} nodes ·{" "}
                {lineage.edges?.length ?? 0} links
                {lineage.chain?.eventHash
                  ? ` · hash ${lineage.chain.eventHash}`
                  : ""}
              </Typography>
            ) : lineageResult?.available === false ? (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 1 }}
              >
                {unavailableReason(lineageResult)}
              </Typography>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
      <RemoteUnavailable result={consolidationResult}>
        <Typography variant="caption" color="text.secondary">
          Consolidation:{" "}
          {consolidation
            ? `${consolidation.pendingTotal ?? consolidation.pending?.length ?? 0} pending candidate${(consolidation.pendingTotal ?? consolidation.pending?.length ?? 0) === 1 ? "" : "s"}`
            : "status unavailable"}
          .
        </Typography>
      </RemoteUnavailable>
    </Stack>
  );
}

/**
 * Customer audit boundary.
 *
 * Omega EE currently exposes `/os/audit` and `/os/audit/integrity` as one
 * deployment-global, operator-only chain. Showing that feed to a tenant, or
 * filtering it by principal in the browser, would be an authorization guess:
 * entries do not carry a server-derived owner. Keep the panel visible so the
 * launch boundary is legible, but render the typed unavailable result until EE
 * provides an owner-bound `/workspace/audit` projection.
 */
function AuditPanel({ auditResult, integrityResult }) {
  const reason = unavailableReason(auditResult || integrityResult);
  return (
    <Stack spacing={2}>
      <Alert severity="info">
        Tenant audit and chain verification are not available in this
        deployment. The remote EE ledger is operator-global; Squad will not
        guess tenant ownership by filtering it.
      </Alert>
      <Card variant="outlined">
        <CardContent>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            justifyContent="space-between"
            gap={1}
          >
            <Box>
              <Typography variant="subtitle1" fontWeight={600}>
                Audit ledger
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 0.5 }}
              >
                A customer-safe, hash-chain-backed audit projection is required
                before this surface can show entries.
              </Typography>
            </Box>
            <Chip
              size="small"
              color="warning"
              variant="outlined"
              label="Unavailable"
            />
          </Stack>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mt: 1.5 }}
          >
            {reason}
          </Typography>
        </CardContent>
      </Card>
      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle2">Integrity verification</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            No tenant integrity verdict is asserted. An operator-only chain
            result cannot prove this workspace&apos;s records.
          </Typography>
          <Chip
            size="small"
            color="warning"
            variant="outlined"
            label="Unknown"
            sx={{ mt: 1 }}
          />
          {integrityResult?.available === false ? (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 1 }}
            >
              {unavailableReason(integrityResult)}
            </Typography>
          ) : null}
        </CardContent>
      </Card>
    </Stack>
  );
}

function formatBillingMoney(cents) {
  if (!Number.isFinite(cents)) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

function currentBillingPeriod() {
  const now = new Date();
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const end = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1),
  );
  return { start: start.toISOString(), end: end.toISOString() };
}

export function BillingPanel({
  accountsResult,
  plansResult,
  accountId,
  onSelectAccount,
  data,
  period,
  loading,
  mutation,
  seatQuote,
  onSeatQuote,
  onCheckout,
  onCancelSubscription,
  onRefresh,
}) {
  // The EE route returns `items`; the typed EE console client projects the same
  // payload as `accounts`. Accept both envelopes so a gateway adapter can keep
  // the server response honest without making the selector look empty.
  const accounts = collection(accountsResult, "accounts");
  const usage = data?.usage?.available ? data.usage.usage || data.usage : null;
  const totals =
    usage?.totals && typeof usage.totals === "object" ? usage.totals : {};
  const attribution = data?.attribution?.available
    ? data.attribution.attribution || data.attribution
    : null;
  const rows = Array.isArray(attribution?.rows) ? attribution.rows : [];
  const subscription = data?.subscription?.available
    ? data.subscription.subscription || data.subscription
    : null;
  const invoice = data?.invoice?.available
    ? data.invoice.invoice || data.invoice
    : null;
  const integrity = data?.integrity?.available
    ? data.integrity.integrity || data.integrity
    : null;
  const issuedInvoices = data?.invoices?.available
    ? data.invoices.issued || []
    : [];
  const seats = data?.seats?.available ? data.seats.seats || data.seats : null;
  const selected = accounts.find(
    (account) => (account.id || account.accountId) === accountId,
  );
  const plans = collection(plansResult, "plans").filter(
    (plan) => plan.purchasable === true,
  );
  const [selectedPlanId, setSelectedPlanId] = useState("");
  const [seatTarget, setSeatTarget] = useState("");
  const [seatQuoteLoading, setSeatQuoteLoading] = useState(false);
  useEffect(() => {
    setSelectedPlanId((current) =>
      plans.some((plan) => plan.id === current) ? current : plans[0]?.id || "",
    );
  }, [plans]);
  useEffect(() => {
    const currentSeats =
      seats?.billableSeats ?? seats?.activeSeats ?? seats?.seats;
    setSeatTarget(
      (current) =>
        current || (Number.isFinite(currentSeats) ? String(currentSeats) : ""),
    );
  }, [seats]);
  const subscriptionStatus = subscription?.status || "none";

  return (
    <Stack spacing={2}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        gap={1}
      >
        <Box>
          <Typography variant="subtitle1" fontWeight={600}>
            Billing and usage
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Read-only account usage from the remote Omega billing ledger (
            {period.start.slice(0, 10)} to {period.end.slice(0, 10)}).
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          onClick={onRefresh}
          disabled={loading}
        >
          Refresh usage
        </Button>
      </Stack>
      <RemoteUnavailable result={accountsResult}>
        {accounts.length === 0 ? (
          <Typography color="text.secondary">
            No billing account is visible to this workspace.
          </Typography>
        ) : (
          <TextField
            select
            fullWidth
            label="Billing account"
            value={accountId}
            onChange={(event) => onSelectAccount(event.target.value)}
            helperText="Account visibility follows the remote tenant session; Squad cannot choose another organization."
          >
            {accounts.map((account) => {
              const id = account.id || account.accountId;
              return (
                <MenuItem key={id} value={id}>
                  {account.name || id}
                </MenuItem>
              );
            })}
          </TextField>
        )}
      </RemoteUnavailable>
      {loading ? <CircularProgress size={22} /> : null}
      {accountId && data?.subscription?.available === false ? (
        <Alert severity="info">{unavailableReason(data.subscription)}</Alert>
      ) : null}
      {accountId && data?.usage?.available === false ? (
        <Alert severity="info">{unavailableReason(data.usage)}</Alert>
      ) : null}
      {accountId ? (
        <Grid container spacing={1.5}>
          <Grid item xs={12} sm={4}>
            <Metric
              label="Plan"
              value={subscription?.planId || "No active plan"}
              detail={
                subscription?.status || "Remote subscription state unavailable"
              }
            />
          </Grid>
          <Grid item xs={12} sm={4}>
            <Metric
              label="Current invoice"
              value={formatBillingMoney(invoice?.totalCents)}
              detail={
                invoice?.basis === "metered"
                  ? "Metered usage"
                  : "Subscription period"
              }
            />
          </Grid>
          <Grid item xs={12} sm={4}>
            <Metric
              label="Issued invoices"
              value={issuedInvoices.length}
              detail={
                data?.invoices?.available === false
                  ? unavailableReason(data.invoices)
                  : selected?.name || accountId
              }
            />
          </Grid>
        </Grid>
      ) : null}
      {accountId && Object.keys(totals).length > 0 ? (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2">Metered usage</Typography>
            <List dense disablePadding sx={{ mt: 0.5 }}>
              {Object.entries(totals).map(([kind, total]) => (
                <ListItem key={kind} divider>
                  <ListItemText
                    primary={kind.replaceAll("_", " ")}
                    secondary={`${total?.quantity ?? 0} ${total?.unit || "units"} · ${total?.events ?? 0} events · ${total?.distinctSources ?? 0} sources`}
                  />
                </ListItem>
              ))}
            </List>
          </CardContent>
        </Card>
      ) : accountId && data?.usage?.available ? (
        <Typography color="text.secondary">
          No metered usage was recorded for this period.
        </Typography>
      ) : null}
      {accountId && attribution ? (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2">Usage by model</Typography>
            {rows.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                No model attribution rows were recorded.
              </Typography>
            ) : (
              <List dense disablePadding sx={{ mt: 0.5 }}>
                {rows.map((row) => (
                  <ListItem key={`${row.key}-${row.attributed}`} divider>
                    <ListItemText
                      primary={row.attributed ? row.key : "Unattributed"}
                      secondary={`${row.tokens ?? 0} tokens · ${row.runs ?? 0} runs · ${formatBillingMoney((row.costMicroUsd || 0) / 10000)}`}
                    />
                  </ListItem>
                ))}
              </List>
            )}
          </CardContent>
        </Card>
      ) : null}
      {accountId && integrity ? (
        <Alert
          severity={
            integrity.usage && integrity.subscription ? "success" : "warning"
          }
        >
          Ledger integrity: usage {integrity.usage ? "verified" : "failed"},
          subscription {integrity.subscription ? "verified" : "failed"}
          {integrity.credits === undefined
            ? ""
            : `, credits ${integrity.credits ? "verified" : "failed"}`}
          .
        </Alert>
      ) : null}
      {accountId && data?.meter?.available === false ? (
        <Alert severity="info">
          Processor reconciliation unavailable: {unavailableReason(data.meter)}
        </Alert>
      ) : null}
      {accountId && seats ? (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2">Seats and entitlement</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {seats.billableSeats ?? seats.activeSeats ?? seats.seats ?? 0}{" "}
              billable seats · cap {seats.seatCap ?? seats.limit ?? "unknown"} ·{" "}
              {seats.overLimit ? "over plan limit" : "within plan limit"}
            </Typography>
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1}
              alignItems={{ sm: "center" }}
              sx={{ mt: 1.5 }}
            >
              <TextField
                size="small"
                type="number"
                label="Target seats"
                value={seatTarget}
                onChange={(event) => setSeatTarget(event.target.value)}
                inputProps={{ min: 1, max: 10000 }}
              />
              <Button
                size="small"
                variant="outlined"
                onClick={async () => {
                  setSeatQuoteLoading(true);
                  await onSeatQuote(accountId, Number(seatTarget));
                  setSeatQuoteLoading(false);
                }}
                disabled={
                  seatQuoteLoading ||
                  !Number.isInteger(Number(seatTarget)) ||
                  Number(seatTarget) < 1
                }
              >
                Preview seat quote
              </Button>
            </Stack>
            {seatQuote?.available === false ? (
              <Alert severity="info" sx={{ mt: 1.5 }}>
                {unavailableReason(seatQuote)}
              </Alert>
            ) : null}
            {seatQuote?.available ? (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 1.5 }}
              >
                Remote quote:{" "}
                {formatBillingMoney(
                  seatQuote.quote?.totalCents ?? seatQuote.totalCents,
                )}
                {seatQuote.quote?.effective
                  ? ` · ${seatQuote.quote.effective}`
                  : ""}
                . No seat mutation was performed.
              </Typography>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
      {accountId ? (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2">Remote plan management</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              Checkout and cancellation are executed by the remote billing
              provider. Squad never handles payment credentials.
            </Typography>
            {subscriptionStatus === "active" ? (
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1}
                alignItems={{ sm: "center" }}
                sx={{ mt: 1.5 }}
              >
                <Chip
                  size="small"
                  color="success"
                  variant="outlined"
                  label={`Active: ${subscription?.planId || "remote plan"}`}
                />
                <Button
                  size="small"
                  color="error"
                  variant="outlined"
                  onClick={() => onCancelSubscription(accountId)}
                  disabled={mutation?.busy}
                >
                  Cancel subscription
                </Button>
              </Stack>
            ) : (
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1}
                alignItems={{ sm: "center" }}
                sx={{ mt: 1.5 }}
              >
                <TextField
                  select
                  size="small"
                  label="Purchasable plan"
                  value={selectedPlanId}
                  onChange={(event) => setSelectedPlanId(event.target.value)}
                  disabled={plans.length === 0 || mutation?.busy}
                  sx={{ minWidth: 220 }}
                >
                  {plans.map((plan) => (
                    <MenuItem key={plan.id} value={plan.id}>
                      {plan.name || plan.id}
                    </MenuItem>
                  ))}
                </TextField>
                <Button
                  size="small"
                  variant="contained"
                  onClick={() => onCheckout(accountId, selectedPlanId)}
                  disabled={
                    !selectedPlanId ||
                    mutation?.busy ||
                    subscriptionStatus === "checkout_pending"
                  }
                >
                  {mutation?.busy
                    ? "Opening checkout…"
                    : "Open hosted checkout"}
                </Button>
              </Stack>
            )}
            {plansResult?.available === false ? (
              <Alert severity="info" sx={{ mt: 1.5 }}>
                {unavailableReason(plansResult)}
              </Alert>
            ) : null}
            {mutation?.error ? (
              <Alert severity="error" sx={{ mt: 1.5 }}>
                {mutation.error}
              </Alert>
            ) : null}
            {mutation?.checkoutUrl ? (
              <Alert severity="success" sx={{ mt: 1.5 }}>
                Checkout opened by remote billing provider.{" "}
                <Button
                  component="a"
                  href={mutation.checkoutUrl}
                  target="_blank"
                  rel="noreferrer"
                  size="small"
                >
                  Continue payment
                </Button>
              </Alert>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </Stack>
  );
}

function SandboxPanel({
  sandboxes,
  result,
  selectedId,
  detail,
  services,
  actionId,
  onSelect,
  onControl,
}) {
  return (
    <Grid container spacing={2}>
      <Grid item xs={12} md={5}>
        <RemoteUnavailable result={result}>
          {sandboxes.length === 0 ? (
            <Typography color="text.secondary">
              No cloud sandboxes are provisioned for this workspace.
            </Typography>
          ) : (
            <List disablePadding>
              {sandboxes.map((sandbox) => {
                const id = sandbox.id || sandbox.sandboxId;
                const state = sandbox.state || sandbox.status || "unknown";
                return (
                  <ListItem
                    key={id || remoteLabel(sandbox)}
                    divider
                    selected={id === selectedId}
                    onClick={() => onSelect(id)}
                    sx={{ cursor: "pointer" }}
                  >
                    <ListItemText
                      primary={remoteLabel(sandbox, "Remote sandbox")}
                      secondary={`${sandbox.providerKind || "cloud provider"} · ${state}`}
                    />
                  </ListItem>
                );
              })}
            </List>
          )}
        </RemoteUnavailable>
      </Grid>
      <Grid item xs={12} md={7}>
        {selectedId ? (
          <Card variant="outlined">
            <CardContent>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                justifyContent="space-between"
                alignItems={{ sm: "center" }}
                gap={1}
              >
                <Box>
                  <Typography variant="h6">
                    {remoteLabel(detail, selectedId)}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {detail?.providerKind || "Remote sandbox"} ·{" "}
                    {detail?.state || detail?.status || "unknown state"}
                  </Typography>
                </Box>
                <Stack direction="row" spacing={1}>
                  <Button
                    size="small"
                    onClick={() => onControl(selectedId, "resume")}
                    disabled={actionId === selectedId}
                  >
                    Resume
                  </Button>
                  <Button
                    size="small"
                    onClick={() => onControl(selectedId, "hibernate")}
                    disabled={actionId === selectedId}
                  >
                    Hibernate
                  </Button>
                  <Button
                    size="small"
                    color="error"
                    variant="outlined"
                    onClick={() => onControl(selectedId, "destroy")}
                    disabled={actionId === selectedId}
                  >
                    Destroy
                  </Button>
                </Stack>
              </Stack>
              <Divider sx={{ my: 2 }} />
              <Typography variant="subtitle2">Services</Typography>
              {services.length === 0 ? (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.5 }}
                >
                  No services reported by this sandbox.
                </Typography>
              ) : (
                <List dense disablePadding>
                  {services.map((service) => (
                    <ListItem
                      key={
                        service.id || service.serviceId || remoteLabel(service)
                      }
                    >
                      <ListItemText
                        primary={remoteLabel(service, "Sandbox service")}
                        secondary={service.status || service.state || "running"}
                      />
                    </ListItem>
                  ))}
                </List>
              )}
            </CardContent>
          </Card>
        ) : (
          <Typography color="text.secondary">
            Select a cloud sandbox to inspect its lifecycle and services.
          </Typography>
        )}
      </Grid>
    </Grid>
  );
}

Metric.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]).isRequired,
  detail: PropTypes.string,
};

AgentCard.propTypes = {
  agent: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    role: PropTypes.string,
    purpose: PropTypes.string,
    status: PropTypes.string,
    currentWork: PropTypes.string,
  }).isRequired,
  selected: PropTypes.bool,
  onSelect: PropTypes.func.isRequired,
};

RunRow.propTypes = {
  run: PropTypes.shape({
    id: PropTypes.string,
    conversationId: PropTypes.string,
    title: PropTypes.string,
    status: PropTypes.string,
    phase: PropTypes.string,
    updatedAt: PropTypes.string,
    createdAt: PropTypes.string,
  }).isRequired,
  busyId: PropTypes.string,
  onControl: PropTypes.func.isRequired,
  onSelect: PropTypes.func.isRequired,
};

export function RunDetailPanel({
  id,
  detail,
  loading,
  artifacts,
  events,
  streamStatus,
  busyId,
  controlReceipt,
  followUp,
  followUpBusy,
  onFollowUpChange,
  onSendFollowUp,
  onArtifact,
  onControl,
  onResolveApproval,
}) {
  if (!id) return null;
  if (loading) return <CircularProgress size={20} sx={{ mt: 2 }} />;
  if (!detail) return null;
  const conversation = detail.conversation?.available
    ? detail.conversation.conversation || detail.conversation
    : null;
  const control = detail.control?.available
    ? detail.control.control || detail.control
    : null;
  const approvals = detail.approvals?.available
    ? collection(detail.approvals, "approvals")
    : [];
  const observations = detail.observations?.available
    ? collection(detail.observations, "observations")
    : [];
  const status = String(
    conversation?.status || detail.control?.status || "unknown",
  ).toLowerCase();
  const terminal = [
    "done",
    "completed",
    "complete",
    "succeeded",
    "success",
    "failed",
    "error",
    "stopped",
    "cancelled",
    "canceled",
  ].includes(status);
  const paused = ["paused", "parked", "interrupted"].includes(status);
  const process = control?.process || {};
  const runSpend = control?.runSpend || control?.spend;
  const budget = process.budget || control?.budget;
  const parkedReason =
    control?.parkedReason || process.parkedReason || conversation?.parkedReason;
  const identity = detail.identity?.available
    ? detail.identity.identity || detail.identity
    : null;
  const verified = artifacts.proofPack?.verification?.ok === true;
  const isolation =
    artifacts.proofPack?.verification?.isolation ||
    artifacts.proofPack?.isolation;
  const isolationLabel =
    isolation?.status === "verified"
      ? "Task isolation verified"
      : isolation?.status === "invalid"
        ? "Task isolation unverified"
        : "Task isolation unknown";
  return (
    <Card variant="outlined" sx={{ mt: 2 }}>
      <CardContent>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ sm: "center" }}
          gap={1}
        >
          <Box>
            <Typography variant="subtitle1" fontWeight={600}>
              Run detail
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {id} ·{" "}
              {conversation?.status ||
                detail.control?.status ||
                "remote state unavailable"}
            </Typography>
          </Box>
          <Stack
            direction="row"
            spacing={1}
            flexWrap="wrap"
            justifyContent="flex-end"
          >
            {!terminal && !paused ? (
              <Button
                size="small"
                onClick={() => onControl(id, "interrupt")}
                disabled={busyId === id}
              >
                Pause
              </Button>
            ) : null}
            {paused ? (
              <Button
                size="small"
                onClick={() => onControl(id, "continue")}
                disabled={busyId === id}
              >
                Resume
              </Button>
            ) : null}
            {!terminal ? (
              <Button
                size="small"
                color="error"
                onClick={() => onControl(id, "cancel")}
                disabled={busyId === id}
              >
                Cancel
              </Button>
            ) : null}
            {!terminal ? (
              <Button
                size="small"
                color="error"
                onClick={() => onControl(id, "stop")}
                disabled={busyId === id}
              >
                Stop
              </Button>
            ) : null}
            {!terminal ? (
              <Button
                size="small"
                color="error"
                onClick={() => onControl(id, "kill")}
                disabled={busyId === id}
              >
                Kill
              </Button>
            ) : null}
            {["failed", "error", "stopped", "cancelled", "canceled"].includes(
              status,
            ) ? (
              <Button
                size="small"
                onClick={() => onControl(id, "retry")}
                disabled={busyId === id}
              >
                Retry
              </Button>
            ) : null}
            <Button size="small" onClick={() => onArtifact("evidence")}>
              Evidence
            </Button>
            <Button
              size="small"
              variant="outlined"
              onClick={() => onArtifact("proofPack")}
            >
              Proof pack
            </Button>
          </Stack>
        </Stack>
        {conversation?.title || conversation?.input ? (
          <Typography variant="body2" sx={{ mt: 1.5 }}>
            {conversation.title || conversation.input}
          </Typography>
        ) : null}
        {detail.conversation?.available ? (
          <RunConversationMessages
            runId={id}
            messages={detail.conversation.messages || conversation?.messages}
          />
        ) : null}
        {typeof conversation?.currentStep === "string" &&
        conversation.currentStep ? (
          <Alert
            severity={["failed", "error"].includes(status) ? "error" : "info"}
            sx={{ mt: 1.5 }}
          >
            {conversation.currentStep}
          </Alert>
        ) : null}
        {control &&
        (runSpend ||
          budget ||
          control.runStatus ||
          control.processStatus ||
          process.processStatus) ? (
          <Card variant="outlined" sx={{ mt: 1.5 }}>
            <CardContent sx={{ "&:last-child": { pb: 1.5 } }}>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                justifyContent="space-between"
                gap={1}
              >
                <Box>
                  <Typography variant="subtitle2">Remote run budget</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Values are reported by Omega; Squad does not recompute spend
                    or entitlement.
                  </Typography>
                </Box>
                <Stack direction="row" spacing={0.75}>
                  <Chip
                    size="small"
                    variant="outlined"
                    label={
                      control.runStatus || conversation?.status || "remote run"
                    }
                  />
                  {process.processStatus || control.processStatus ? (
                    <Chip
                      size="small"
                      variant="outlined"
                      label={`process ${process.processStatus || control.processStatus}`}
                    />
                  ) : null}
                </Stack>
              </Stack>
              <Grid container spacing={1} sx={{ mt: 0.5 }}>
                {Object.entries(budget || {}).map(([key, value]) => (
                  <Grid item xs={6} sm={3} key={`budget-${key}`}>
                    <Typography variant="caption" color="text.secondary">
                      {key.replaceAll("_", " ")}
                    </Typography>
                    <Typography variant="body2">
                      {typeof value === "object"
                        ? JSON.stringify(value)
                        : String(value)}
                    </Typography>
                  </Grid>
                ))}
                {Object.entries(runSpend || {}).map(([key, value]) => (
                  <Grid item xs={6} sm={3} key={`spend-${key}`}>
                    <Typography variant="caption" color="text.secondary">
                      spend {key.replaceAll("_", " ")}
                    </Typography>
                    <Typography variant="body2">
                      {typeof value === "object"
                        ? JSON.stringify(value)
                        : String(value)}
                    </Typography>
                  </Grid>
                ))}
              </Grid>
              {Object.keys(budget || {}).length === 0 &&
              Object.keys(runSpend || {}).length === 0 ? (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 1 }}
                >
                  The remote control plane did not publish budget or spend
                  fields.
                </Typography>
              ) : null}
            </CardContent>
          </Card>
        ) : null}
        {paused ? (
          <Alert severity="info" sx={{ mt: 1.5 }}>
            This run is paused or parked remotely.
            {parkedReason
              ? ` ${parkedReason}`
              : " The remote control plane did not publish a parked reason."}
          </Alert>
        ) : null}
        {detail.identity?.available === false ? (
          <Alert severity="info" sx={{ mt: 1.5 }}>
            {unavailableReason(detail.identity)}
          </Alert>
        ) : null}
        {identity ? (
          <Card variant="outlined" sx={{ mt: 1.5 }}>
            <CardContent sx={{ "&:last-child": { pb: 1.5 } }}>
              <Typography variant="subtitle2">Run identity</Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 0.5 }}
              >
                Server-derived binding:{" "}
                {[
                  identity.agentId,
                  identity.agentVersion !== undefined
                    ? `agent v${identity.agentVersion}`
                    : null,
                  identity.deploymentVersion !== undefined
                    ? `deployment v${identity.deploymentVersion}`
                    : null,
                  identity.deploymentProcessId
                    ? `process ${identity.deploymentProcessId}`
                    : null,
                ]
                  .filter(Boolean)
                  .join(" · ") || "Identity details unavailable"}
              </Typography>
            </CardContent>
          </Card>
        ) : null}
        {controlReceipt ? (
          <Box sx={{ mt: 1.5 }}>
            <Chip
              size="small"
              color="success"
              variant="outlined"
              label="Control receipt recorded"
            />
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5 }}
            >
              {controlReceipt.auditEntryId ||
                controlReceipt.id ||
                "Remote control receipt"}
              {controlReceipt.hash
                ? ` · ${String(controlReceipt.hash).slice(0, 12)}…`
                : ""}
              {controlReceipt.recordedAt
                ? ` · ${controlReceipt.recordedAt}`
                : ""}
            </Typography>
          </Box>
        ) : null}
        {!conversation && detail.conversation?.available === false ? (
          <Alert severity="info" sx={{ mt: 1.5 }}>
            {unavailableReason(detail.conversation)}
          </Alert>
        ) : null}
        {detail.control?.available === false ? (
          <Alert severity="info" sx={{ mt: 1.5 }}>
            {unavailableReason(detail.control)}
          </Alert>
        ) : null}
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1.5 }}>
          <Chip
            size="small"
            color={streamStatus === "live" ? "success" : "default"}
            label={`Events: ${streamStatus}`}
          />
          <Typography variant="caption" color="text.secondary">
            {events.length} event{events.length === 1 ? "" : "s"} received ·
            reconnects resume from the last remote event id
          </Typography>
        </Stack>
        {events.length > 0 ? (
          <List
            dense
            disablePadding
            sx={{ mt: 1, maxHeight: 180, overflow: "auto" }}
          >
            {events.slice(-50).map((event, index) => (
              <ListItem key={`${event.id || "event"}-${index}`} divider>
                <ListItemText
                  primary={`${event.event}${event.id ? ` · #${event.id}` : ""}`}
                  secondary={
                    typeof event.data === "string"
                      ? event.data
                      : JSON.stringify(event.data)
                  }
                />
              </ListItem>
            ))}
          </List>
        ) : null}
        <Box sx={{ mt: 1.5 }}>
          <TaskTreeInspector treeResult={detail.tree} runId={id} />
        </Box>
        {detail.approvals?.available === false ? (
          <Alert severity="info" sx={{ mt: 1.5 }}>
            {unavailableReason(detail.approvals)}
          </Alert>
        ) : null}
        {approvals.length > 0 ? (
          <Box sx={{ mt: 1.5 }}>
            <Typography variant="subtitle2">Run approvals</Typography>
            <List
              dense
              disablePadding
              sx={{ maxHeight: 180, overflow: "auto" }}
            >
              {approvals.map((row) => {
                const approval = approvalRequest(row);
                const approvalId = approval.id || approval.approvalId;
                const statusValue =
                  approval.status || approval.state || "pending";
                const pending = ["pending", "requested", "open"].includes(
                  String(statusValue).toLowerCase(),
                );
                return (
                  <ListItem
                    key={approvalId || remoteLabel(approval)}
                    divider
                    secondaryAction={
                      pending ? (
                        <Stack direction="row" spacing={0.5}>
                          <Button
                            size="small"
                            variant="contained"
                            onClick={() =>
                              onResolveApproval(approvalId, "approve")
                            }
                            disabled={!approvalId || busyId === approvalId}
                          >
                            Approve
                          </Button>
                          <Button
                            size="small"
                            color="error"
                            onClick={() =>
                              onResolveApproval(approvalId, "deny")
                            }
                            disabled={!approvalId || busyId === approvalId}
                          >
                            Reject
                          </Button>
                        </Stack>
                      ) : null
                    }
                  >
                    <ListItemText
                      primary={remoteLabel(approval, "Approval request")}
                      secondary={`${approval.detail || approval.reason || approval.description || approval.proposedAction?.summary || "Managed run action requires review"} · ${statusValue}`}
                    />
                  </ListItem>
                );
              })}
            </List>
          </Box>
        ) : null}
        {detail.observations?.available === false ? (
          <Alert severity="info" sx={{ mt: 1.5 }}>
            {unavailableReason(detail.observations)}
          </Alert>
        ) : null}
        {observations.length > 0 ? (
          <Box sx={{ mt: 1.5 }}>
            <Typography variant="subtitle2">Run observations</Typography>
            <List
              dense
              disablePadding
              sx={{ maxHeight: 180, overflow: "auto" }}
            >
              {observations.slice(-30).map((observation, index) => (
                <ListItem
                  key={observation.id || observation.observationId || index}
                  divider
                >
                  <ListItemText
                    primary={
                      observation.summary ||
                      observation.name ||
                      observation.kind ||
                      observation.type ||
                      `Observation ${index + 1}`
                    }
                    secondary={
                      observation.occurredAt ||
                      observation.createdAt ||
                      observation.status ||
                      "Remote observation"
                    }
                  />
                </ListItem>
              ))}
            </List>
          </Box>
        ) : null}
        <ObserveExportLinks runId={id} response={detail.observations} />
        <EvidenceInspector
          runId={id}
          evidence={artifacts.evidence}
          proofPack={artifacts.proofPack}
          events={events}
          observations={observations}
          streamStatus={streamStatus}
        />
        {conversation && !paused ? (
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1}
            sx={{ mt: 1.5 }}
          >
            <TextField
              fullWidth
              size="small"
              label="Follow-up or steer"
              value={followUp}
              onChange={(event) => onFollowUpChange(event.target.value)}
              placeholder="Send another instruction to this durable run…"
            />
            <Button
              variant="outlined"
              onClick={onSendFollowUp}
              disabled={followUpBusy || !followUp.trim()}
            >
              {followUpBusy ? "Sending…" : "Send"}
            </Button>
          </Stack>
        ) : null}
        {artifacts.evidence ? (
          <Typography
            component="pre"
            variant="caption"
            sx={{
              mt: 1.5,
              maxHeight: 180,
              overflow: "auto",
              p: 1,
              bgcolor: "action.hover",
              borderRadius: 1,
            }}
          >
            {JSON.stringify(artifacts.evidence, null, 2)}
          </Typography>
        ) : null}
        {artifacts.proofPack ? (
          <Box sx={{ mt: 1.5 }}>
            <Stack direction="row" spacing={1} flexWrap="wrap">
              <Chip
                size="small"
                color={verified ? "success" : "error"}
                label={verified ? "Proof verified offline" : "Proof unverified"}
              />
              {isolation ? (
                <Chip
                  size="small"
                  color={
                    isolation.status === "verified"
                      ? "success"
                      : isolation.status === "unknown"
                        ? "warning"
                        : "error"
                  }
                  label={isolationLabel}
                />
              ) : null}
            </Stack>
            <Typography
              component="pre"
              variant="caption"
              sx={{
                mt: 1,
                maxHeight: 180,
                overflow: "auto",
                p: 1,
                bgcolor: "action.hover",
                borderRadius: 1,
              }}
            >
              {JSON.stringify(artifacts.proofPack, null, 2)}
            </Typography>
          </Box>
        ) : null}
      </CardContent>
    </Card>
  );
}

RunDetailPanel.propTypes = {
  id: PropTypes.string,
  detail: PropTypes.shape({
    conversation: PropTypes.shape({
      available: PropTypes.bool,
      conversation: PropTypes.object,
      messages: PropTypes.array,
    }),
    control: PropTypes.shape({
      available: PropTypes.bool,
      status: PropTypes.string,
      control: PropTypes.object,
    }),
    identity: PropTypes.shape({
      available: PropTypes.bool,
      identity: PropTypes.object,
    }),
    tree: PropTypes.shape({
      available: PropTypes.bool,
      schemaVersion: PropTypes.string,
      runId: PropTypes.string,
      tree: PropTypes.array,
      trees: PropTypes.array,
      tasks: PropTypes.array,
      nodes: PropTypes.array,
      integrity: PropTypes.string,
    }),
    approvals: PropTypes.shape({
      available: PropTypes.bool,
      approvals: PropTypes.array,
    }),
    observations: PropTypes.shape({
      available: PropTypes.bool,
      observations: PropTypes.array,
      items: PropTypes.array,
      observeExports: PropTypes.array,
    }),
  }),
  loading: PropTypes.bool.isRequired,
  artifacts: PropTypes.shape({
    evidence: PropTypes.object,
    proofPack: PropTypes.object,
  }).isRequired,
  events: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      event: PropTypes.string,
      data: PropTypes.any,
    }),
  ).isRequired,
  streamStatus: PropTypes.string.isRequired,
  busyId: PropTypes.string,
  controlReceipt: PropTypes.shape({
    auditEntryId: PropTypes.string,
    id: PropTypes.string,
    hash: PropTypes.string,
    recordedAt: PropTypes.string,
  }),
  followUp: PropTypes.string.isRequired,
  followUpBusy: PropTypes.bool.isRequired,
  onFollowUpChange: PropTypes.func.isRequired,
  onSendFollowUp: PropTypes.func.isRequired,
  onArtifact: PropTypes.func.isRequired,
  onControl: PropTypes.func.isRequired,
  onResolveApproval: PropTypes.func.isRequired,
};

ApprovalPanel.propTypes = {
  result: PropTypes.shape({ available: PropTypes.bool }),
  busyId: PropTypes.string,
  onResolve: PropTypes.func.isRequired,
};

EnvironmentPanel.propTypes = {
  result: PropTypes.shape({ available: PropTypes.bool }),
  revisionsById: PropTypes.object,
  revisionsLoadingId: PropTypes.string,
  onInspectRevisions: PropTypes.func.isRequired,
  onControl: PropTypes.func.isRequired,
  onCreate: PropTypes.func.isRequired,
  onRevise: PropTypes.func.isRequired,
  busyId: PropTypes.string,
};

SchedulePanel.propTypes = {
  client: PropTypes.object.isRequired,
  agents: PropTypes.arrayOf(PropTypes.object).isRequired,
  result: PropTypes.shape({ available: PropTypes.bool }),
  busyId: PropTypes.string,
  selectedId: PropTypes.string,
  detail: PropTypes.shape({
    available: PropTypes.bool,
    schedule: PropTypes.object,
  }),
  detailLoading: PropTypes.bool.isRequired,
  onSelect: PropTypes.func.isRequired,
  onRun: PropTypes.func.isRequired,
  onCreate: PropTypes.func.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
};

MemoryPanel.propTypes = {
  client: PropTypes.object.isRequired,
  scopesResult: PropTypes.shape({ available: PropTypes.bool }),
  graphResult: PropTypes.shape({
    available: PropTypes.bool,
    graph: PropTypes.object,
  }),
  timelineResult: PropTypes.shape({
    available: PropTypes.bool,
    timeline: PropTypes.object,
  }),
  eventsResult: PropTypes.shape({
    available: PropTypes.bool,
    events: PropTypes.array,
    items: PropTypes.array,
  }),
  consolidationResult: PropTypes.shape({
    available: PropTypes.bool,
    consolidation: PropTypes.object,
  }),
  eventDetail: PropTypes.shape({
    available: PropTypes.bool,
    event: PropTypes.object,
  }),
  lineageResult: PropTypes.shape({
    available: PropTypes.bool,
    lineage: PropTypes.object,
  }),
  selectedEventId: PropTypes.string,
  eventLoading: PropTypes.bool,
  exportValue: PropTypes.string,
  onInspectEvent: PropTypes.func.isRequired,
  onExport: PropTypes.func.isRequired,
};

AuditPanel.propTypes = {
  auditResult: PropTypes.shape({
    available: PropTypes.bool,
    code: PropTypes.string,
    reason: PropTypes.string,
  }),
  integrityResult: PropTypes.shape({
    available: PropTypes.bool,
    code: PropTypes.string,
    reason: PropTypes.string,
  }),
};

BillingPanel.propTypes = {
  accountsResult: PropTypes.shape({ available: PropTypes.bool }),
  plansResult: PropTypes.shape({ available: PropTypes.bool }),
  accountId: PropTypes.string,
  onSelectAccount: PropTypes.func.isRequired,
  data: PropTypes.shape({
    subscription: PropTypes.shape({
      available: PropTypes.bool,
      subscription: PropTypes.object,
    }),
    usage: PropTypes.shape({
      available: PropTypes.bool,
      usage: PropTypes.object,
    }),
    attribution: PropTypes.shape({
      available: PropTypes.bool,
      attribution: PropTypes.object,
    }),
    invoice: PropTypes.shape({
      available: PropTypes.bool,
      invoice: PropTypes.object,
    }),
    invoices: PropTypes.shape({
      available: PropTypes.bool,
      issued: PropTypes.array,
    }),
    meter: PropTypes.shape({ available: PropTypes.bool }),
    seats: PropTypes.shape({
      available: PropTypes.bool,
      seats: PropTypes.object,
    }),
    integrity: PropTypes.shape({
      available: PropTypes.bool,
      integrity: PropTypes.object,
    }),
  }),
  period: PropTypes.shape({
    start: PropTypes.string.isRequired,
    end: PropTypes.string.isRequired,
  }).isRequired,
  loading: PropTypes.bool.isRequired,
  mutation: PropTypes.shape({
    busy: PropTypes.bool,
    error: PropTypes.string,
    checkoutUrl: PropTypes.string,
  }),
  seatQuote: PropTypes.shape({
    available: PropTypes.bool,
    quote: PropTypes.object,
    totalCents: PropTypes.number,
  }),
  onSeatQuote: PropTypes.func.isRequired,
  onCheckout: PropTypes.func.isRequired,
  onCancelSubscription: PropTypes.func.isRequired,
  onRefresh: PropTypes.func.isRequired,
};

SandboxPanel.propTypes = {
  sandboxes: PropTypes.arrayOf(PropTypes.object).isRequired,
  result: PropTypes.shape({ available: PropTypes.bool }),
  selectedId: PropTypes.string,
  detail: PropTypes.object,
  services: PropTypes.arrayOf(PropTypes.object).isRequired,
  actionId: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  onControl: PropTypes.func.isRequired,
};

RemoteUnavailable.propTypes = {
  result: PropTypes.shape({ available: PropTypes.bool }),
  children: PropTypes.node.isRequired,
};

export function FleetHealthPanel({ fleet, loading }) {
  if (loading && !fleet) {
    return (
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h5">Fleet health</Typography>
          <Typography color="text.secondary" sx={{ mt: 1 }}>
            Reading the tenant-scoped workforce projection from remote Omega…
          </Typography>
          <CircularProgress size={22} sx={{ mt: 2 }} />
        </CardContent>
      </Card>
    );
  }
  if (!fleet) return null;

  const statuses = Object.entries(fleet.health.byStatus).filter(
    ([, count]) => count > 0,
  );
  const now = Date.now();
  const recentAgents = [...fleet.agents].sort((left, right) => {
    const leftTime = Date.parse(left.updatedAt || "");
    const rightTime = Date.parse(right.updatedAt || "");
    return (
      (Number.isFinite(rightTime) ? rightTime : 0) -
      (Number.isFinite(leftTime) ? leftTime : 0)
    );
  });
  return (
    <Card sx={{ mb: 3 }}>
      <CardContent>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ sm: "center" }}
          gap={1}
        >
          <Box>
            <Typography variant="h5">Fleet health</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              Workspace agents reported by the remote{" "}
              <code>/workspace/workforce</code> projection.
            </Typography>
          </Box>
          <Chip
            size="small"
            variant="outlined"
            label={`${fleet.health.total} agent${fleet.health.total === 1 ? "" : "s"}`}
          />
        </Stack>
        <Grid container spacing={1.5} sx={{ mt: 0.5 }}>
          {statuses.map(([status, count]) => {
            const meta = FLEET_STATUS_META[status] || {
              label: status,
              color: "default",
            };
            return (
              <Grid item xs={6} sm={3} md={2.4} key={status}>
                <Card variant="outlined" sx={{ height: "100%" }}>
                  <CardContent sx={{ "&:last-child": { pb: 2 } }}>
                    <Typography variant="h4">{count}</Typography>
                    <Chip
                      size="small"
                      color={meta.color}
                      label={meta.label}
                      sx={{ mt: 0.5 }}
                    />
                  </CardContent>
                </Card>
              </Grid>
            );
          })}
          {statuses.length === 0 ? (
            <Grid item xs={12}>
              <Typography color="text.secondary">
                No agents are currently registered in this workspace.
              </Typography>
            </Grid>
          ) : null}
        </Grid>
        <Divider sx={{ my: 2 }} />
        <Typography variant="subtitle2">Recent activity</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          Activity freshness comes only from the remote workforce projection.
          Missing timestamps remain unknown.
        </Typography>
        {recentAgents.length === 0 ? (
          <Typography color="text.secondary" sx={{ mt: 1 }}>
            No remote activity is available.
          </Typography>
        ) : (
          <List dense disablePadding sx={{ mt: 0.5 }}>
            {recentAgents.slice(0, 8).map((agent) => {
              const updated = agent.updatedAt
                ? Date.parse(agent.updatedAt)
                : Number.NaN;
              const stale =
                Number.isFinite(updated) && now - updated > 15 * 60 * 1000;
              const freshness = !Number.isFinite(updated)
                ? "timestamp unknown"
                : stale
                  ? "stale projection"
                  : "recent projection";
              return (
                <ListItem key={agent.id} divider>
                  <ListItemText
                    primary={`${agent.name} · ${agent.currentWork || agent.reason || "No activity detail"}`}
                    secondary={`${agent.updatedAt || "Remote timestamp unavailable"} · ${freshness}`}
                  />
                </ListItem>
              );
            })}
          </List>
        )}
      </CardContent>
    </Card>
  );
}

FleetHealthPanel.propTypes = {
  fleet: PropTypes.shape({
    agents: PropTypes.arrayOf(
      PropTypes.shape({
        id: PropTypes.string.isRequired,
        name: PropTypes.string.isRequired,
        currentWork: PropTypes.string,
        reason: PropTypes.string,
        updatedAt: PropTypes.string,
      }),
    ),
    health: PropTypes.shape({
      total: PropTypes.number.isRequired,
      byStatus: PropTypes.objectOf(PropTypes.number).isRequired,
    }).isRequired,
  }),
  loading: PropTypes.bool.isRequired,
};

function CapabilityPanel({ result }) {
  const features =
    result?.available && result.features && typeof result.features === "object"
      ? Object.entries(result.features)
      : [];
  const limits =
    result?.available && result.limits && typeof result.limits === "object"
      ? Object.entries(result.limits)
      : [];
  return (
    <Card sx={{ mb: 3 }}>
      <CardContent>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ sm: "center" }}
          gap={1}
        >
          <Box>
            <Typography variant="h5">Remote capabilities</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              Feature flags and limits are read from the tenant-scoped Omega
              deployment. Squad does not infer support from local code.
            </Typography>
          </Box>
          {result?.available ? (
            <Chip
              size="small"
              color="success"
              variant="outlined"
              label="Server reported"
            />
          ) : null}
        </Stack>
        <RemoteUnavailable result={result}>
          {features.length === 0 && limits.length === 0 ? (
            <Typography color="text.secondary" sx={{ mt: 2 }}>
              The remote deployment did not publish capability details.
            </Typography>
          ) : (
            <Grid container spacing={1.5} sx={{ mt: 0.5 }}>
              {features.map(([name, enabled]) => (
                <Grid item xs={12} sm={6} md={3} key={`feature-${name}`}>
                  <Card variant="outlined">
                    <CardContent sx={{ "&:last-child": { pb: 1.5 } }}>
                      <Stack
                        direction="row"
                        justifyContent="space-between"
                        alignItems="center"
                        gap={1}
                      >
                        <Typography variant="body2">
                          {String(name).replaceAll("_", " ")}
                        </Typography>
                        <Chip
                          size="small"
                          color={enabled === true ? "success" : "default"}
                          label={enabled === true ? "on" : "off"}
                        />
                      </Stack>
                    </CardContent>
                  </Card>
                </Grid>
              ))}
              {limits.map(([name, value]) => (
                <Grid item xs={12} sm={6} md={3} key={`limit-${name}`}>
                  <Card variant="outlined">
                    <CardContent sx={{ "&:last-child": { pb: 1.5 } }}>
                      <Typography variant="caption" color="text.secondary">
                        {String(name).replaceAll("_", " ")}
                      </Typography>
                      <Typography variant="h6">
                        {typeof value === "object"
                          ? JSON.stringify(value)
                          : String(value)}
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>
              ))}
            </Grid>
          )}
        </RemoteUnavailable>
      </CardContent>
    </Card>
  );
}

CapabilityPanel.propTypes = {
  result: PropTypes.shape({
    available: PropTypes.bool,
    features: PropTypes.object,
    limits: PropTypes.object,
  }),
};

/**
 * Customer-facing Squad surface. It deliberately speaks only to the remote Omega control plane:
 * there is no local runtime fallback, local sandbox allocation, or browser-persisted Omega key.
 */
export default function OmegaSquadPage() {
  const { config, scopeKey } = useOmegaSquadConfig();
  return (
    <ScopedOmegaSquadPage key={scopeKey} config={config} scopeKey={scopeKey} />
  );
}

ScopedOmegaSquadPage.propTypes = {
  config: PropTypes.object.isRequired,
  scopeKey: PropTypes.string.isRequired,
};

function ScopedOmegaSquadPage({ config, scopeKey }) {
  const client = useMemo(() => createOmegaSquadClient(config), [config]);
  const [snapshot, setSnapshot] = useState({
    loading: true,
    error: null,
    sandboxError: null,
    sandboxResult: null,
    agents: null,
    sandboxes: null,
    capabilities: null,
    fleet: null,
    approvals: null,
    environments: null,
    schedules: null,
    connectors: null,
    memoryScopes: null,
    memoryGraph: null,
    memoryTimeline: null,
    memoryEvents: null,
    memoryConsolidation: null,
    audit: null,
    auditIntegrity: null,
  });
  const [selectedAgent, setSelectedAgent] = useState("");
  const [agentDetail, setAgentDetail] = useState(null);
  const [agentDetailLoading, setAgentDetailLoading] = useState(false);
  const agentDetailRequestRef = useRef(0);
  const selectedAgentRef = useRef(selectedAgent);
  selectedAgentRef.current = selectedAgent;
  const [runs, setRuns] = useState(null);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [conversationDetail, setConversationDetail] = useState(null);
  const [conversationDetailLoading, setConversationDetailLoading] =
    useState(false);
  const [runControlReceipt, setRunControlReceipt] = useState(null);
  const [runArtifacts, setRunArtifacts] = useState({
    evidence: null,
    proofPack: null,
  });
  const [runEvents, setRunEvents] = useState([]);
  const [runStreamStatus, setRunStreamStatus] = useState("idle");
  const [runStreamGeneration, setRunStreamGeneration] = useState(0);
  const [followUp, setFollowUp] = useState("");
  const [followUpBusy, setFollowUpBusy] = useState(false);
  const followUpRequestRef = useRef(null);
  const [input, setInput] = useState("");
  const [runMode, setRunMode] = useState("agent");
  const [runState, setRunState] = useState({ loading: false, error: null });
  const runScopeRef = useRef(null);
  const runsRequestRef = useRef(0);
  const detailRequestRef = useRef(0);
  const selectedRunRef = useRef("");
  const [runsError, setRunsError] = useState(null);
  const [consoleTab, setConsoleTab] = useState("approvals");
  const [createAgentRequest, setCreateAgentRequest] = useState(0);
  const [importDefinitionRequest, setImportDefinitionRequest] = useState(0);
  const [selectedSandboxId, setSelectedSandboxId] = useState("");
  const [sandboxDetail, setSandboxDetail] = useState(null);
  const [sandboxServices, setSandboxServices] = useState([]);
  const [sandboxActionId, setSandboxActionId] = useState("");
  const [controlAction, setControlAction] = useState({ id: "", error: null });
  const [memoryExport, setMemoryExport] = useState("");
  const [selectedMemoryEventId, setSelectedMemoryEventId] = useState("");
  const [memoryEventDetail, setMemoryEventDetail] = useState(null);
  const [memoryLineage, setMemoryLineage] = useState(null);
  const [memoryEventLoading, setMemoryEventLoading] = useState(false);
  const [selectedScheduleId, setSelectedScheduleId] = useState("");
  const [scheduleDetail, setScheduleDetail] = useState(null);
  const [scheduleDetailLoading, setScheduleDetailLoading] = useState(false);
  const [environmentRevisions, setEnvironmentRevisions] = useState({});
  const [environmentRevisionsLoadingId, setEnvironmentRevisionsLoadingId] =
    useState("");
  const [billingAccountsResult, setBillingAccountsResult] = useState(null);
  const [billingPlansResult, setBillingPlansResult] = useState(null);
  const [billingAccountId, setBillingAccountId] = useState("");
  const [billingData, setBillingData] = useState(null);
  const [billingLoading, setBillingLoading] = useState(false);
  const [billingMutation, setBillingMutation] = useState({
    busy: false,
    error: null,
    checkoutUrl: "",
  });
  const [billingSeatQuote, setBillingSeatQuote] = useState(null);
  const billingPeriod = useMemo(() => currentBillingPeriod(), []);

  const loadBillingAccount = useCallback(
    async (id) => {
      if (!id) {
        setBillingData(null);
        return;
      }
      setBillingLoading(true);
      const query = { start: billingPeriod.start, end: billingPeriod.end };
      const [
        subscription,
        usage,
        attribution,
        invoice,
        invoices,
        meter,
        integrity,
        seats,
      ] = await Promise.all([
        client.getBillingSubscription(id),
        client.getBillingUsage(id, query),
        client.getBillingUsageAttribution(id, { ...query, by: "model" }),
        client.getBillingInvoice(id, query),
        client.getBillingInvoices(id),
        client.getBillingMeterReconciliation(id, query),
        client.getBillingIntegrity(id),
        client.getBillingSeats(id),
      ]);
      setBillingData({
        subscription,
        usage,
        attribution,
        invoice,
        invoices,
        meter,
        integrity,
        seats,
      });
      setBillingLoading(false);
    },
    [billingPeriod, client],
  );

  const getBillingSeatQuote = useCallback(
    (id, seats) => {
      if (!id || !Number.isSafeInteger(seats) || seats < 1)
        return Promise.resolve({
          available: false,
          code: "INVALID_INPUT",
          reason: "A positive target seat count is required.",
        });
      return client.getBillingSeatQuote(id, { seats });
    },
    [client],
  );

  const previewBillingSeats = useCallback(
    async (id, seats) => {
      const result = await getBillingSeatQuote(id, seats);
      setBillingSeatQuote(result);
      return result;
    },
    [getBillingSeatQuote],
  );

  const loadBillingAccounts = useCallback(async () => {
    setBillingLoading(true);
    const [result, plans] = await Promise.all([
      client.listBillingAccounts({ limit: 100 }),
      client.listBillingPlans(),
    ]);
    setBillingAccountsResult(result);
    setBillingPlansResult(plans);
    if (!result.available) {
      setBillingAccountId("");
      setBillingData(null);
      setBillingLoading(false);
      return;
    }
    const accounts = collection(result, "accounts");
    setBillingAccountId((current) => {
      if (
        current &&
        accounts.some(
          (account) => (account.id || account.accountId) === current,
        )
      )
        return current;
      return accounts[0]?.id || accounts[0]?.accountId || "";
    });
    setBillingLoading(false);
  }, [client]);

  const openBillingCheckout = useCallback(
    async (id, planId) => {
      if (!id || !planId) return;
      setBillingMutation({ busy: true, error: null, checkoutUrl: "" });
      const result = await client.startSubscriptionCheckout(
        id,
        { planId },
        requestKey(`billing-checkout-${id}-${planId}`),
      );
      if (!result.available) {
        setBillingMutation({
          busy: false,
          error: unavailableReason(result),
          checkoutUrl: "",
        });
        return;
      }
      const checkoutUrl =
        typeof result.url === "string" && /^https:\/\/[^\s]+$/u.test(result.url)
          ? result.url
          : "";
      setBillingMutation({
        busy: false,
        error: checkoutUrl
          ? null
          : "Remote billing did not return a safe hosted checkout URL.",
        checkoutUrl,
      });
      await loadBillingAccount(id);
    },
    [client, loadBillingAccount],
  );

  const cancelBillingSubscription = useCallback(
    async (id) => {
      if (!id) return;
      if (
        typeof globalThis.confirm === "function" &&
        !globalThis.confirm("Cancel this remote subscription immediately?")
      )
        return;
      setBillingMutation({ busy: true, error: null, checkoutUrl: "" });
      const result = await client.cancelSubscription(
        id,
        requestKey(`billing-cancel-${id}`),
      );
      if (!result.available) {
        setBillingMutation({
          busy: false,
          error: unavailableReason(result),
          checkoutUrl: "",
        });
        return;
      }
      setBillingMutation({ busy: false, error: null, checkoutUrl: "" });
      await loadBillingAccount(id);
    },
    [client, loadBillingAccount],
  );

  const refreshBilling = useCallback(async () => {
    await loadBillingAccounts();
    if (billingAccountId) await loadBillingAccount(billingAccountId);
  }, [billingAccountId, loadBillingAccount, loadBillingAccounts]);

  const inspectEnvironmentRevisions = useCallback(
    async (id) => {
      if (!id) return;
      setEnvironmentRevisionsLoadingId(id);
      try {
        const result = await client.listEnvironmentRevisions(id);
        setEnvironmentRevisions((current) => ({ ...current, [id]: result }));
      } finally {
        setEnvironmentRevisionsLoadingId("");
      }
    },
    [client],
  );

  const inspectMemoryEvent = useCallback(
    async (id) => {
      if (!id) {
        setSelectedMemoryEventId("");
        setMemoryEventDetail(null);
        setMemoryLineage(null);
        return;
      }
      setSelectedMemoryEventId(id);
      setMemoryEventLoading(true);
      const [detail, lineage] = await Promise.all([
        client.getMemoryEvent(id),
        client.getMemoryLineage(id),
      ]);
      setMemoryEventDetail(detail);
      setMemoryLineage(lineage);
      setMemoryEventLoading(false);
    },
    [client],
  );

  const loadMemoryActivity = useCallback(async () => {
    const [events, consolidation] = await Promise.all([
      client.getMemoryEvents({ limit: 25 }),
      client.getMemoryConsolidation({ limit: 25 }),
    ]);
    setSnapshot((current) => ({
      ...current,
      memoryEvents: events,
      memoryConsolidation: consolidation,
    }));
  }, [client]);

  useEffect(() => {
    if (consoleTab === "billing" && billingAccountsResult === null)
      void loadBillingAccounts();
  }, [billingAccountsResult, consoleTab, loadBillingAccounts]);

  useEffect(() => {
    if (consoleTab === "billing" && billingAccountId)
      void loadBillingAccount(billingAccountId);
  }, [billingAccountId, consoleTab, loadBillingAccount]);

  useEffect(() => {
    if (consoleTab === "memory" && snapshot.memoryEvents === null)
      void loadMemoryActivity();
  }, [consoleTab, loadMemoryActivity, snapshot.memoryEvents]);

  const load = useCallback(async () => {
    setSnapshot((current) => ({ ...current, loading: true, error: null }));
    // Bare sandbox registries remain operator-global in the current Omega EE contract.
    // Connector catalog and sync reads are tenant-derived projections; mutation routes
    // stay closed in the browser and credentials remain server-owned.
    const operatorOnly = (label) => ({
      available: false,
      code: "OPERATOR_ONLY",
      reason: `${label} requires an Omega operator session.`,
    });
    const [
      capabilities,
      fleet,
      approvals,
      environments,
      schedules,
      connectors,
      memoryScopes,
      memoryGraph,
      memoryTimeline,
    ] = await Promise.all([
      client.getCapabilities(),
      client.getFleetHealth(),
      client.listApprovals(),
      client.listEnvironments(),
      client.listSchedules(),
      client.listConnectors(),
      client.listMemoryScopes(),
      client.getMemoryGraph(),
      client.getMemoryTimeline({ limit: 25 }),
    ]);
    const sandboxes = operatorOnly("Sandbox administration");
    // Workforce is the customer-scoped roster. Sandbox inventory is an
    // optional remote operator projection and must not make a valid tenant
    // roster look unavailable when the deployment correctly denies it.
    const firstError = [capabilities, fleet].find(
      (result) => !result.available,
    );
    const nextAgents = fleet.available ? fleet.agents : [];
    setSnapshot({
      loading: false,
      error: firstError ? unavailableReason(firstError) : null,
      agents: nextAgents,
      sandboxes: sandboxes.available ? sandboxes.items : [],
      sandboxError: sandboxes.available ? null : unavailableReason(sandboxes),
      sandboxResult: sandboxes,
      capabilities,
      fleet: fleet.available ? fleet : null,
      approvals,
      environments,
      schedules,
      connectors,
      memoryScopes,
      memoryGraph,
      memoryTimeline,
      memoryEvents: null,
      memoryConsolidation: null,
      audit: null,
      auditIntegrity: null,
    });
    setSelectedAgent((current) =>
      nextAgents.some((agent) => agent.id === current)
        ? current
        : nextAgents[0]?.id || "",
    );
    setSelectedSandboxId((current) => {
      const nextSandboxes = sandboxes.available ? sandboxes.items : [];
      return nextSandboxes.some(
        (sandbox) => (sandbox.id || sandbox.sandboxId) === current,
      )
        ? current
        : nextSandboxes[0]?.id || nextSandboxes[0]?.sandboxId || "";
    });
    setSelectedScheduleId((current) => {
      const nextSchedules = schedules.available
        ? collection(schedules, "schedules")
        : [];
      return nextSchedules.some(
        (schedule) => (schedule.id || schedule.scheduleId) === current,
      )
        ? current
        : nextSchedules[0]?.id || nextSchedules[0]?.scheduleId || "";
    });
  }, [client]);

  const loadRuns = useCallback(async () => {
    const scope = runScopeRef.current;
    if (!selectedAgent || scope?.agentId !== selectedAgent) return;
    const requestNumber = ++runsRequestRef.current;
    setRunsError(null);
    try {
      const result = await client.listConversations({
        agentId: selectedAgent,
        limit: 10,
      });
      if (
        scope !== runScopeRef.current ||
        requestNumber !== runsRequestRef.current
      )
        return;
      if (!result.available) {
        setRunsError(unavailableReason(result));
        return;
      }
      setRuns((current) => {
        const next = result.conversations;
        // A newly accepted conversation may not appear in the list projection yet.
        // Retain its actual server-returned record while it is selected.
        const selected = Array.isArray(current)
          ? current.find(
              (run) =>
                (run.id || run.conversationId) === selectedRunRef.current,
            )
          : null;
        return selected &&
          !next.some(
            (run) => (run.id || run.conversationId) === selectedRunRef.current,
          )
          ? [selected, ...next]
          : next;
      });
    } catch {
      if (
        scope === runScopeRef.current &&
        requestNumber === runsRequestRef.current
      ) {
        setRunsError(
          "We couldn't refresh the recent runs. Your current run remains open.",
        );
      }
    }
  }, [client, selectedAgent]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    // A fresh scope also fences A -> B -> A selection changes and unmounts.
    const scope = { agentId: selectedAgent };
    runScopeRef.current = scope;
    selectedRunRef.current = "";
    detailRequestRef.current += 1;
    setRuns(null);
    setRunsError(null);
    setSelectedRunId("");
    setConversationDetail(null);
    setConversationDetailLoading(false);
    setRunArtifacts({ evidence: null, proofPack: null });
    setRunControlReceipt(null);
    setRunEvents([]);
    setRunStreamStatus("idle");
    setFollowUp("");
    setFollowUpBusy(false);
    setRunState({ loading: false, error: null });
    void loadRuns();
    return () => {
      if (runScopeRef.current === scope) runScopeRef.current = null;
    };
  }, [loadRuns, selectedAgent]);

  const inspectAgent = useCallback(
    async (id) => {
      if (selectedAgentRef.current !== id) return;
      const generation = ++agentDetailRequestRef.current;
      if (!id) {
        setAgentDetail(null);
        setAgentDetailLoading(false);
        return;
      }
      setAgentDetailLoading(true);
      setAgentDetail(null);
      try {
        const result = await client.getWorkforceAgent(id);
        if (
          generation === agentDetailRequestRef.current &&
          selectedAgentRef.current === id
        )
          setAgentDetail(result);
      } catch {
        if (
          generation === agentDetailRequestRef.current &&
          selectedAgentRef.current === id
        )
          setAgentDetail({
            available: false,
            reason: "Couldn't read this agent. Try selecting it again.",
          });
      } finally {
        if (
          generation === agentDetailRequestRef.current &&
          selectedAgentRef.current === id
        )
          setAgentDetailLoading(false);
      }
    },
    [client],
  );

  useEffect(() => {
    void inspectAgent(selectedAgent);
    return () => {
      agentDetailRequestRef.current += 1;
    };
  }, [inspectAgent, selectedAgent]);

  const inspectConversation = useCallback(
    async (id, { background = false } = {}) => {
      const scope = runScopeRef.current;
      if (!scope || scope.agentId !== selectedAgent) return;
      if (background && selectedRunRef.current !== id) return;
      const requestNumber = ++detailRequestRef.current;
      const changedRun = selectedRunRef.current !== id;
      selectedRunRef.current = id;
      setSelectedRunId(id);
      if (!background) setConversationDetailLoading(Boolean(id));
      if (changedRun || !id) {
        setConversationDetail(null);
        setRunArtifacts({ evidence: null, proofPack: null });
        setRunControlReceipt(null);
        setRunEvents([]);
        setRunStreamStatus(id ? "connecting" : "idle");
        setFollowUp("");
        setFollowUpBusy(false);
      }
      if (!id) return;
      try {
        const [conversation, control, tree, approvals, observations] =
          await Promise.all([
            client.getConversation(id),
            client.getConversationControlStatus(id),
            client.getConversationTree(id),
            client.listConversationApprovals(id),
            client.getConversationObservations(id, { limit: 100 }),
          ]);
        if (
          scope !== runScopeRef.current ||
          requestNumber !== detailRequestRef.current ||
          selectedRunRef.current !== id
        )
          return;
        const taskId =
          conversation?.available === true
            ? conversation.conversation?.managedExecution?.taskId
            : undefined;
        const identity =
          typeof taskId === "string" && taskId.trim().length > 0
            ? await client.getWorkforceAgentRunIdentity(selectedAgent, taskId)
            : {
                available: false,
                reason:
                  "Run identity is unavailable until the server reports a managed task id.",
              };
        if (
          scope !== runScopeRef.current ||
          requestNumber !== detailRequestRef.current ||
          selectedRunRef.current !== id
        )
          return;
        setConversationDetail({
          conversation,
          control,
          identity,
          tree,
          approvals,
          observations,
        });
      } catch {
        if (
          scope === runScopeRef.current &&
          requestNumber === detailRequestRef.current
        ) {
          setConversationDetail({
            conversation: {
              available: false,
              reason:
                "We couldn't read this run's details. Select it again to retry.",
            },
          });
        }
      } finally {
        if (
          scope === runScopeRef.current &&
          requestNumber === detailRequestRef.current
        )
          setConversationDetailLoading(false);
      }
    },
    [client, selectedAgent],
  );

  useEffect(() => {
    const scope = runScopeRef.current;
    if (
      !selectedRunId ||
      selectedRunRef.current !== selectedRunId ||
      scope?.agentId !== selectedAgent
    ) {
      setRunStreamStatus("idle");
      return undefined;
    }
    let active = true;
    let lastEventId;
    let retryTimer;
    let refreshTimer;
    let refreshFlight = false;
    let refreshPending = false;
    let terminalEvent = false;
    let controller;
    const isCurrent = () =>
      active &&
      scope === runScopeRef.current &&
      selectedRunRef.current === selectedRunId;
    const refreshCurrentRun = async () => {
      refreshTimer = undefined;
      if (!isCurrent()) return;
      if (refreshFlight) {
        refreshPending = true;
        return;
      }
      refreshFlight = true;
      refreshPending = false;
      try {
        await Promise.all([
          inspectConversation(selectedRunId, { background: true }),
          loadRuns(),
        ]);
      } finally {
        refreshFlight = false;
        if (refreshPending && isCurrent())
          refreshTimer = setTimeout(refreshCurrentRun, 500);
      }
    };
    const queueRefresh = () => {
      if (!isCurrent()) return;
      if (refreshFlight) {
        refreshPending = true;
        return;
      }
      // Coalesce a burst without resetting the timer on every token, so long streams still refresh.
      if (refreshTimer === undefined)
        refreshTimer = setTimeout(refreshCurrentRun, 500);
    };
    const connect = async () => {
      if (!active) return;
      controller = new AbortController();
      setRunStreamStatus((current) =>
        current === "live" ? current : "connecting",
      );
      const result = await client.openConversationEvents(selectedRunId, {
        lastEventId,
        signal: controller.signal,
      });
      if (!active) return;
      if (!result.available) {
        setRunStreamStatus("unavailable");
        return;
      }
      setRunStreamStatus("live");
      try {
        await consumeSse(
          result.response,
          (event) => {
            if (!isCurrent() || controller.signal.aborted) return;
            if (event.id !== undefined) lastEventId = event.id;
            setRunEvents((current) => [...current, event].slice(-100));
            const type =
              typeof event.data?.type === "string"
                ? event.data.type
                : event.event;
            if (type === "done") terminalEvent = true;
            if (type === "message_delta") terminalEvent = false;
            if (type === "status")
              terminalEvent = [
                "done",
                "completed",
                "error",
                "failed",
                "canceled",
                "cancelled",
              ].includes(event.data?.status);
            if (
              [
                "message_delta",
                "message_complete",
                "status",
                "done",
                "error",
                "approval_request",
                "approval_resolved",
              ].includes(type)
            )
              queueRefresh();
          },
          controller.signal,
        );
      } catch {
        if (!active || controller.signal.aborted) return;
      }
      if (active && !controller.signal.aborted) {
        queueRefresh();
        if (terminalEvent) {
          setRunStreamStatus("complete");
          return;
        }
        setRunStreamStatus("reconnecting");
        retryTimer = setTimeout(connect, 1500);
      }
    };
    void connect().catch(() => {
      if (active) setRunStreamStatus("unavailable");
    });
    return () => {
      active = false;
      controller?.abort();
      if (retryTimer) clearTimeout(retryTimer);
      if (refreshTimer !== undefined) clearTimeout(refreshTimer);
    };
  }, [
    client,
    inspectConversation,
    loadRuns,
    selectedAgent,
    selectedRunId,
    runStreamGeneration,
  ]);

  const loadRunArtifact = async (kind) => {
    if (!selectedRunId) return;
    const result =
      kind === "evidence"
        ? await client.getConversationEvidence(selectedRunId)
        : await client.getConversationProofPack(selectedRunId);
    setRunArtifacts((current) => ({ ...current, [kind]: result }));
  };

  const inspectSandbox = useCallback(
    async (id) => {
      if (!id) {
        setSandboxDetail(null);
        setSandboxServices([]);
        return;
      }
      const [detail, services] = await Promise.all([
        client.getSandbox(id),
        client.listSandboxServices(id),
      ]);
      setSandboxDetail(
        detail.available
          ? detail.sandbox || detail
          : { error: unavailableReason(detail) },
      );
      setSandboxServices(collection(services, "services"));
    },
    [client],
  );

  useEffect(() => {
    void inspectSandbox(selectedSandboxId);
  }, [inspectSandbox, selectedSandboxId]);

  const inspectSchedule = useCallback(
    async (id) => {
      if (!id) {
        setScheduleDetail(null);
        setScheduleDetailLoading(false);
        return;
      }
      setScheduleDetailLoading(true);
      const result = await client.getSchedule(id);
      setScheduleDetail(result);
      setScheduleDetailLoading(false);
    },
    [client],
  );

  useEffect(() => {
    void inspectSchedule(selectedScheduleId);
  }, [inspectSchedule, selectedScheduleId]);

  const startRun = async () => {
    const scope = runScopeRef.current;
    if (
      !selectedAgent ||
      !input.trim() ||
      runState.loading ||
      scope?.agentId !== selectedAgent
    )
      return;
    const agentId = selectedAgent;
    const draftInput = input;
    setRunState({ loading: true, error: null });
    let result;
    try {
      result = await client.createConversation(draftInput.trim(), {
        mode: runMode,
        agentId,
        idempotencyKey: requestKey(`conversation-create-${agentId}`),
      });
    } catch {
      if (scope === runScopeRef.current)
        setRunState({
          loading: false,
          error:
            "We couldn't start this remote run. Your task is still here; try again.",
        });
      return;
    }
    if (scope !== runScopeRef.current) return;
    if (!result.available) {
      setRunState({ loading: false, error: unavailableReason(result) });
      return;
    }
    const conversationId = result.conversation?.id;
    if (typeof conversationId !== "string" || !conversationId.trim()) {
      setRunState({
        loading: false,
        error:
          "Remote Omega accepted the request without returning a conversation id. Refresh recent runs before starting another run.",
      });
      void loadRuns();
      return;
    }
    // Use the accepted record now; list refresh and detail reads must not delay
    // selection or the event stream, even if the recent-runs projection lags.
    setRuns((current) => [
      result.conversation,
      ...(Array.isArray(current)
        ? current.filter(
            (run) => (run.id || run.conversationId) !== conversationId,
          )
        : []),
    ]);
    setInput((current) => (current === draftInput ? "" : current));
    setRunState({ loading: false, error: null });
    void inspectConversation(conversationId);
    void loadRuns();
  };

  const createAgent = async (input) => {
    setControlAction({ id: "agent-editor", error: null });
    const result = await client.createWorkforceAgent(
      input,
      requestKey("agent-create"),
    );
    if (!result.available) {
      setControlAction({ id: "", error: unavailableReason(result) });
      return result;
    }
    const id = result.agent?.id || "";
    setControlAction({ id: "", error: null });
    if (id) setSelectedAgent(id);
    await load();
    if (id) await inspectAgent(id);
    return result;
  };

  const updateAgent = async (id, input) => {
    if (!id) return { available: false, reason: "An agent id is required." };
    setControlAction({ id: "agent-editor", error: null });
    const result = await client.updateWorkforceAgent(
      id,
      input,
      requestKey(`agent-update-${id}`),
    );
    if (!result.available) {
      setControlAction({ id: "", error: unavailableReason(result) });
      return result;
    }
    setControlAction({ id: "", error: null });
    await Promise.all([load(), inspectAgent(id)]);
    return result;
  };

  const controlAgent = async (id, action) => {
    if (!id) return;
    setControlAction({ id, error: null });
    const result = await client.controlWorkforceAgent(
      id,
      action,
      requestKey(`agent-${id}-${action}`),
    );
    if (!result.available) {
      setControlAction({ id: "", error: unavailableReason(result) });
      return result;
    }
    setControlAction({ id: "", error: null });
    await Promise.all([load(), inspectAgent(id)]);
    return result;
  };

  const controlRun = async (id, action) => {
    if (!id) return;
    if (!confirmRunControl(action)) return null;
    setControlAction({ id, error: null });
    const result = await client.controlConversation(
      id,
      action,
      {},
      requestKey(`conversation-${id}-${action}`),
    );
    if (!result.available) {
      setControlAction({ id: "", error: unavailableReason(result) });
      return result;
    }
    setControlAction({ id: "", error: null });
    await Promise.all([loadRuns(), inspectConversation(id)]);
    if (result.receipt && typeof result.receipt === "object") {
      setRunControlReceipt(result.receipt);
    } else if (
      result.transport?.headers &&
      typeof result.transport.headers === "object"
    ) {
      const headers = result.transport.headers;
      const receiptId =
        headers["x-omega-receipt-id"] ||
        headers["x-omega-operation-id"] ||
        headers["x-operation-id"] ||
        headers["x-request-id"];
      if (receiptId)
        setRunControlReceipt({
          id: receiptId,
          recordedAt: new Date().toISOString(),
        });
    }
    return result;
  };

  const sendFollowUp = async () => {
    if (!selectedRunId || !followUp.trim() || followUpBusy) return;
    if (!confirmFollowUp()) return;
    const scope = runScopeRef.current;
    const id = selectedRunId;
    const input = followUp.trim();
    const current = () =>
      scope === runScopeRef.current && selectedRunRef.current === id;
    if (
      followUpRequestRef.current?.scope !== scope ||
      followUpRequestRef.current?.id !== id ||
      followUpRequestRef.current?.input !== input
    ) {
      followUpRequestRef.current = {
        scope,
        id,
        input,
        key: requestKey(`conversation-${id}-follow-up`),
      };
    }
    const pending = followUpRequestRef.current;
    setFollowUpBusy(true);
    try {
      const result = await client.sendConversationMessage(id, input, {
        idempotencyKey: pending.key,
      });
      if (!current()) return;
      if (!result.available) {
        setControlAction({ id: "", error: unavailableReason(result) });
        return;
      }
      setFollowUp((value) => (value.trim() === input ? "" : value));
      if (followUpRequestRef.current === pending)
        followUpRequestRef.current = null;
      setControlAction({ id: "", error: null });
      // A completed turn closes its SSE response. A new turn on the same conversation
      // needs a fresh stream even though the selected run id has not changed.
      setRunStreamGeneration((value) => value + 1);
      await inspectConversation(id, { background: true });
      if (current() && result.receipt && typeof result.receipt === "object")
        setRunControlReceipt(result.receipt);
    } catch {
      if (current())
        setControlAction({
          id: "",
          error:
            "We couldn't send this follow-up. Your instruction is still here; try again.",
        });
    } finally {
      if (current()) setFollowUpBusy(false);
    }
  };

  const resolveApproval = async (id, decision) => {
    if (!id) return;
    setControlAction({ id, error: null });
    const result = await client.resolveApproval(
      id,
      { decision },
      requestKey(`approval-${id}`),
    );
    if (!result.available) {
      setControlAction({ id: "", error: unavailableReason(result) });
      return;
    }
    setControlAction({ id: "", error: null });
    await load();
    return result;
  };

  const resolveRunApproval = async (id, decision) => {
    const result = await resolveApproval(id, decision);
    if (result?.available && selectedRunId)
      await inspectConversation(selectedRunId);
    return result;
  };

  const controlEnvironment = async (id, action, expectedRevision) => {
    if (!id) return;
    setControlAction({ id, error: null });
    const result = await client.controlEnvironment(
      id,
      { action, expectedRevision },
      requestKey(`environment-${id}-${action}`),
    );
    if (!result.available) {
      setControlAction({ id: "", error: unavailableReason(result) });
      return;
    }
    setControlAction({ id: "", error: null });
    await load();
  };

  const createEnvironment = async (input) => {
    setControlAction({ id: "environment-editor", error: null });
    const result = await client.createEnvironment(
      input,
      requestKey("environment-create"),
    );
    setControlAction({
      id: "",
      error: result.available ? null : unavailableReason(result),
    });
    if (result.available) await load();
    return result;
  };

  const reviseEnvironment = async (id, input) => {
    if (!id)
      return { available: false, reason: "An environment id is required." };
    setControlAction({ id: "environment-editor", error: null });
    const result = await client.reviseEnvironment(
      id,
      input,
      requestKey(`environment-revise-${id}`),
    );
    setControlAction({
      id: "",
      error: result.available ? null : unavailableReason(result),
    });
    if (result.available) await load();
    return result;
  };

  const runSchedule = async (id) => {
    if (!id) return;
    setControlAction({ id, error: null });
    const result = await client.runSchedule(
      id,
      {},
      requestKey(`schedule-${id}`),
    );
    setControlAction({
      id: "",
      error: result.available ? null : unavailableReason(result),
    });
    if (result.available) await Promise.all([load(), inspectSchedule(id)]);
  };

  const createSchedule = async (input) => {
    setControlAction({ id: "schedule-editor", error: null });
    const result = await client.createSchedule(
      input,
      requestKey("schedule-create"),
    );
    if (!result.available) {
      setControlAction({ id: "", error: unavailableReason(result) });
      return result;
    }
    const id = result.schedule?.id || result.schedule?.scheduleId || "";
    setControlAction({ id: "", error: null });
    if (id) setSelectedScheduleId(id);
    await load();
    if (id) await inspectSchedule(id);
    return result;
  };

  const updateSchedule = async (id, input) => {
    if (!id) return { available: false, reason: "A schedule id is required." };
    setControlAction({ id: "schedule-editor", error: null });
    const result = await client.updateSchedule(
      id,
      input,
      requestKey(`schedule-update-${id}`),
    );
    if (!result.available) {
      setControlAction({ id: "", error: unavailableReason(result) });
      return result;
    }
    setControlAction({ id: "", error: null });
    await Promise.all([load(), inspectSchedule(id)]);
    return result;
  };

  const deleteSchedule = async (id) => {
    if (!id) return;
    if (
      typeof window !== "undefined" &&
      typeof window.confirm === "function" &&
      !window.confirm(
        "Delete this remote schedule? Its durable run history will remain on the remote audit log.",
      )
    )
      return;
    setControlAction({ id, error: null });
    const result = await client.deleteSchedule(
      id,
      requestKey(`schedule-delete-${id}`),
    );
    if (!result.available) {
      setControlAction({ id: "", error: unavailableReason(result) });
      return;
    }
    setControlAction({ id: "", error: null });
    setSelectedScheduleId("");
    setScheduleDetail(null);
    await load();
  };

  const exportMemory = async () => {
    const result = await client.exportMemory();
    setMemoryExport(
      result.available
        ? JSON.stringify(result, null, 2)
        : unavailableReason(result),
    );
  };

  const controlSandbox = async (id, action) => {
    if (!id) return;
    setSandboxActionId(id);
    const result = await client.controlSandbox(id, action);
    setSandboxActionId("");
    if (!result.available) {
      setControlAction({ id: "", error: unavailableReason(result) });
      return;
    }
    await Promise.all([load(), inspectSandbox(id)]);
  };

  const agents = snapshot.agents || [];
  const societyEnabled =
    snapshot.capabilities?.available === true &&
    snapshot.capabilities.features?.society === true;
  const active = agents.filter(
    (agent) => agent.status === "idle" || agent.status === "working",
  ).length;
  const sandboxes = snapshot.sandboxes || [];
  const activeSandboxes = sandboxes.filter(
    (sandbox) => sandbox.state === "active",
  ).length;
  const selected = agents.find((agent) => agent.id === selectedAgent);
  // Omega EE's bare connector catalog and sandbox plane are operator-global. A tenant
  // session receives a typed authz or route refusal for those routes; hide those controls
  // after the first load instead of presenting buttons that can only fail. Memory uses the
  // tenant-safe read projections, so a temporary outage remains visible in the panel.
  const sandboxOperatorDenied = isOperatorDenied(snapshot.sandboxResult);
  const connectorOperatorDenied = isOperatorDenied(snapshot.connectors);
  const memoryOperatorDenied =
    isOperatorDenied(snapshot.memoryScopes) &&
    isOperatorDenied(snapshot.memoryGraph);
  const hiddenOperatorSurfaceCount = [
    sandboxOperatorDenied,
    connectorOperatorDenied,
    memoryOperatorDenied,
  ].filter(Boolean).length;

  useEffect(() => {
    if (!societyEnabled && runMode === "society") setRunMode("agent");
  }, [runMode, societyEnabled]);

  useEffect(() => {
    if (
      (consoleTab === "sandboxes" && sandboxOperatorDenied) ||
      (consoleTab === "connectors" && connectorOperatorDenied) ||
      (consoleTab === "memory" && memoryOperatorDenied)
    ) {
      setConsoleTab("approvals");
    }
  }, [
    consoleTab,
    connectorOperatorDenied,
    memoryOperatorDenied,
    sandboxOperatorDenied,
  ]);

  return (
    <Box sx={{ p: { xs: 2, md: 4 }, maxWidth: 1480, mx: "auto" }}>
      <Stack
        direction={{ xs: "column", md: "row" }}
        justifyContent="space-between"
        gap={2}
        sx={{ mb: 3 }}
      >
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <Iconify
              icon="solar:cloud-outline"
              sx={{ color: "primary.main" }}
            />
            <Typography variant="h3">Omega Squad</Typography>
            <Chip size="small" color="info" label="REMOTE" />
          </Stack>
          <Typography color="text.secondary" sx={{ mt: 1 }}>
            Run FutureAGI agents against the remote Omega managed control plane.
          </Typography>
        </Box>
        <Button
          variant="outlined"
          startIcon={<Iconify icon="solar:refresh-linear" />}
          onClick={() => void load()}
          disabled={snapshot.loading}
        >
          Refresh fleet
        </Button>
      </Stack>

      {!config.baseUrl ? (
        <Alert severity="warning" sx={{ mb: 3 }}>
          Configure <code>VITE_OMEGA_SQUAD_API_URL</code> or inject{" "}
          <code>window.__OMEGA_SQUAD_CONFIG__</code>. Squad never falls back to
          a local Omega runtime.
        </Alert>
      ) : null}
      {snapshot.error ? (
        <Alert severity="error" sx={{ mb: 3 }}>
          {snapshot.error}
        </Alert>
      ) : null}

      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={12} sm={4}>
          <Metric
            label="Managed agents"
            value={agents.length}
            detail={`${active} ready or working remotely`}
          />
        </Grid>
        <Grid item xs={12} sm={4}>
          <Metric
            label="Cloud workspaces"
            value={sandboxes.length || "—"}
            detail={
              snapshot.sandboxError ||
              `${activeSandboxes} active remote sandboxes`
            }
          />
        </Grid>
        <Grid item xs={12} sm={4}>
          <Metric
            label="Control plane"
            value={snapshot.capabilities?.available ? "Connected" : "Unknown"}
            detail={config.baseUrl || "Remote URL required"}
          />
        </Grid>
      </Grid>

      <FleetHealthPanel fleet={snapshot.fleet} loading={snapshot.loading} />
      <CapabilityPanel result={snapshot.capabilities} />

      <SquadLaunchNav
        onSurfaceChange={(surface) => {
          if (
            ["approvals", "billing", "connectors", "skills"].includes(surface)
          )
            setConsoleTab(surface);
          if (surface === "approvals" || surface === "billing")
            document
              .getElementById("squad-explore")
              ?.scrollIntoView({ behavior: "smooth", block: "start" });
          if (surface === "chat")
            document
              .getElementById("squad-chat")
              ?.scrollIntoView({ behavior: "smooth", block: "start" });
          if (surface === "agents" || surface === "templates")
            document
              .getElementById("workspace-agents")
              ?.scrollIntoView({ behavior: "smooth", block: "start" });
          if (surface === "explore") {
            setConsoleTab("connectors");
            document
              .getElementById("squad-explore")
              ?.scrollIntoView({ behavior: "smooth", block: "start" });
          }
          if (surface === "skills")
            document
              .getElementById("squad-explore")
              ?.scrollIntoView({ behavior: "smooth", block: "start" });
        }}
        onNewAgent={() => {
          setCreateAgentRequest((value) => value + 1);
          document
            .getElementById("workspace-agents")
            ?.scrollIntoView({ behavior: "smooth", block: "start" });
        }}
        onImport={() => {
          setImportDefinitionRequest((value) => value + 1);
          document
            .getElementById("workspace-agents")
            ?.scrollIntoView({ behavior: "smooth", block: "start" });
        }}
      />

      <Grid container spacing={3}>
        <Grid item xs={12} md={7} id="workspace-agents">
          <Card>
            <CardContent>
              <Typography variant="h5" sx={{ mb: 2 }}>
                Squad roster
              </Typography>
              {snapshot.loading && !snapshot.agents ? (
                <CircularProgress size={24} />
              ) : null}
              {!snapshot.loading && agents.length === 0 ? (
                <Typography color="text.secondary">
                  No managed agents are visible in this remote workspace.
                </Typography>
              ) : null}
              <Grid container spacing={2}>
                {agents.map((agent) => (
                  <Grid item xs={12} sm={6} key={agent.id}>
                    <AgentCard
                      agent={agent}
                      selected={agent.id === selectedAgent}
                      onSelect={setSelectedAgent}
                    />
                  </Grid>
                ))}
              </Grid>
              <Divider sx={{ my: 2 }} />
              <AgentControlPanel
                agent={selected}
                detail={agentDetail}
                capabilities={snapshot.fleet?.capabilities}
                environmentResult={snapshot.environments}
                detailLoading={agentDetailLoading}
                onCreate={createAgent}
                onUpdate={updateAgent}
                onControl={controlAgent}
                client={client}
                onChanged={async (id) => {
                  await Promise.all([load(), inspectAgent(id)]);
                }}
                createRequest={createAgentRequest}
                importRequest={importDefinitionRequest}
              />
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={5} id="squad-chat">
          <Card>
            <CardContent>
              <Typography variant="h5">Start a remote run</Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 0.75, mb: 2 }}
              >
                Runs are durable Omega conversations. Sandbox, evidence,
                approvals, memory, and billing stay on the remote deployment.
              </Typography>
              <TextField
                fullWidth
                select
                SelectProps={{ native: true }}
                label="Managed agent"
                value={selectedAgent}
                onChange={(event) => setSelectedAgent(event.target.value)}
                disabled={agents.length === 0}
                sx={{ mb: 2 }}
              >
                <option value="">Choose an agent</option>
                {agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </TextField>
              <TextField
                fullWidth
                select
                SelectProps={{ native: true }}
                label="Run mode"
                value={runMode}
                onChange={(event) => setRunMode(event.target.value)}
                sx={{ mb: 2 }}
              >
                <option value="agent">Managed agent</option>
                {societyEnabled ? (
                  <option value="society">Team mode</option>
                ) : null}
              </TextField>
              {runMode === "society" ? (
                <Alert severity="info" sx={{ mb: 2 }}>
                  Team mode is delegated by the remote agent loop. The run tree
                  and receipts remain authoritative on Omega.
                </Alert>
              ) : null}
              <TextField
                fullWidth
                multiline
                minRows={4}
                label="Task"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="Ask this squad member to do something…"
              />
              {runState.error ? (
                <Alert severity="error" sx={{ mt: 2 }}>
                  {runState.error}
                </Alert>
              ) : null}
              <Button
                fullWidth
                variant="contained"
                startIcon={<Iconify icon="solar:play-bold" />}
                sx={{ mt: 2 }}
                onClick={() => void startRun()}
                disabled={!selected || !input.trim() || runState.loading}
              >
                {runState.loading ? "Starting remotely…" : "Start remote run"}
              </Button>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} id="squad-explore">
          <Card>
            <CardContent>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                justifyContent="space-between"
                gap={1}
              >
                <Box>
                  <Typography variant="h5">Recent runs</Typography>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ mt: 0.5 }}
                  >
                    {selected
                      ? `Runs for ${selected.name}`
                      : "Select an agent to inspect its durable runs."}
                  </Typography>
                </Box>
                <Stack direction="row" alignItems="center" spacing={1}>
                  {selected ? (
                    <Button size="small" onClick={() => void loadRuns()}>
                      Refresh runs
                    </Button>
                  ) : null}
                  {selected ? (
                    <Chip
                      variant="outlined"
                      label={selected.status || "registered"}
                    />
                  ) : null}
                </Stack>
              </Stack>
              <Divider sx={{ my: 2 }} />
              {runsError ? <Alert severity="error">{runsError}</Alert> : null}
              {Array.isArray(runs) && runs.length === 0 ? (
                <Typography color="text.secondary">No runs yet.</Typography>
              ) : null}
              {Array.isArray(runs)
                ? runs
                    .slice(0, 10)
                    .map((run) => (
                      <RunRow
                        key={run.id || run.conversationId}
                        run={run}
                        busyId={controlAction.id}
                        onControl={(id, action) => void controlRun(id, action)}
                        onSelect={(id) => void inspectConversation(id)}
                      />
                    ))
                : null}
              <RunDetailPanel
                id={selectedRunId}
                detail={conversationDetail}
                loading={conversationDetailLoading}
                artifacts={runArtifacts}
                events={runEvents}
                streamStatus={runStreamStatus}
                busyId={controlAction.id}
                controlReceipt={runControlReceipt}
                followUp={followUp}
                followUpBusy={followUpBusy}
                onFollowUpChange={setFollowUp}
                onSendFollowUp={sendFollowUp}
                onArtifact={(kind) => void loadRunArtifact(kind)}
                onControl={(id, action) => void controlRun(id, action)}
                onResolveApproval={(id, decision) =>
                  void resolveRunApproval(id, decision)
                }
              />
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Stack
                direction={{ xs: "column", md: "row" }}
                justifyContent="space-between"
                alignItems={{ md: "center" }}
                gap={1}
              >
                <Box>
                  <Typography variant="h5">Cloud control plane</Typography>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ mt: 0.5 }}
                  >
                    Review and operate tenant-scoped Omega resources through the
                    remote deployment.
                  </Typography>
                </Box>
                <Chip size="small" variant="outlined" label="Remote only" />
              </Stack>
              <Tabs
                value={consoleTab}
                onChange={(_, value) => setConsoleTab(value)}
                variant="scrollable"
                scrollButtons="auto"
                sx={{ mt: 2, borderBottom: 1, borderColor: "divider" }}
              >
                <Tab value="agent-teams" label="Agent teams" />
                <Tab value="skills" label="Skills" />
                <Tab value="governance" label="Governance" />
                <Tab value="approvals" label="Approvals" />
                <Tab value="environments" label="Environments" />
                <Tab value="schedules" label="Schedules" />
                <Tab value="billing" label="Billing & usage" />
                <Tab value="webhooks" label="Webhooks" />
                <Tab value="audit" label="Audit & integrity" />
                {!connectorOperatorDenied ? (
                  <Tab value="connectors" label="Connectors" />
                ) : null}
                {!memoryOperatorDenied ? (
                  <Tab value="memory" label="Memory" />
                ) : null}
                {!sandboxOperatorDenied ? (
                  <Tab value="sandboxes" label="Sandboxes" />
                ) : null}
              </Tabs>
              {hiddenOperatorSurfaceCount > 0 ? (
                <Alert severity="info" sx={{ mt: 2 }}>
                  Some cloud administration surfaces require an Omega operator
                  session and are hidden for this tenant workspace.
                </Alert>
              ) : null}
              <Box sx={{ pt: 2 }}>
                {controlAction.error ? (
                  <Alert severity="error" sx={{ mb: 2 }}>
                    {controlAction.error}
                  </Alert>
                ) : null}
                {consoleTab === "agent-teams" ? (
                  <AgentTeamsPanel client={client} agents={agents} />
                ) : null}
                {consoleTab === "skills" ? (
                  <SkillLibraryPanel client={client} />
                ) : null}
                {consoleTab === "governance" ? (
                  <GovernancePanel client={client} />
                ) : null}
                {consoleTab === "approvals" ? (
                  <ApprovalPanel
                    result={snapshot.approvals}
                    busyId={controlAction.id}
                    onResolve={(id, decision) =>
                      void resolveApproval(id, decision)
                    }
                  />
                ) : null}
                {consoleTab === "environments" ? (
                  <EnvironmentPanel
                    result={snapshot.environments}
                    revisionsById={environmentRevisions}
                    revisionsLoadingId={environmentRevisionsLoadingId}
                    onInspectRevisions={inspectEnvironmentRevisions}
                    onControl={(id, action, expectedRevision) =>
                      void controlEnvironment(id, action, expectedRevision)
                    }
                    onCreate={createEnvironment}
                    onRevise={reviseEnvironment}
                    busyId={controlAction.id}
                  />
                ) : null}
                {consoleTab === "schedules" ? (
                  <SchedulePanel
                    result={snapshot.schedules}
                    agents={snapshot.agents}
                    client={client}
                    busyId={controlAction.id}
                    selectedId={selectedScheduleId}
                    detail={scheduleDetail}
                    detailLoading={scheduleDetailLoading}
                    onSelect={setSelectedScheduleId}
                    onRun={(id) => void runSchedule(id)}
                    onCreate={createSchedule}
                    onUpdate={updateSchedule}
                    onDelete={deleteSchedule}
                  />
                ) : null}
                {consoleTab === "billing" ? (
                  <BillingPanel
                    accountsResult={billingAccountsResult}
                    plansResult={billingPlansResult}
                    accountId={billingAccountId}
                    onSelectAccount={setBillingAccountId}
                    data={billingData}
                    period={billingPeriod}
                    loading={billingLoading}
                    seatQuote={billingSeatQuote}
                    onSeatQuote={previewBillingSeats}
                    mutation={billingMutation}
                    onCheckout={(id, planId) =>
                      void openBillingCheckout(id, planId)
                    }
                    onCancelSubscription={(id) =>
                      void cancelBillingSubscription(id)
                    }
                    onRefresh={() => void refreshBilling()}
                  />
                ) : null}
                {consoleTab === "webhooks" ? (
                  <WebhookPanel client={client} />
                ) : null}
                {consoleTab === "audit" ? (
                  typeof client?.getTenantAudit === "function" ? (
                    <WorkspaceAuditPanel client={client} scopeKey={scopeKey} />
                  ) : (
                    <AuditPanel
                      auditResult={snapshot.audit}
                      integrityResult={snapshot.auditIntegrity}
                    />
                  )
                ) : null}
                {consoleTab === "connectors" ? (
                  connectorOperatorDenied ? null : (
                    <IntegrationDiscoveryPanel
                      result={snapshot.connectors}
                      client={client}
                      onRefreshWorkforce={load}
                    />
                  )
                ) : null}
                {consoleTab === "memory" ? (
                  memoryOperatorDenied ? null : (
                    <MemoryPanel
                      client={client}
                      scopesResult={snapshot.memoryScopes}
                      graphResult={snapshot.memoryGraph}
                      timelineResult={snapshot.memoryTimeline}
                      eventsResult={snapshot.memoryEvents}
                      consolidationResult={snapshot.memoryConsolidation}
                      eventDetail={memoryEventDetail}
                      lineageResult={memoryLineage}
                      selectedEventId={selectedMemoryEventId}
                      eventLoading={memoryEventLoading}
                      exportValue={memoryExport}
                      onInspectEvent={(id) => void inspectMemoryEvent(id)}
                      onExport={() => void exportMemory()}
                    />
                  )
                ) : null}
                {consoleTab === "sandboxes" ? (
                  sandboxOperatorDenied ? null : (
                    <SandboxPanel
                      sandboxes={sandboxes}
                      result={
                        snapshot.sandboxResult ||
                        (snapshot.sandboxError
                          ? { available: false, reason: snapshot.sandboxError }
                          : { available: true })
                      }
                      selectedId={selectedSandboxId}
                      detail={sandboxDetail}
                      services={sandboxServices}
                      actionId={sandboxActionId}
                      onSelect={setSelectedSandboxId}
                      onControl={(id, action) =>
                        void controlSandbox(id, action)
                      }
                    />
                  )
                ) : null}
              </Box>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
}
