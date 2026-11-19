import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Autocomplete,
  Button,
  Link,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";

import SideDrawer from "../../components/SideDrawer";
import {
  extendHarnessJob,
  harnessIdempotencyKey,
  sendHarnessConversationMessage,
} from "src/api/harness/harness";
import {
  uploadEnvironmentFile,
  useActors,
} from "src/api/simulate-environments/versions";
import { harnessScenariosListKey } from "src/api/simulate-environments/scenariosHooks";
import { paths } from "src/routes/paths";
import {
  ADD_COPY,
  ADD_ROUTES,
  describeMessage,
  groundingSuffix,
  MAX_COUNT,
  POLICY_TYPES,
  RECORD_TYPES,
  UPLOAD_MAX_BYTES,
  clampCount,
  fileMessage,
  recordsMessage,
  requestedLines,
} from "./addScenarios.constants";
import { ENV_SHAPE } from "./scenarios.shapes";

// `payload` carries what the platform reads back, such as the requested cases, never parsed from the text.
const message = (content, payload = {}) => ({
  content,
  client_request_id: harnessIdempotencyKey(),
  kind: "user_message",
  payload,
});

// Add scenarios: five routes, each ending with the builder writing and proving drafts.
// FileReader rather than Blob.text(), which some embedded browsers and jsdom lack.
const readText = (file) =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });

export default function AddScenariosDrawer({
  open,
  onClose,
  env,
  initialGuidance = "",
  initialCount,
}) {
  const [route, setRoute] = useState("generate");
  const [count, setCount] = useState(initialCount ?? 5);
  const [guidance, setGuidance] = useState(initialGuidance);
  const [description, setDescription] = useState("");
  const [file, setFile] = useState(null);
  const [state, setState] = useState("idle");
  const [error, setError] = useState(null);
  const queryClient = useQueryClient();
  const jobId = env?.id;
  // What the person may ground new cases in: the cast, the environment's rules and its files.
  const cast = useActors(jobId, { enabled: Boolean(open && jobId) });
  const actorNames = (cast.data?.actors ?? [])
    .map((one) => one?.name || one?.id)
    .filter(Boolean);
  const ruleOptions = env?.rules ?? [];
  const fileOptions = cast.data?.knowledge_sources?.files ?? [];
  const [actors, setActors] = useState([]);
  const [grounding, setGrounding] = useState([]);
  const suffix = () =>
    groundingSuffix(
      actors,
      grounding.filter((one) => one.kind === "rule").map((one) => one.value),
      grounding.filter((one) => one.kind === "file").map((one) => one.value),
    );

  const close = () => {
    setState("idle");
    setError(null);
    setFile(null);
    onClose();
  };

  const upload = async () => {
    if (file.size > UPLOAD_MAX_BYTES) throw new Error(ADD_COPY.tooLarge);
    await uploadEnvironmentFile(jobId, { file, kind: "knowledge" });
    return { path: `knowledge/${file.name}`, text: await readText(file) };
  };

  const submit = async () => {
    setState("submitting");
    setError(null);
    try {
      const n = clampCount(count);
      if (route === "generate") {
        const asked = `${guidance.trim()}${suffix()}`.trim();
        await extendHarnessJob(jobId, {
          count: n,
          ...(asked ? { guidance: asked } : {}),
        });
      } else if (route === "describe") {
        await sendHarnessConversationMessage(
          jobId,
          message(`${describeMessage(description)}${suffix()}`, {
            requested_cases: requestedLines(description),
          }),
        );
      } else if (route === "file" || route === "records") {
        const { path, text } = await upload();
        const content =
          route === "file"
            ? fileMessage(n, path, text)
            : recordsMessage(n, path, text);
        await sendHarnessConversationMessage(jobId, message(`${content}${suffix()}`));
      }
      queryClient.invalidateQueries({
        queryKey: harnessScenariosListKey(jobId),
      });
      setState("done");
      close();
    } catch (failure) {
      setState("error");
      setError(
        failure?.response?.data?.message ||
          failure?.message ||
          "Something went wrong.",
      );
    }
  };

  const ready =
    route === "generate" ||
    (route === "describe" && description.trim()) ||
    ((route === "file" || route === "records") && file);

  return (
    <SideDrawer open={open} onClose={close} width={720}>
      <Stack
        spacing={2.5}
        sx={{ p: 3, height: "100%" }}
        data-testid="add-scenarios-drawer"
      >
        <Stack spacing={0.5}>
          <Typography sx={{ typography: "m1", fontWeight: "fontWeightMedium" }}>
            {ADD_COPY.title}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {ADD_COPY.subtitle}
          </Typography>
        </Stack>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={route}
          onChange={(_, value) => value && setRoute(value)}
          sx={{ flexWrap: "wrap" }}
        >
          {ADD_ROUTES.map((one) => (
            <ToggleButton key={one} value={one}>
              {ADD_COPY.routes[one].label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          {ADD_COPY.routes[route].blurb}
        </Typography>

        {route !== "describe" && route !== "elsewhere" && (
          <TextField
            size="small"
            type="number"
            label={ADD_COPY.count}
            value={count}
            inputProps={{ min: 1, max: MAX_COUNT }}
            onChange={(e) => setCount(e.target.value)}
            sx={{ maxWidth: 160 }}
          />
        )}
        {route === "generate" && (
          <TextField
            size="small"
            multiline
            minRows={2}
            label={ADD_COPY.guidance}
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
          />
        )}
        {route === "describe" && (
          <TextField
            multiline
            minRows={4}
            label={ADD_COPY.describe}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        )}
        {["generate", "describe", "file"].includes(route) && (
          <>
            <Autocomplete
              multiple
              size="small"
              options={actorNames}
              value={actors}
              onChange={(_, value) => setActors(value)}
              renderInput={(params) => <TextField {...params} label={ADD_COPY.actors} />}
              data-testid="add-actors"
            />
            <Autocomplete
              multiple
              size="small"
              options={[
                ...ruleOptions.map((value) => ({ kind: "rule", value })),
                ...fileOptions.map((value) => ({ kind: "file", value })),
              ]}
              value={grounding}
              isOptionEqualToValue={(a, b) => a.kind === b.kind && a.value === b.value}
              getOptionLabel={(one) =>
                one.kind === "rule" ? ADD_COPY.ruleOption(one.value) : ADD_COPY.fileOption(one.value)
              }
              onChange={(_, value) => setGrounding(value)}
              renderInput={(params) => <TextField {...params} label={ADD_COPY.groundIn} />}
              data-testid="add-ground-in"
            />
          </>
        )}
        {(route === "file" || route === "records") && (
          <Stack spacing={0.5}>
            <Button
              component="label"
              variant="outlined"
              color="inherit"
              size="small"
              sx={{ alignSelf: "flex-start" }}
            >
              {file ? file.name : ADD_COPY.file}
              <input
                hidden
                type="file"
                accept={route === "file" ? POLICY_TYPES : RECORD_TYPES}
                data-testid="add-scenarios-file"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </Button>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {route === "file" ? ADD_COPY.fileHint : ADD_COPY.recordsHint}
            </Typography>
          </Stack>
        )}
        {route === "elsewhere" && (
          <Stack spacing={1}>
            <Link
              component="button"
              type="button"
              onClick={close}
              sx={{ alignSelf: "flex-start" }}
            >
              {ADD_COPY.transcriptLink}
            </Link>
            <Link
              href={paths.dashboard.observe}
              sx={{ alignSelf: "flex-start" }}
            >
              {ADD_COPY.productionLink}
            </Link>
          </Stack>
        )}

        {state === "submitting" && (
          <Alert severity="info">{ADD_COPY.writing}</Alert>
        )}
        {state === "error" && error && <Alert severity="error">{error}</Alert>}

        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{ mt: "auto" }}
        >
          <Button size="small" color="inherit" onClick={close}>
            {ADD_COPY.cancel}
          </Button>
          {route !== "elsewhere" && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={!ready || state === "submitting"}
              onClick={submit}
            >
              {ADD_COPY.submit}
            </Button>
          )}
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

AddScenariosDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  env: ENV_SHAPE,
  initialGuidance: PropTypes.string,
  initialCount: PropTypes.number,
};
