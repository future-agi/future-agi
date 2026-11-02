import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { BTN_SX, INPUT_SX, T12, T13 } from "../../drawerStyles";
import { HintLine, Label, MONO } from "../formBits";
import RepoAgentScan from "./RepoAgentScan";
import TextLink from "../shared/TextLink";
import {
  SAMPLE_BRANCHES,
  SAMPLE_GITHUB_ACCOUNTS,
  SAMPLE_GITHUB_REPOS,
} from "./sampleGithub";

export const GITHUB_PICK_INITIAL = {
  installed: false,
  repo: "",
  branch: SAMPLE_BRANCHES[0],
  agentId: null,
  entryFile: "",
};

const accountsLabel = (names) =>
  names.length > 1
    ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`
    : names[0] || "";

const SELECT_SX = {
  ...INPUT_SX,
  ...T12,
  "& .MuiSelect-select": { py: 0.75, px: 1, minHeight: "unset", ...T12 },
};

/**
 * Connect GitHub, on sample data: install the app, pick a repository, then
 * the agent its scan finds. `value` is the pick ({ installed, repo, branch,
 * agentId, entryFile }); `onChange(patch)` updates it.
 */
export default function GithubPicker({
  value,
  onChange,
  onScanStatus,
  onUseToken,
}) {
  const { installed, repo, branch, agentId, entryFile } = value;
  const [changingBranch, setChangingBranch] = useState(false);

  if (!installed) {
    return (
      <Box>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            p: 1.75,
            borderRadius: "8px",
            border: "1px dashed",
            borderColor: "divider",
          }}
        >
          <Iconify
            icon="mdi:github"
            width={28}
            sx={{ color: "text.primary", flexShrink: 0 }}
          />
          <Box flex={1} minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Typography sx={{ ...T13, fontWeight: 600 }}>
                Install the GitHub App to pick the agent
              </Typography>
              <UiOnlyFlag />
            </Stack>
            <Typography
              component="div"
              sx={{ ...T12, color: "text.secondary" }}
            >
              <HintLine
                text="Read only. We find the agents in your repos."
                hint="We scan the repositories you choose and find the agents in them. We ask for write access at your first draft pull request."
              />
            </Typography>
          </Box>
          <Button
            variant="contained"
            color="primary"
            onClick={() => onChange({ installed: true })}
            startIcon={<Iconify icon="mdi:github" width={14} />}
            sx={{ ...BTN_SX, flexShrink: 0 }}
          >
            Install GitHub App
          </Button>
        </Stack>
        <TextLink onClick={onUseToken} sx={{ mt: 0.75 }}>
          Can&apos;t install the app? Paste the repository link and use an
          access token
        </TextLink>
      </Box>
    );
  }

  return (
    <Stack spacing={1.25}>
      <Box>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ mb: 0.5 }}
        >
          <Typography sx={{ ...T12, fontWeight: 600 }}>Repository</Typography>
          <Typography
            sx={{ ...T12, color: "text.secondary" }}
          >{`· from GitHub (${accountsLabel(SAMPLE_GITHUB_ACCOUNTS)})`}</Typography>
          <UiOnlyFlag />
          <Box flex={1} />
          <TextLink
            onClick={() =>
              enqueueSnackbar(
                "UI only: adding a repository on GitHub isn't wired to the API yet.",
                { variant: "info" },
              )
            }
          >
            Missing a repository or an account? Add it on GitHub
          </TextLink>
        </Stack>
        <Select
          size="small"
          fullWidth
          displayEmpty
          value={repo}
          onChange={(e) => {
            setChangingBranch(false);
            onChange({
              repo: e.target.value,
              branch: SAMPLE_BRANCHES[0],
              agentId: null,
              entryFile: "",
            });
          }}
          SelectDisplayProps={{ "aria-label": "Repository" }}
          renderValue={(v) =>
            v ? (
              <Box component="span" sx={{ fontFamily: MONO }}>
                {v}
              </Box>
            ) : (
              <Box component="span" sx={{ color: "text.disabled" }}>
                Choose a repository
              </Box>
            )
          }
          sx={SELECT_SX}
        >
          {SAMPLE_GITHUB_REPOS.map((r) => (
            <MenuItem key={r.name} value={r.name} sx={{ ...T12, gap: 1 }}>
              <Box component="span" sx={{ fontFamily: MONO, flex: 1 }}>
                {r.name}
              </Box>
              <Box component="span" sx={{ color: "text.secondary" }}>
                {r.language}
              </Box>
            </MenuItem>
          ))}
        </Select>
      </Box>

      {repo &&
        (changingBranch ? (
          <Box sx={{ maxWidth: 320 }}>
            <Label>Branch</Label>
            <Select
              size="small"
              fullWidth
              value={branch}
              onChange={(e) => {
                setChangingBranch(false);
                onChange({ branch: e.target.value, agentId: null });
              }}
              SelectDisplayProps={{ "aria-label": "Branch" }}
              renderValue={(v) => (
                <Box component="span" sx={{ fontFamily: MONO }}>
                  {v}
                </Box>
              )}
              sx={{ ...SELECT_SX, mt: 0.5 }}
            >
              {SAMPLE_BRANCHES.map((b) => (
                <MenuItem key={b} value={b} sx={{ ...T12, gap: 1 }}>
                  <Box component="span" sx={{ fontFamily: MONO, flex: 1 }}>
                    {b}
                  </Box>
                  {b === SAMPLE_BRANCHES[0] && (
                    <Box component="span" sx={{ color: "text.secondary" }}>
                      default
                    </Box>
                  )}
                </MenuItem>
              ))}
            </Select>
          </Box>
        ) : (
          <Typography component="div" sx={{ ...T12, color: "text.secondary" }}>
            {"Reads "}
            <Box
              component="span"
              sx={{ fontFamily: MONO, color: "text.primary" }}
            >
              {branch}
            </Box>
            {". A fix merged there counts as released. "}
            <TextLink onClick={() => setChangingBranch(true)}>Change</TextLink>
          </Typography>
        ))}

      {repo && (
        <RepoAgentScan
          key={repo}
          repoName={repo}
          branch={branch}
          agentId={agentId}
          onPick={(a) => onChange({ agentId: a.id, entryFile: "" })}
          entryFile={entryFile}
          onEntryFile={(v) => onChange({ entryFile: v, agentId: null })}
          onStatus={onScanStatus}
        />
      )}
    </Stack>
  );
}
GithubPicker.propTypes = {
  value: PropTypes.shape({
    installed: PropTypes.bool,
    repo: PropTypes.string,
    branch: PropTypes.string,
    agentId: PropTypes.string,
    entryFile: PropTypes.string,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
  onScanStatus: PropTypes.func.isRequired,
  onUseToken: PropTypes.func.isRequired,
};
