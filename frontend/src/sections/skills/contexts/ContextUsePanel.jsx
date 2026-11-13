import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, MenuItem, Stack, TextField, Typography } from '@mui/material';
import Section from 'src/components/section';
import StatusBadge from 'src/components/status-badge';
import { ReviewState } from '../review/ReleaseReviewBody';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';

const quote = (value) => `'${String(value).replaceAll("'", "'\"'\"'")}'`;
const safeValue = (value) => typeof value === 'string' && value.length > 0 && [...value].every((char) => char.codePointAt(0) >= 32 && char.codePointAt(0) !== 127);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const loopback = (host) => ['localhost', '127.0.0.1'].includes(host);

// Preview is explicit, not a qualification claim. Never install an invented package
// version, adopt existing files, or combine plan/inspect with an automatic apply.
export function buildContextPlan({ snapshot, origin, organizationId, workspaceId, profile, packageVersion, project, preview = false }) {
  const url = new URL(origin);
  const local = url.protocol === 'http:' && loopback(url.hostname) && typeof window !== 'undefined' && loopback(window.location.hostname);
  if ((url.protocol !== 'https:' && !local) || url.username || url.password || url.search || url.hash || !uuid.test(organizationId) || !uuid.test(workspaceId)) throw new Error('Use an HTTPS origin and explicit workspace identity.');
  if (!safeValue(snapshot.name) || !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(snapshot.name) || snapshot.kind !== 'agent_context' || !Number.isSafeInteger(snapshot.version) || snapshot.version < 1 || !uuid.test(snapshot.versionId) || !/^sha256:[a-f0-9]{64}$/.test(snapshot.digest) || !safeValue(project) || project.startsWith('-')) throw new Error('Choose a complete immutable context and a project path.');
  if (!['codex', 'claude-code'].includes(profile?.id) || (!preview && (profile.state !== 'verified' || !profile.version || !/^\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)$/.test(packageVersion)))) throw new Error('This package and host profile are not qualified.');
  return [
    ...(!preview ? [`python -m pip install ${quote(`futureagi==${packageVersion}`)}`] : []),
    `export FI_CONTEXT_ORIGIN=${quote(url.origin)}`,
    `export FI_CONTEXT_ORGANIZATION_ID=${quote(organizationId)}`,
    `export FI_CONTEXT_WORKSPACE_ID=${quote(workspaceId)}`,
    ': "${FI_CONTEXT_CREDENTIAL:?Set your isolated consume credential securely; do not paste it into this command}"',
    `python -m fi.context plan --name ${quote(snapshot.name)} --version ${snapshot.version} --client ${quote(profile.id)} --project ${quote(project)}${preview ? ' --allow-preview' : ''} --out ${quote('context-plan.json')}`,
    `python -m fi.context inspect --plan ${quote('context-plan.json')} --diff`,
  ].join('\n');
}

function UseContent({ snapshot, origin, originError, organizationId, workspaceId, qualification, workspaceName, onViewFiles, onOpenLinks }) {
  const [profileId, setProfileId] = useState('codex');
  const [project, setProject] = useState('.');
  const [copied, setCopied] = useState(null);
  const [copyError, setCopyError] = useState('');
  const [copyBusy, setCopyBusy] = useState(false);
  const profile = qualification?.profiles?.find((item) => item.id === profileId);
  const preview = qualification?.released !== true || profile?.state !== 'verified';
  let command = '';
  let problem = '';
  if (originError || !origin) problem = 'This workspace has no public HTTPS API address configured. Ask an admin to set VITE_PUBLIC_API_ORIGIN.';
  else {
    try { command = buildContextPlan({ snapshot, origin, organizationId, workspaceId, profile: profile || { id: profileId }, packageVersion: qualification?.packageVersion, project, preview }); }
    catch (error) { problem = error.message; }
  }
  return <Stack spacing={3} sx={{ minWidth: 0 }}>
    <Section title={`Install ${snapshot.name} v${snapshot.version}`}>
      <Stack spacing={1.5}>
        <Typography variant="s1">Create a plan, inspect the files it will change, then apply it to your project. Production and existing contexts stay unchanged.</Typography>
        {preview && <StatusBadge status="unavailable" label="Preview: host profile not yet verified" />}
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5}>
          <TextField select size="small" label="Project profile" value={profileId} onChange={(event) => { setProfileId(event.target.value); setCopyError(''); }} sx={{ minWidth: 160 }}>
            <MenuItem value="codex">Codex</MenuItem><MenuItem value="claude-code">Claude Code</MenuItem>
          </TextField>
          <TextField size="small" label="Project directory" value={project} onChange={(event) => { setProject(event.target.value); setCopyError(''); }} sx={{ flex: 1 }} helperText="Project scope only. Existing local instructions are never silently replaced." />
        </Stack>
        <Typography variant="s3" color="text.secondary">Instructions: project AGENTS.md · Skills: {profileId === 'codex' ? '.agents/skills/' : '.claude/skills/'}</Typography>
        {profileId === 'claude-code' && <Typography variant="s3" color="text.secondary">Existing CLAUDE.md or local instructions may affect AGENTS.md discovery. Settings, imports and permissions are not changed.</Typography>}
        <Typography variant="s1">Use a scoped consume credential for this version. Create one in Runtime access below, or choose an existing credential that grants this version, then set FI_CONTEXT_CREDENTIAL securely in your terminal.</Typography>
        {preview && <Typography variant="s3" color="text.secondary">Requires an environment with the Context SDK CLI installed (python -m fi.context). The plan explicitly opts in to the preview host profile; no released package or delivery verification is claimed.</Typography>}
        {problem ? <Alert severity="warning">{problem}</Alert> : <>
          <Typography variant="s3" color="text.secondary">API: {new URL(origin).host}</Typography>
          <Box component="pre" tabIndex={0} aria-label="Context install plan command" sx={{ m: 0, p: 1.5, bgcolor: 'background.paper', border: 1, borderColor: 'divider', borderRadius: 1, typography: 's1', fontFamily: 'monospace', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{command}</Box>
          <Button variant="contained" sx={{ alignSelf: 'flex-start' }} disabled={copyBusy} onClick={async () => {
            setCopyBusy(true); setCopyError(''); setCopied(null);
            try { if (!navigator.clipboard?.writeText) throw new Error('Unavailable'); await navigator.clipboard.writeText(command); setCopied(command); }
            catch { setCopyError('Could not copy. Select and copy the full command manually.'); }
            finally { setCopyBusy(false); }
          }}>Copy plan command</Button>
          {copied === command && <Typography variant="s3" role="status">Copied the complete plan command.</Typography>}
          {copyError && <Alert severity="error">{copyError}</Alert>}
          <TechnicalDetails title="Apply only after reviewing the plan">
            <Typography variant="s1">Inspect every file change and confirm v{snapshot.version} and the full digest in Installation requirements. Stop the coding client before applying. This page does not run commands.</Typography>
            <Box component="pre" sx={{ typography: 's1', fontFamily: 'monospace', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{"python -m fi.context apply --plan 'context-plan.json' --quiescent"}</Box>
          </TechnicalDetails>
        </>}
        <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}>
          {onViewFiles && <Button onClick={onViewFiles}>View published files</Button>}
          {onOpenLinks && <Button onClick={onOpenLinks}>Open linked skills</Button>}
        </Stack>
      </Stack>
    </Section>
    <TechnicalDetails title="Installation requirements">
      <Stack spacing={1}>
        {workspaceName && <Typography variant="s1">Workspace: {workspaceName}</Typography>}
        <IdentityChip value={workspaceId} kind="id" label="Workspace" />
        <IdentityChip value={organizationId} kind="id" label="Organization" />
        <IdentityChip value={snapshot.versionId} kind="id" label="Pinned candidate" />
        <IdentityChip value={snapshot.digest} label="Digest" />
        <Typography variant="s3">Pinned version. A later release requires a new reviewed plan. Installed files are not proof of model delivery.</Typography>
      </Stack>
    </TechnicalDetails>
  </Stack>;
}
UseContent.propTypes = { snapshot: PropTypes.object.isRequired, origin: PropTypes.string, originError: PropTypes.string, organizationId: PropTypes.string.isRequired, workspaceId: PropTypes.string.isRequired, qualification: PropTypes.object, workspaceName: PropTypes.string, onViewFiles: PropTypes.func, onOpenLinks: PropTypes.func };
export default function ContextUsePanel({ scopeKey, state = 'unavailable', snapshot, onRetry, ...props }) {
  if (!scopeKey || state !== 'ready' || !snapshot) return <ReviewState state={!scopeKey ? 'forbidden' : state} onRetry={onRetry} />;
  return <UseContent key={`${scopeKey}:${snapshot.versionId}`} {...props} snapshot={snapshot} />;
}
ContextUsePanel.propTypes = { scopeKey: PropTypes.string, state: PropTypes.string, snapshot: PropTypes.object, onRetry: PropTypes.func };
