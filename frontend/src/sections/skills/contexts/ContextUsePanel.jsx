import { cloneElement, isValidElement, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, FormControlLabel, Link, Stack, Switch, Tab, TextField, Typography } from '@mui/material';
import Section from 'src/components/section';
import StatusBadge from 'src/components/status-badge';
import CodeBlock from 'src/components/code-block';
import Iconify from 'src/components/iconify';
import { SegmentedTabs } from 'src/components/tabs/tabs';
import { ReviewState } from '../review/ReleaseReviewBody';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';

const quote = (value) => `'${String(value).replaceAll("'", "'\"'\"'")}'`;
const safeValue = (value) => typeof value === 'string' && value.length > 0 && [...value].every((char) => char.codePointAt(0) >= 32 && char.codePointAt(0) !== 127);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const applyCommand = "python -m fi.context apply --plan 'context-plan.json' --quiescent";
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
    '# requires FI_CONTEXT_CREDENTIAL',
    `python -m fi.context plan --name ${quote(snapshot.name)} --version ${snapshot.version} --client ${quote(profile.id)} --project ${quote(project)}${preview ? ' --allow-preview' : ''} --out ${quote('context-plan.json')}`,
    `python -m fi.context inspect --plan ${quote('context-plan.json')} --diff`,
  ].join('\n');
}

function UseContent({ snapshot, origin, originError, organizationId, workspaceId, qualification, workspaceName, onViewFiles, onOpenLinks, runtimeAccess, productionVersion, viewingVersion, useViewed, onUseViewed }) {
  const [profileId, setProfileId] = useState('claude-code');
  const [hasKey, setHasKey] = useState(false);
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
  const copyCommand = async (text) => {
    setCopyBusy(true); setCopyError(''); setCopied(null);
    try { if (!navigator.clipboard?.writeText) throw new Error('Unavailable'); await navigator.clipboard.writeText(text); setCopied(text); }
    catch { setCopyError('Could not copy. Copy the command manually; include Environment variables when copying the plan.'); }
    finally { setCopyBusy(false); }
  };
  const environment = command.split('\n').filter((line) => /^export FI_CONTEXT_(ORGANIZATION|WORKSPACE)_ID=/.test(line)).join('\n');
  const visibleCommand = command.split('\n').filter((line) => !/^export FI_CONTEXT_(ORGANIZATION|WORKSPACE)_ID=/.test(line)).join('\n');
  return <Stack spacing={3} sx={{ minWidth: 0 }}>
    {productionVersion != null && viewingVersion !== productionVersion && <Stack spacing={1}><Typography variant="s1">You’re viewing v{viewingVersion}. {useViewed ? `Installing this non-production version does not change production v${productionVersion}.` : `Install defaults to production v${productionVersion}.`}</Typography><FormControlLabel control={<Switch checked={Boolean(useViewed)} onChange={(event) => onUseViewed?.(event.target.checked)} />} label={`Install viewed v${viewingVersion} instead`} /></Stack>}
    {productionVersion == null && <Typography variant="s3" color="text.secondary">No production version is assigned. This plan installs the viewed version only.</Typography>}
    <Section title={`Install ${snapshot.name} v${snapshot.version}`}>
      <Stack spacing={2} sx={{ minWidth: 0 }}>
        <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}>
          {onViewFiles && <Link component="button" variant="s3" onClick={onViewFiles}>View published files</Link>}
          {onViewFiles && onOpenLinks && <Typography variant="s3" color="text.secondary">·</Typography>}
          {onOpenLinks && <Link component="button" variant="s3" onClick={onOpenLinks}>Open linked skills</Link>}
        </Stack>
        {preview && <Stack direction="row" spacing={1} alignItems="center"><StatusBadge status="unknown" label="Preview" /><Typography variant="s3" color="text.secondary">Host setup not yet verified for {profileId === 'claude-code' ? 'Claude Code' : 'Codex'}</Typography></Stack>}
        <Typography variant="s3" color="text.secondary">{preview ? 'Preview installer' : 'Installer'} — writes AGENTS.md and {profileId === 'codex' ? '.agents/skills/' : '.claude/skills/'} only after you review the plan.</Typography>
        <Stack spacing={1}>
          <Typography variant="m3" component="h3">1. Choose your client</Typography>
          <SegmentedTabs value={profileId} onChange={(_, value) => { setProfileId(value); setCopyError(''); }} aria-label="Choose your client"><Tab value="codex" label="Codex" /><Tab value="claude-code" label="Claude Code" /></SegmentedTabs>
          <TextField size="small" label="Project directory" value={project} onChange={(event) => { setProject(event.target.value); setCopyError(''); }} helperText="Existing local instructions are never silently replaced." />
        </Stack>
        <Stack spacing={1}>
          <Typography variant="m3" component="h3">2. Set a read-only key</Typography>
          <Typography variant="s3" color="text.secondary">Use a read-only key for this version. Set FI_CONTEXT_CREDENTIAL securely in your terminal; never paste it into the command.</Typography>
          <Box id="context-runtime-access" sx={{ minWidth: 0 }}>{isValidElement(runtimeAccess) ? cloneElement(runtimeAccess, { onAvailabilityChange: setHasKey }) : runtimeAccess}</Box>
        </Stack>
        <Stack direction="row" spacing={1} alignItems="center"><Typography variant="m3" component="h3">3. Plan and inspect</Typography>{!hasKey && <StatusBadge status="unknown" label="Needs a key" />}</Stack>
        {problem ? <Alert severity="warning">{problem}</Alert> : <>
          <Typography variant="s3" color="text.secondary">API: {new URL(origin).host}</Typography>
          <CodeBlock label="Context install plan command" code={visibleCommand} copyLabel="Copy" ariaLabel="Copy plan command" action={<Button aria-label="Copy plan command" variant="outlined" size="small" startIcon={<Iconify icon="solar:copy-linear" width={16} />} disabled={copyBusy} onClick={() => copyCommand(command)}>Copy</Button>} />
          <TechnicalDetails title="Environment variables"><Typography variant="s3" color="text.secondary">Included when you copy the plan command.</Typography><Box component="pre" sx={{ m: 0, typography: 's1', fontFamily: 'monospace', overflowX: 'auto', maxWidth: 1 }}>{environment}</Box></TechnicalDetails>
          {copied === command && <Typography variant="s3" role="status">Copied the complete plan command.</Typography>}
          {copyError && <Alert severity="error">{copyError}</Alert>}
          <Stack spacing={1}>
            <Typography variant="m3" component="h3">4. Apply after you review the plan</Typography>
            <Typography variant="s3" color="text.secondary">Inspect every file change and confirm v{snapshot.version} and the full digest in Installation requirements. Stop the coding client before applying. This page does not run commands.</Typography>
            <CodeBlock label="Context apply command" code={applyCommand} copyLabel="Copy" ariaLabel="Copy apply command" action={<Button aria-label="Copy apply command" variant="outlined" size="small" startIcon={<Iconify icon="solar:copy-linear" width={16} />} disabled={copyBusy} onClick={() => copyCommand(applyCommand)}>Copy</Button>} />
            {copied === applyCommand && <Typography variant="s3" role="status">Copied the apply command.</Typography>}
          </Stack>
        </>}

      </Stack>
    </Section>
    <TechnicalDetails title="Installation requirements">
      <Stack spacing={1}>
        {preview && <Typography variant="s3" color="text.secondary">Requires an environment with the Context SDK CLI installed (python -m fi.context). This plan opts in to a preview host profile. No released package or verified model delivery is claimed.</Typography>}
        {profileId === 'claude-code' && <Typography variant="s3" color="text.secondary">Existing CLAUDE.md or local instructions may affect AGENTS.md discovery. Settings, imports and permissions are not changed.</Typography>}
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
UseContent.propTypes = { snapshot: PropTypes.object.isRequired, origin: PropTypes.string, originError: PropTypes.string, organizationId: PropTypes.string.isRequired, workspaceId: PropTypes.string.isRequired, qualification: PropTypes.object, workspaceName: PropTypes.string, onViewFiles: PropTypes.func, onOpenLinks: PropTypes.func, runtimeAccess: PropTypes.node, productionVersion: PropTypes.number, viewingVersion: PropTypes.number, useViewed: PropTypes.bool, onUseViewed: PropTypes.func };
export default function ContextUsePanel({ scopeKey, state = 'unavailable', snapshot, onRetry, ...props }) {
  if (!scopeKey || state !== 'ready' || !snapshot) return <ReviewState state={!scopeKey ? 'forbidden' : state} onRetry={onRetry} />;
  return <UseContent key={`${scopeKey}:${snapshot.versionId}`} {...props} snapshot={snapshot} />;
}
ContextUsePanel.propTypes = { scopeKey: PropTypes.string, state: PropTypes.string, snapshot: PropTypes.object, onRetry: PropTypes.func };
