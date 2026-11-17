import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import JurySeatEditor from "./JurySeatEditor";
import JuryDatasetReport from "./JuryDatasetReport";

const basePath = (id) =>
  `/model-hub/eval-templates/${encodeURIComponent(id)}/composite/`;
const detailError = (error) => {
  const body = error?.response?.data;
  return typeof body?.detail === "string"
    ? body.detail
    : "The jury could not be saved or run. Check the seat settings and mapped inputs, then try again.";
};

export default function JuryPanel({
  templateId,
  childrenList,
  editable = false,
  disabled = false,
}) {
  const [seats, setSeats] = useState([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [runId, setRunId] = useState("");
  const [mapping, setMapping] = useState('{"output": ""}');
  const definition = useQuery({
    queryKey: ["composite-jury-definition", templateId],
    queryFn: async () =>
      (await axios.get(`${basePath(templateId)}jury/config/`)).data.result,
  });
  const report = useQuery({
    queryKey: ["composite-jury-report", templateId, runId],
    queryFn: async () =>
      (
        await axios.get(`${basePath(templateId)}jury/`, {
          params: runId ? { run_id: runId } : {},
        })
      ).data.result,
  });
  useEffect(() => {
    if (definition.data && !dirty) setSeats(definition.data.seats);
  }, [definition.data, dirty]);
  const update = (next) => {
    setSeats(next);
    setDirty(true);
    setMessage("");
  };
  const add = (kind) => {
    const id = `${kind}-${crypto.randomUUID().slice(0, 8)}`;
    update([
      ...seats,
      kind === "check"
        ? { id, kind, check_key: "", weight: 1 }
        : {
            id,
            kind,
            child_id: childrenList[0].child_id,
            level: "row",
            weight: 1,
          },
    ]);
  };
  const save = async () => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const { data } = await axios.put(`${basePath(templateId)}jury/config/`, {
        seats,
      });
      setSeats(data.result.seats);
      await definition.refetch();
      setDirty(false);
      setMessage("Jury saved. Future evaluations use these seats.");
    } catch (failure) {
      setError(detailError(failure));
    } finally {
      setBusy(false);
    }
  };
  const run = async () => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const values = JSON.parse(mapping);
      if (!values || Array.isArray(values) || typeof values !== "object")
        throw new Error("Use a JSON object for mapped inputs.");
      const { data } = await axios.post(`${basePath(templateId)}execute/`, {
        mapping: values,
      });
      if (!data.result.evaluation_id)
        throw new Error(
          "The execution result was not saved. Try again before relying on this report.",
        );
      setRunId(data.result.evaluation_id);
      await report.refetch();
    } catch (failure) {
      setError(failure?.response ? detailError(failure) : failure.message);
    } finally {
      setBusy(false);
    }
  };
  const blocked =
    disabled || busy || definition.isLoading || definition.isError;
  return (
    <Stack spacing={2} component="section" aria-label="Composite jury">
      <Typography variant="h6">Composite jury</Typography>
      <Typography variant="body2" color="text.secondary">
        Give the same evaluation several judges, evaluate individual steps, or
        include stored checks. A missing verdict never counts as a pass.
      </Typography>
      {(error || definition.isError || report.isError) && (
        <Alert severity="error">
          {error || detailError(definition.error || report.error)}
        </Alert>
      )}
      {message && <Alert severity="success">{message}</Alert>}
      {definition.isLoading && (
        <Typography role="status">Loading jury…</Typography>
      )}
      {!definition.isLoading && !seats.length && (
        <Typography color="text.secondary">
          No jury configured. Add a judge or check to get started.
        </Typography>
      )}
      {seats.map((seat, index) => (
        <JurySeatEditor
          key={seat.id}
          seat={seat}
          childrenList={childrenList}
          disabled={blocked || !editable}
          onChange={(next) =>
            update(seats.map((item, at) => (at === index ? next : item)))
          }
          onRemove={() => update(seats.filter((_, at) => at !== index))}
        />
      ))}
      {editable && (
        <Stack direction="row" gap={1} flexWrap="wrap">
          <Button
            onClick={() => add("eval")}
            disabled={blocked || !childrenList.length || seats.length >= 16}
          >
            Add judge
          </Button>
          <Button
            onClick={() => add("check")}
            disabled={blocked || seats.length >= 16}
          >
            Add check
          </Button>
          <Button
            variant="contained"
            onClick={save}
            disabled={blocked || !dirty || !seats.length}
          >
            Save jury
          </Button>
        </Stack>
      )}
      <Typography variant="subtitle1">Try the saved jury</Typography>
      <TextField
        label="Mapped inputs (JSON)"
        multiline
        minRows={3}
        value={mapping}
        disabled={blocked}
        onChange={(event) => setMapping(event.target.value)}
        helperText={
          'Map child variables here. Steps: [{"kind":"llm","mapping":{"output":"…"}}]. Checks: [{"key":"safe","passed":true}]. Up to 32 steps and 64 judge calls.'
        }
      />
      <Button
        sx={{ alignSelf: "flex-start" }}
        variant="outlined"
        onClick={run}
        disabled={blocked || dirty || !definition.data?.seats.length}
      >
        Run saved jury
      </Button>
      <Typography variant="subtitle1">Saved jury report</Typography>
      {!report.data?.runs.length ? (
        <Typography color="text.secondary">
          No saved jury evaluations yet.
        </Typography>
      ) : report.data.kind === "dataset" ? (
        <JuryDatasetReport
          report={report.data}
          runId={runId}
          onRunChange={setRunId}
        />
      ) : (
        <>
          <TextField
            label="Jury run"
            select
            size="small"
            value={runId || report.data.run_id || ""}
            onChange={(event) => setRunId(event.target.value)}
          >
            {report.data.runs.map((item) => (
              <MenuItem key={item.run_id} value={item.run_id}>
                {item.run_label}
              </MenuItem>
            ))}
          </TextField>
          <Typography role="status">
            {!report.data.complete
              ? "Incomplete jury: one or more seats have no verdict."
              : report.data.disagreement
                ? "Judges disagree."
                : "All seats agree."}
          </Typography>
          <Table size="small" aria-label="Jury verdicts">
            <TableHead>
              <TableRow>
                <TableCell>Seat</TableCell>
                <TableCell>Evaluation</TableCell>
                <TableCell>Score</TableCell>
                <TableCell>Verdict</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {report.data.seats.map((seat) => (
                <TableRow key={seat.id}>
                  <TableCell>{seat.id}</TableCell>
                  <TableCell>{seat.child_name}</TableCell>
                  <TableCell>{seat.score ?? "Not scored"}</TableCell>
                  <TableCell>
                    {seat.passed === null
                      ? "Unavailable"
                      : seat.passed
                        ? "Pass"
                        : "Fail"}
                    {seat.error ? `: ${seat.error}` : ""}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </Stack>
  );
}
JuryPanel.propTypes = {
  templateId: PropTypes.string.isRequired,
  childrenList: PropTypes.array.isRequired,
  editable: PropTypes.bool,
  disabled: PropTypes.bool,
};
