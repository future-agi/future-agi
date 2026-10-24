import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { ActionText, KvRow, PanelCard } from "./AgentBits";
import {
  FAGI_PROMPT_LIBRARY,
  GIT_HOSTS,
  codeRepoOf,
  fagiPromptsOf,
  fagiPromptsPatch,
  gitHostOf,
  identityOf,
} from "./agentProfile";
import { MONO } from "./agentViewUtils";
import { segmentedToggleSx } from "./segmentedToggleSx";

/* The product's segmented control (Evals: Single / Composite). */
const toggleSx = segmentedToggleSx;

/* One field, read or edited in place. */
function InlineEdit({
  value,
  empty,
  placeholder,
  multiline,
  maxLength,
  onSave,
  hint,
}) {
  const [draft, setDraft] = useState(null);
  if (draft != null) {
    return (
      <Stack spacing={0.75}>
        <TextField
          size="small"
          fullWidth
          autoFocus
          multiline={multiline}
          minRows={multiline ? 2 : undefined}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value.slice(0, maxLength || 200))}
          InputProps={{ sx: { typography: "s2" } }}
        />
        <Stack direction="row" spacing={1} alignItems="center">
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => {
              onSave(draft.trim());
              setDraft(null);
            }}
          >
            Save
          </Button>
          <Button
            size="small"
            onClick={() => setDraft(null)}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          {maxLength && (
            <Typography
              sx={{
                typography: "s3",
                color: "text.disabled",
                ml: "auto !important",
                fontVariantNumeric: "tabular-nums",
              }}
            >{`${draft.length}/${maxLength}`}</Typography>
          )}
        </Stack>
      </Stack>
    );
  }
  return (
    <Box>
      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
        <Typography
          sx={{
            typography: "s2",
            flex: 1,
            minWidth: 0,
            color: value ? "text.primary" : "text.subtitle",
          }}
        >
          {value || empty}
        </Typography>
        <ActionText onClick={() => setDraft(value || "")}>
          {value ? "Edit" : "Add"}
        </ActionText>
      </Stack>
      {hint && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {hint}
        </Typography>
      )}
    </Box>
  );
}
InlineEdit.propTypes = {
  value: PropTypes.string,
  empty: PropTypes.string,
  placeholder: PropTypes.string,
  multiline: PropTypes.bool,
  maxLength: PropTypes.number,
  onSave: PropTypes.func.isRequired,
  hint: PropTypes.node,
};

/**
 * Who the agent is: an owner, the languages it speaks and one line on what
 * it does — shown in the agent header and on Connected agents. Languages are
 * set under Reach, with the channels they apply to.
 */
export function IdentityCard({ agent, onSave, bare = false }) {
  const id = identityOf(agent);
  return (
    <PanelCard
      title="Identity"
      note="Shown in the header and on Connected agents"
      bare={bare}
    >
      <KvRow label="Owner" first>
        <InlineEdit
          value={id.ownerSet ? id.owner : ""}
          empty={`${id.owner} · who connected it`}
          placeholder="Who looks after this agent"
          maxLength={60}
          onSave={(owner) =>
            onSave(
              { owner: owner || null },
              owner
                ? `${owner} owns ${agent.name} now.`
                : "Owner cleared — it reads as whoever connected it.",
            )
          }
        />
      </KvRow>
      <KvRow label="Description">
        <InlineEdit
          value={id.description}
          empty="No description"
          placeholder="What it does, in one line — e.g. Answers order and refund calls for the US store"
          multiline
          maxLength={140}
          onSave={(description) =>
            onSave({ description: description || null }, "Description saved.")
          }
        />
      </KvRow>
    </PanelCard>
  );
}
IdentityCard.propTypes = {
  agent: PropTypes.object.isRequired,
  onSave: PropTypes.func.isRequired,
  bare: PropTypes.bool,
};

/**
 * Source — what an agent version is pinned to beyond its own definition:
 * the Future AGI prompts it loads (each at its version) and the git host
 * improvements open pull requests on. A version pins its commit, each prompt
 * version, the platform config and the tools together.
 */
export function SourceCard({ agent, source, onSave }) {
  const prompts = fagiPromptsOf(agent);
  const addable = FAGI_PROMPT_LIBRARY.filter(
    (p) => !prompts.some((x) => x.promptId === p.promptId),
  );
  const host = gitHostOf(agent);
  const repo = codeRepoOf(agent);
  return (
    <PanelCard title="Source" note="Pinned together on every version">
      <KvRow label="Read from" first>
        <Typography
          noWrap
          sx={{
            typography: "s2",
            fontFamily:
              source.where && source.kind !== "platform" ? MONO : undefined,
          }}
        >
          {source.kind === "endpoint"
            ? "Nothing to read — an endpoint"
            : `${source.label}${source.where && source.kind !== "platform" ? ` · ${source.where}` : ""}`}
        </Typography>
      </KvRow>
      {/* An agent not read from code can still have its code in a repository —
          linking it is what lets code fixes and coding agents work on it. */}
      {!["repo", "upload", "framework"].includes(source.kind) && (
        <KvRow
          label="Linked repository"
          hint="The code behind its tools or prompts. Code fixes open pull requests here, and coding agents push to it."
        >
          <InlineEdit
            value={agent.values?.linkedRepo || agent.values?.repoUrl || ""}
            empty="None — needed for code fixes and coding agents"
            placeholder="https://github.com/your-org/your-agent"
            maxLength={200}
            onSave={(url) =>
              onSave(
                { linkedRepo: url || null },
                url
                  ? `Repository linked — code fixes and coding agents can work on ${agent.name}.`
                  : "Repository unlinked.",
              )
            }
          />
        </KvRow>
      )}
      <KvRow
        label="Prompts it loads"
        hint={
          prompts.length
            ? "Each one's version is pinned on every agent version saved while it's loaded, next to the commit, platform config and tools."
            : "None — its prompts live in its own source. Add one it fetches from Prompts at run time."
        }
      >
        <Stack spacing={0.5}>
          {prompts.map((p) => (
            <Stack
              key={p.promptId}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ minWidth: 0 }}
            >
              <Iconify
                icon="solar:document-text-linear"
                width={14}
                sx={{ color: "text.subtitle", flexShrink: 0 }}
              />
              <Typography
                noWrap
                sx={{ typography: "s2_1", fontWeight: 500, minWidth: 0 }}
              >
                {p.name}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  fontFamily: MONO,
                  color: "text.secondary",
                  flexShrink: 0,
                }}
              >
                {p.version}
              </Typography>
              <Box flex={1} />
              <ActionText
                onClick={() =>
                  onSave(
                    fagiPromptsPatch(agent, { remove: p.promptId }),
                    `${p.name} no longer loaded — versions saved from now on don't pin it.`,
                  )
                }
              >
                Remove
              </ActionText>
            </Stack>
          ))}
          {addable.length > 0 && (
            <Select
              size="small"
              value=""
              displayEmpty
              onChange={(e) => {
                const p = FAGI_PROMPT_LIBRARY.find(
                  (x) => x.promptId === e.target.value,
                );
                if (p)
                  onSave(
                    fagiPromptsPatch(agent, { add: p }),
                    `${p.name} ${p.version} added — the next version pins it.`,
                  );
              }}
              renderValue={() => "Add a Future AGI prompt"}
              sx={{
                height: 26,
                width: "fit-content",
                typography: "s3",
                color: "text.secondary",
                "& .MuiSelect-select": { typography: "s3", py: 0, pl: 1 },
              }}
            >
              {addable.map((p) => (
                <MenuItem
                  key={p.promptId}
                  value={p.promptId}
                  sx={{ typography: "s2", gap: 1 }}
                >
                  {p.name}
                  <Box
                    component="span"
                    sx={{ fontFamily: MONO, color: "text.subtitle" }}
                  >
                    {p.version}
                  </Box>
                </MenuItem>
              ))}
            </Select>
          )}
        </Stack>
      </KvRow>
      {/* Where draft pull requests open — only once there's a repository to open them on. */}
      {repo && (
        <KvRow
          label="Git host"
          hint={`${
            repo
              ? `Improvements open draft pull requests on ${repo.replace(/^https?:\/\//, "")}.`
              : "Where improvements open draft pull requests once the agent's code or config is in a repository."
          }`}
        >
          <ToggleButtonGroup
            size="small"
            exclusive
            value={host.id}
            onChange={(_, v) =>
              v &&
              v !== host.id &&
              onSave(
                { gitHost: v },
                `Git host set to ${GIT_HOSTS.find((h) => h.id === v)?.label}.`,
              )
            }
            sx={toggleSx}
          >
            {GIT_HOSTS.map((h) => (
              <ToggleButton key={h.id} value={h.id} disabled={h.later}>
                <Iconify icon={h.icon} width={14} />
                {h.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </KvRow>
      )}
    </PanelCard>
  );
}
SourceCard.propTypes = {
  agent: PropTypes.object.isRequired,
  source: PropTypes.object.isRequired,
  onSave: PropTypes.func.isRequired,
};
