import PropTypes from "prop-types";
import {
  Box,
  Button,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { fDate } from "src/utils/format-time";
import useSourceParams from "./useSourceParams";
import SourceDrawer from "./SourceDrawer";
import { plural, purposeLabel } from "./sources.constants";

export default function SourcesTable({ envId, sources, generation }) {
  const { sourceId, sourceTab, openSource, closeSource } = useSourceParams();
  const selected = sources.find((row) => row.source_id === sourceId);
  if (!sources.length) return null;
  const showVisibility = sources.some((source) => source.visibility);
  const showUpdated = sources.some((source) => source.updated_at);
  const showUsage =
    sources.some((source) => Array.isArray(source.used_by)) ||
    (generation?.state === "completed" &&
      sources.some(
        (source) =>
          source.review_id && source.review_id === generation.review_id,
      ));
  const columns = [
    "Source",
    "Type",
    "Used as",
    showVisibility && "Who reads it",
    "State",
    showUpdated && "Updated",
    showUsage && "Used by",
  ].filter(Boolean);
  return (
    <>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small" aria-label="Environment sources">
          <TableHead>
            <TableRow>
              {columns.map((label) => (
                <TableCell key={label}>{label}</TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {sources.map((row) => (
              <TableRow key={row.source_id}>
                <TableCell>
                  <Button
                    color="inherit"
                    onClick={() => openSource(row.source_id)}
                  >
                    {row.filename || "Source file"}
                  </Button>
                </TableCell>
                <TableCell>
                  {row.filename?.includes(".")
                    ? row.filename.split(".").pop().toUpperCase()
                    : "—"}
                </TableCell>
                <TableCell>{purposeLabel(row.purpose)}</TableCell>
                {showVisibility && (
                  <TableCell>
                    {{
                      agent_visible: "Tested agent",
                      evaluator_only: "Evaluator only",
                    }[row.visibility] || "—"}
                  </TableCell>
                )}
                <TableCell>
                  {row.unsaved == null ? "—" : row.unsaved ? "Draft" : "Saved"}
                  {row.rejected_count > 0 &&
                    " · " + row.rejected_count + " rejected"}
                </TableCell>
                {showUpdated && (
                  <TableCell>
                    {row.updated_at ? fDate(row.updated_at) : "—"}
                  </TableCell>
                )}
                {showUsage && (
                  <TableCell>
                    {Array.isArray(row.used_by)
                      ? plural(row.used_by.length, "scenario")
                      : row.review_id && row.review_id === generation?.review_id
                        ? plural(
                            (generation.generated_scenarios || []).filter(
                              (scenario) =>
                                scenario.citations?.some(
                                  (c) => c.source_id === row.source_id,
                                ),
                            ).length,
                            "scenario",
                          )
                        : "—"}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      {showVisibility && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", px: 2, py: 1 }}
        >
          In a run · Tested agent:{" "}
          {plural(
            sources.filter((source) => source.visibility === "agent_visible")
              .length,
            "file",
          )}{" "}
          · Evaluator only:{" "}
          {plural(
            sources.filter((source) => source.visibility === "evaluator_only")
              .length,
            "file",
          )}
        </Typography>
      )}
      {selected && (
        <SourceDrawer
          key={sourceId}
          envId={envId}
          source={selected}
          initialTab={sourceTab}
          onClose={closeSource}
        />
      )}
    </>
  );
}
SourcesTable.propTypes = {
  envId: PropTypes.string.isRequired,
  sources: PropTypes.array.isRequired,
  generation: PropTypes.object,
};
