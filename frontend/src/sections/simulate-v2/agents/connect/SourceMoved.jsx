import PropTypes from "prop-types";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ClosableDialog from "../../components/ClosableDialog";
import { codeRepoOf } from "../agentProfile";
import { isGitAgent, codeRefOf, defaultBranchOf } from "../../_mock/codeRefs";

const MONO = "ui-monospace, Menlo, monospace";

/* The repository as owner/name, without the host. */
const shortRepo = (url) =>
  String(url || "")
    .replace(/^https?:\/\/[^/]+\//, "")
    .replace(/\.git$/, "");

/**
 * A connected repository or branch that no longer resolves (demo:
 * ?source=moved). The version we read stays usable; the pointer changes only
 * when someone confirms the new location.
 */
export default function SourceMoved({ agent, onSaveValues }) {
  const [params] = useSearchParams();
  const [open, setOpen] = useState(false);
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("");
  if (
    params.get("source") !== "moved" ||
    !isGitAgent(agent) ||
    agent.values?.sourcePointer
  )
    return null;
  const oldRepo = shortRepo(codeRepoOf(agent)) || agent.name;
  const oldBranch = defaultBranchOf(agent);
  const version =
    agent.versions.find((v) => v.label === agent.production) ||
    agent.versions[agent.versions.length - 1];
  const sha = version ? codeRefOf(agent, version)?.sha : null;
  const openDialog = () => {
    setRepo(oldRepo);
    setBranch(oldBranch);
    setOpen(true);
  };
  const save = () => {
    onSaveValues(
      {
        sourcePointer: {
          repo: repo.trim(),
          ref: branch.trim() || "main",
          from: `${oldRepo} @ ${oldBranch}`,
          at: new Date().toISOString(),
        },
      },
      "Pointed to the new location.",
    );
    setOpen(false);
  };
  return (
    <>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          mb: 2,
          px: 2,
          py: 1.25,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          bgcolor: "background.paper",
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {`Source moved — ${oldRepo} @ ${oldBranch} no longer resolves`}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {`${version?.label || "The version"} stays usable${sha ? ` at ${oldBranch} @ ${sha}` : ""}.`}
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          onClick={openDialog}
          sx={{ flexShrink: 0, color: "text.primary", borderColor: "divider" }}
        >
          Point to the new location
        </Button>
      </Stack>
      <ClosableDialog
        open={open}
        onClose={() => setOpen(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle sx={{ typography: "s1", fontWeight: 600 }}>
          Point to the new location
        </DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ pt: 0.5 }}>
            <TextField
              size="small"
              fullWidth
              label="Repository"
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
            />
            <TextField
              size="small"
              fullWidth
              label="Branch"
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button size="small" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!repo.trim()}
            onClick={save}
          >
            Point here
          </Button>
        </DialogActions>
      </ClosableDialog>
    </>
  );
}
SourceMoved.propTypes = {
  agent: PropTypes.object.isRequired,
  onSaveValues: PropTypes.func.isRequired,
};
