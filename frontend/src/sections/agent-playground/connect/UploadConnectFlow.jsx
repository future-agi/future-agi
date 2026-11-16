import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import {
  useAgentScan,
  useConnectAgent,
  useStartScan,
} from "src/api/agents/agentConnect";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { CONNECTED_AGENTS_PATH } from "../agentsTabs";
import { AgentDetails, FoundAgents } from "./ConnectAgentSteps";
import { TextLink } from "./connectBits";
import {
  clearConnectDraft,
  draftHasProgress,
  readConnectDraft,
  saveConnectDraft,
} from "./connectDraft";
import { UPLOAD_SOURCE } from "./connectSources";
import { MONO } from "./connectStyles";
import FolderUploadField from "./FolderUploadField";

const WHOLE_UPLOAD = "Connect the uploaded folder anyway";

const defaultName = (picked, folder) => folder?.name || picked?.name || "";

/**
 * Connect an agent from a folder: upload it, find the agents in it, pick one,
 * give it a name and its values, connect. The agent page then shows it being
 * read. A reload comes back to the same step; the upload is never sent twice.
 */
export default function UploadConnectFlow() {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const { role } = useAuthContext();
  const canConnect =
    !!RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role];
  const [draft] = useState(() => readConnectDraft(UPLOAD_SOURCE) || {});
  const [folder, setFolder] = useState(draft.folder || null);
  const [uploading, setUploading] = useState(false);
  const [stage, setStage] = useState(
    draft.scanId ? draft.stage || "source" : "source",
  );
  const [scanId, setScanId] = useState(draft.scanId || null);
  const [picked, setPicked] = useState(draft.picked || null);
  const [manual, setManual] = useState(!!draft.manual);
  const [name, setName] = useState(draft.name || "");
  const [values, setValues] = useState({});
  const startScan = useStartScan();
  const scan = useAgentScan(scanId);
  const connect = useConnectAgent();

  useEffect(() => {
    const next = { stage, folder, scanId, picked, manual, name };
    if (draftHasProgress(next)) {
      saveConnectDraft(next, UPLOAD_SOURCE);
    } else {
      clearConnectDraft(UPLOAD_SOURCE);
    }
  }, [stage, folder, scanId, picked, manual, name]);

  const source = folder?.sourceId
    ? { kind: "archive", archive_artifact_id: folder.sourceId }
    : null;

  const uploaded = (next) => {
    setFolder(next);
    setScanId(null);
    setPicked(null);
    setManual(false);
  };

  const findAgents = () =>
    startScan.mutate(source, {
      onSuccess: (data) => {
        setScanId(data.id);
        setPicked(null);
        setManual(false);
        setStage("agents");
      },
      onError: (e) => enqueueSnackbar(errorMessage(e), { variant: "error" }),
    });

  const toDetails = () => {
    setName(
      (current) => current || defaultName(manual ? null : picked, folder),
    );
    setStage("details");
  };

  const envNames = manual ? [] : picked?.envNames || [];

  const connectAgent = () =>
    connect.mutate(
      {
        ...source,
        name: name.trim(),
        env_values: Object.fromEntries(
          Object.entries(values).filter(([, value]) => value),
        ),
      },
      {
        onSuccess: (data) => {
          clearConnectDraft(UPLOAD_SOURCE);
          enqueueSnackbar(
            data.exists
              ? "This agent is already connected"
              : `${name.trim()} connected; we're reading it now`,
            { variant: data.exists ? "info" : "success" },
          );
          navigate(`${CONNECTED_AGENTS_PATH}/${data.agent_id}/overview`);
        },
        onError: (e) => enqueueSnackbar(errorMessage(e), { variant: "error" }),
      },
    );

  const folderLine = (
    <Typography
      component="div"
      sx={{ typography: "s3", color: "text.secondary" }}
    >
      <Box component="span" sx={{ fontFamily: MONO, color: "text.primary" }}>
        {folder?.name}
      </Box>
      {stage === "details"
        ? " · the whole folder"
        : ` · ${folder?.files} file${folder?.files === 1 ? "" : "s"}`}
      {" · "}
      <TextLink
        onClick={() => setStage(stage === "details" ? "agents" : "source")}
      >
        Change
      </TextLink>
    </Typography>
  );

  let body;
  let hint;
  let action;
  if (stage === "source") {
    body = (
      <>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          Pick the folder with the agent&apos;s code, not a whole repository. We
          upload it once and read it as one agent.
        </Typography>
        <FolderUploadField
          folder={folder}
          onUploaded={uploaded}
          onUploading={setUploading}
          disabled={!canConnect}
          testId="connect-upload-dropzone"
        />
      </>
    );
    hint = uploading
      ? "Uploading the folder"
      : source
        ? `We read ${folder.name} and list the agents in it.`
        : "Choose a folder";
    action = (
      <Button
        variant="contained"
        color="primary"
        disabled={!source || uploading || !canConnect || startScan.isPending}
        onClick={findAgents}
        endIcon={<Iconify icon="solar:magnifer-linear" width={14} />}
        sx={{ flexShrink: 0 }}
      >
        Find agents
      </Button>
    );
  } else if (stage === "agents") {
    body = (
      <>
        {folderLine}
        {manual ? (
          <Stack spacing={1} data-testid="connect-whole-upload">
            <Typography sx={{ typography: "s1" }}>
              {`We connect ${folder?.name || "the upload"} as one agent and read it from the top of the folder.`}
            </Typography>
            <TextLink onClick={() => setManual(false)}>
              Back to the agents we found
            </TextLink>
          </Stack>
        ) : (
          <FoundAgents
            scan={scan.data}
            picked={picked}
            onPick={setPicked}
            onManual={() => {
              setPicked(null);
              setManual(true);
            }}
            manualLabel={WHOLE_UPLOAD}
            failedManualLabel={WHOLE_UPLOAD}
          />
        )}
      </>
    );
    const ready = manual || !!picked;
    hint = ready
      ? "Next: name it and add its values"
      : "Pick the agent to connect";
    action = (
      <Button
        variant="contained"
        color="primary"
        disabled={!ready}
        onClick={toDetails}
        endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
        sx={{ flexShrink: 0 }}
      >
        Next
      </Button>
    );
  } else {
    body = (
      <>
        {folderLine}
        <AgentDetails
          name={name}
          onName={setName}
          envNames={envNames}
          values={values}
          onValue={(envName, value) =>
            setValues((prev) => ({ ...prev, [envName]: value }))
          }
        />
      </>
    );
    hint = name.trim()
      ? "We read the agent once it's connected"
      : "Name the agent";
    action = (
      <Button
        variant="contained"
        color="primary"
        disabled={!name.trim() || !canConnect || connect.isPending}
        onClick={connectAgent}
        endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
        sx={{ flexShrink: 0 }}
      >
        Connect agent
      </Button>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: "100%" }}>
      <Stack spacing={1.75} sx={{ p: 2.5, flex: 1 }}>
        {body}
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          px: 2.5,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
          position: "sticky",
          bottom: 0,
          bgcolor: "background.paper",
        }}
      >
        {stage !== "source" && (
          <Button
            color="inherit"
            onClick={() => setStage(stage === "details" ? "agents" : "source")}
            startIcon={
              <Iconify icon="solar:alt-arrow-left-linear" width={14} />
            }
            sx={{ flexShrink: 0, color: "text.secondary" }}
          >
            Back
          </Button>
        )}
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {hint}
        </Typography>
        {action}
      </Stack>
    </Box>
  );
}
