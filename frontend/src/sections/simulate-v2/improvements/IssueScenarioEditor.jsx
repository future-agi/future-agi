import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ClosableDialog from "../components/ClosableDialog";

const REPEATS_WORD = "three times";

/*
  The scenarios an epoch built from its issue, edited in place: rename one,
  or leave it out, then reproduce again on the same epoch. Opened from a
  not-reproduced ending.
*/
const REPRO_WORD = {
  reproduced: "Reproduced",
  inconclusive: "Inconclusive",
  passed: "Not reproduced",
};
export default function IssueScenarioEditor({
  open,
  drafts,
  perScenario,
  onClose,
  onReproduce,
}) {
  const [edits, setEdits] = useState({});
  useEffect(() => {
    if (open) setEdits({});
  }, [open]);
  const set = (id, patchIn) =>
    setEdits((e) => ({ ...e, [id]: { ...e[id], ...patchIn } }));
  const included = drafts.filter((d) => edits[d.id]?.include !== false).length;
  return (
    <ClosableDialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      fullWidth
      PaperProps={{
        sx: { maxWidth: 720, borderRadius: "8px", backgroundImage: "none" },
      }}
    >
      <Box
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          Edit this epoch&apos;s scenarios
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Built from the issue&apos;s traces. They stay with this epoch — the
          environment&apos;s own scenarios don&apos;t change.
        </Typography>
      </Box>
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
        sx={{ maxHeight: "56vh", overflowY: "auto" }}
      >
        {drafts.map((d) => {
          const on = edits[d.id]?.include !== false;
          const state = perScenario?.[d.id]?.state;
          return (
            <Stack
              key={d.id}
              direction="row"
              alignItems="center"
              spacing={1.25}
              sx={{ px: 2, py: 1 }}
            >
              <Checkbox
                size="small"
                checked={on}
                onChange={(e) => set(d.id, { include: e.target.checked })}
                inputProps={{ "aria-label": `Include ${d.title}` }}
                sx={{ p: 0.5 }}
              />
              <TextField
                size="small"
                fullWidth
                disabled={!on}
                value={edits[d.id]?.title ?? d.title ?? ""}
                onChange={(e) => set(d.id, { title: e.target.value })}
                inputProps={{ "aria-label": "Scenario title" }}
                sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
              />
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  width: 104,
                  flexShrink: 0,
                  textAlign: "right",
                }}
              >
                {REPRO_WORD[state] || "—"}
              </Typography>
            </Stack>
          );
        })}
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 1.75,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {`${included} of ${drafts.length} included · each runs ${REPEATS_WORD} on the agent as it runs today`}
        </Typography>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!included}
          onClick={() => onReproduce(edits)}
          startIcon={<Iconify icon="solar:play-bold" width={14} />}
        >
          Reproduce again
        </Button>
      </Stack>
    </ClosableDialog>
  );
}
IssueScenarioEditor.propTypes = {
  open: PropTypes.bool,
  drafts: PropTypes.array,
  perScenario: PropTypes.object,
  onClose: PropTypes.func,
  onReproduce: PropTypes.func,
};
