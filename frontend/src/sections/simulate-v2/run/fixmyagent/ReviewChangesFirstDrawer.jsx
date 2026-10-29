import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { neutralCheckboxSx } from "../../components/primitives";
import {
  editedCandidateOf,
  editableTextOf,
  riskOfCandidate,
} from "../../_mock/candidateReview";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * Review the changes before they run (roadmap improve_next.md "Review
 * candidates"): each change the search would try — what it edits, why it
 * might fix the failure, and what it could break. Keep, edit or drop each;
 * the search then tries only what's kept, within the same limits. A
 * suggestion here isn't a tested improvement.
 */
export default function ReviewChangesFirstDrawer({
  open,
  onClose,
  candidates,
  maxTrials,
  onRun,
}) {
  const [kept, setKept] = useState({});
  const [edits, setEdits] = useState({});
  const [editing, setEditing] = useState(null);

  useEffect(() => {
    if (!open) return;
    setKept(Object.fromEntries(candidates.map((c) => [c.id, true])));
    setEdits({});
    setEditing(null);
  }, [open, candidates]);

  if (!open) return <SideDrawer open={false} onClose={onClose} />;
  const keptList = candidates.filter((c) => kept[c.id]);

  return (
    <SideDrawer open onClose={onClose} width={960}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              Review the changes before they run
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`${candidates.length} change${candidates.length === 1 ? "" : "s"} written from the failing cases. None is tested yet. Keep, edit or drop each; it then tries up to ${maxTrials} trials made from the ones you keep.`}
            </Typography>
          </Box>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, py: 2 }}>
          {!candidates.length ? (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              No change to review: nothing it may change addresses the cases to
              fix. Go back and allow another part, or choose other cases.
            </Typography>
          ) : (
            <Stack spacing={1.5}>
              {candidates.map((c) => {
                const on = !!kept[c.id];
                const shown =
                  edits[c.id] != null ? editedCandidateOf(c, edits[c.id]) : c;
                return (
                  <Box
                    key={c.id}
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: "8px",
                      opacity: on ? 1 : 0.55,
                    }}
                  >
                    <Stack
                      direction="row"
                      spacing={1}
                      alignItems="flex-start"
                      sx={{ p: 1.5 }}
                    >
                      <Checkbox
                        size="small"
                        checked={on}
                        onChange={() => setKept((k) => ({ ...k, [c.id]: !on }))}
                        sx={{ ...neutralCheckboxSx, p: 0.25 }}
                      />
                      <Box flex={1} minWidth={0}>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Typography
                            sx={{ typography: "s2", fontWeight: 600 }}
                          >
                            {shown.title}
                          </Typography>
                          <Typography
                            sx={{ typography: "s3", color: "text.tertiary" }}
                          >
                            {`· ${c.kind}${c.path ? ` · ${c.path}` : ""}${edits[c.id] != null ? " · edited" : ""}`}
                          </Typography>
                        </Stack>
                        <Typography
                          sx={{
                            typography: "s3",
                            color: "text.secondary",
                            mt: 0.25,
                          }}
                        >
                          {`Why: ${c.why}`}
                        </Typography>
                        <Typography
                          sx={{ typography: "s3", color: "text.secondary" }}
                        >
                          {`Aims at ${c.addresses.length} case${c.addresses.length === 1 ? "" : "s"}. Could break: ${riskOfCandidate(c)}`}
                        </Typography>
                        <Box
                          component="pre"
                          sx={{
                            m: 0,
                            mt: 1,
                            p: 1,
                            borderRadius: "6px",
                            bgcolor: "background.neutral",
                            fontFamily: MONO,
                            fontSize: 12,
                            lineHeight: "18px",
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-word",
                          }}
                        >
                          {(shown.diff || [])
                            .map(
                              (d) =>
                                `${d.type === "add" ? "+" : d.type === "remove" ? "−" : " "} ${d.text}`,
                            )
                            .join("\n")}
                        </Box>
                        {editing === c.id && (
                          <TextField
                            multiline
                            fullWidth
                            minRows={2}
                            autoFocus
                            label={
                              c.kind === "Skill"
                                ? "When it's used (description)"
                                : "The line it adds"
                            }
                            value={edits[c.id] ?? editableTextOf(c)}
                            onChange={(e) =>
                              setEdits((x) => ({
                                ...x,
                                [c.id]: e.target.value,
                              }))
                            }
                            sx={{ mt: 1 }}
                            inputProps={{ style: { fontSize: 13 } }}
                          />
                        )}
                      </Box>
                      {editableTextOf(c) != null && on && (
                        <Button
                          size="small"
                          color="inherit"
                          onClick={() =>
                            setEditing(editing === c.id ? null : c.id)
                          }
                          sx={{ minWidth: 0 }}
                        >
                          {editing === c.id ? "Done" : "Edit"}
                        </Button>
                      )}
                    </Stack>
                  </Box>
                );
              })}
            </Stack>
          )}
        </Box>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
          >
            {keptList.length
              ? `${keptList.length} kept${candidates.length - keptList.length ? ` · ${candidates.length - keptList.length} dropped` : ""}. The same cases, limits and protected checks apply.`
              : "Keep at least one change to run."}
          </Typography>
          <Button size="small" color="inherit" onClick={onClose}>
            Back to the plan
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!keptList.length}
            onClick={() =>
              onRun(
                keptList.map((c) =>
                  edits[c.id] != null ? editedCandidateOf(c, edits[c.id]) : c,
                ),
              )
            }
            startIcon={<Iconify icon="solar:play-bold" width={16} />}
          >
            {`Run with ${keptList.length} change${keptList.length === 1 ? "" : "s"}`}
          </Button>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

ReviewChangesFirstDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  candidates: PropTypes.array.isRequired,
  maxTrials: PropTypes.number,
  onRun: PropTypes.func.isRequired,
};
