import { cloneElement, isValidElement, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, FormControlLabel, Link, Stack, Switch, Tab, TextField, Typography } from '@mui/material';
import Section from 'src/components/section';
import StatusBadge from 'src/components/status-badge';
import CodeBlock from 'src/components/code-block';
import Iconify from 'src/components/iconify';
import { SegmentedTabs } from 'src/components/tabs/tabs';
import { ReviewState } from '../review/ReleaseReviewBody';
import ActionReason, { reasonProps } from '../components/ActionReason';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';
import { useSkillsAccess } from '../utils/access';
import { buildSkillPlan, checkProfile, cliApplyCommand, cliPlanLines, installerPreview, registryScope, safeText } from '../utils/install';

const NAME_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Preview is explicit, not a qualification claim. Never install an invented package
// version, adopt existing files, or combine plan/inspect with an automatic apply.
// The shared pieces (scope check, profile check, command lines) live in utils/install.js
// so that a skill and a context produce the same steps.
export function buildContextPlan({ snapshot, origin, organizationId, workspaceId, profile, packageVersion, project, preview = false }) {
  const url = registryScope({ origin, organizationId, workspaceId });
  if (!safeText(snapshot.name) || !NAME_PATTERN.test(snapshot.name) || snapshot.kind !== 'agent_context' || !Number.isSafeInteger(snapshot.version) || snapshot.version < 1 || !uuid.test(snapshot.versionId) || !/^sha256:[a-f0-9]{64}$/.test(snapshot.digest) || !safeText(project) || project.startsWith('-')) throw new Error('Choose a complete immutable context and a project path.');
  checkProfile({ profile, packageVersion, preview });
  return cliPlanLines({ kind: 'agent_context', name: snapshot.name, selectorFlags: `--version ${snapshot.version}`, client: profile.id, project, preview, url, organizationId, workspaceId, packageVersion }).join('\n');
}

// The one install flow for a context and a skill: choose your tool, create a read-only key,
// preview the changes, then install. `snapshot.kind` selects the command; the steps, headings and
// copy rules are the same. A skill passes `snapshot.label` (or a `version`) as its selector.
// `afterInstall` and `footer` are slots for the skill's no-Python fallback and for Check tracing.
export function InstallSteps({ snapshot, origin, originError, organizationId, workspaceId, qualification, workspaceName, onViewFiles, onOpenLinks, runtimeAccess, productionVersion, viewingVersion, useViewed, onUseViewed, title, afterInstall, footer }) {
  const [profileId, setProfileId] = useState('claude-code');
  const [hasKey, setHasKey] = useState(false);
  const [project, setProject] = useState('.');
  const [copied, setCopied] = useState(null);
  const [copyError, setCopyError] = useState('');
  const [copyBusy, setCopyBusy] = useState(false);
  const { isViewer } = useSkillsAccess();
  const isSkill = snapshot.kind === 'skill';
  const noun = isSkill ? 'skill' : 'context';
  const Noun = isSkill ? 'Skill' : 'Context';
  const toolName = profileId === 'claude-code' ? 'Claude Code' : 'Codex';
  const profile = qualification?.profiles?.find((item) => item.id === profileId);
  const preview = installerPreview({ qualification, profile });
  let command = '';
  let problem = '';
  if (originError || !origin) problem = 'This workspace has no public HTTPS API address configured. Ask an admin to set VITE_PUBLIC_API_ORIGIN.';
  else {
    try {
      const common = { origin, organizationId, workspaceId, profile: profile || { id: profileId }, packageVersion: qualification?.packageVersion, project, preview };
      command = isSkill
        ? buildSkillPlan({ ...common, name: snapshot.name, label: snapshot.label, version: snapshot.label ? undefined : snapshot.version })
        : buildContextPlan({ ...common, snapshot });
    } catch (error) { problem = error.message; }
  }
  const copyCommand = async (text) => {
    setCopyBusy(true); setCopyError(''); setCopied(null);
    try { if (!navigator.clipboard?.writeText) throw new Error('Unavailable'); await navigator.clipboard.writeText(text); setCopied(text); }
    catch { setCopyError('Could not copy. Select the command and copy it by hand; add the environment variables from Before you install when you copy the plan.'); }
    finally { setCopyBusy(false); }
  };
  const environment = command.split('\n').filter((line) => /^export FI_CONTEXT_(ORGANIZATION|WORKSPACE)_ID=/.test(line)).join('\n');
  const visibleCommand = command.split('\n').filter((line) => !/^export FI_CONTEXT_(ORGANIZATION|WORKSPACE)_ID=/.test(line)).join('\n');
  // Only someone who can create keys is told to create one; a member without that permission sees the admin guidance instead.
  const canCreateKey = !isViewer && !(isValidElement(runtimeAccess) && runtimeAccess.props.canIssue === false);
  const versionText = snapshot.version != null ? ` v${snapshot.version}` : '';
  const adminRequest = isSkill && snapshot.label
    ? `Please create a read-only key for the ${snapshot.label} label of ${snapshot.name} in Future AGI.`
    : `Please create a read-only key for ${snapshot.name} v${snapshot.version} in Future AGI.`;
  // Viewers can read the commands but cannot create the key they need, so copying is held back with a reason.
  const viewerReason = isViewer ? 'Ask a workspace admin for a read-only key, then come back to copy the commands.' : '';
  const applyCommand = cliApplyCommand(snapshot.kind);
  const skillsDir = profileId === 'codex' ? '.agents/skills/' : '.claude/skills/';
  return <Stack spacing={3} sx={{ minWidth: 0 }}>
    {!isSkill && productionVersion != null && viewingVersion !== productionVersion && <Stack spacing={1}><Typography variant="s1">You’re viewing v{viewingVersion}. {useViewed ? `Installing this non-production version does not change production v${productionVersion}.` : `Install defaults to production v${productionVersion}.`}</Typography><FormControlLabel control={<Switch checked={Boolean(useViewed)} onChange={(event) => onUseViewed?.(event.target.checked)} />} label={`Install viewed v${viewingVersion} instead`} /></Stack>}
    {!isSkill && productionVersion == null && <Typography variant="s3" color="text.secondary">No production version is assigned. This plan installs the viewed version only.</Typography>}
    <Section title={title || `Install ${snapshot.name}${versionText}`}>
      <Stack spacing={2} sx={{ minWidth: 0 }}>
        <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}>
          {onViewFiles && <Link component="button" variant="s3" onClick={onViewFiles}>View published files</Link>}
          {onViewFiles && onOpenLinks && <Typography variant="s3" color="text.secondary">·</Typography>}
          {onOpenLinks && <Link component="button" variant="s3" onClick={onOpenLinks}>Open linked skills</Link>}
        </Stack>
        {preview && <Box><StatusBadge status="unknown" label={`Preview: not yet tested with ${toolName}`} /></Box>}
        <Typography variant="s3" color="text.secondary">{preview ? 'Preview installer. ' : ''}{isSkill ? `Writes the skill into ${skillsDir}` : `Writes AGENTS.md and ${skillsDir}`} only after you review the changes.</Typography>
        <Stack spacing={1}>
          <Typography variant="m3" component="h3">1. Choose your tool</Typography>
          <SegmentedTabs value={profileId} onChange={(_, value) => { setProfileId(value); setCopyError(''); }} aria-label="Choose your tool"><Tab value="codex" label="Codex" /><Tab value="claude-code" label="Claude Code" /></SegmentedTabs>
          <TextField size="small" label="Project directory" value={project} onChange={(event) => { setProject(event.target.value); setCopyError(''); }} helperText="Existing local instructions are never silently replaced." />
        </Stack>
        <Stack spacing={1}>
          <Typography variant="m3" component="h3">2. Create a read-only key</Typography>
          <Box id={`${noun}-runtime-access`} sx={{ minWidth: 0 }}>{isValidElement(runtimeAccess) ? cloneElement(runtimeAccess, { onAvailabilityChange: setHasKey, showHeading: false }) : runtimeAccess}</Box>
        </Stack>
        <Stack direction="row" spacing={1} alignItems="center" useFlexGap sx={{ flexWrap: 'wrap' }}><Typography variant="m3" component="h3">3. Preview the changes</Typography>{!hasKey && canCreateKey && <StatusBadge status="unknown" label="Create a key first" />}</Stack>
        {problem ? <Alert severity="warning">{problem}</Alert> : <>
          <Typography variant="s3" color="text.secondary">API: {new URL(origin).host}</Typography>
          {isViewer && <Stack direction="row" spacing={1} alignItems="center" useFlexGap sx={{ flexWrap: 'wrap' }}>
            <ActionReason id={`${noun}-copy-reason`} reason={viewerReason} sx={{ flexBasis: 'auto' }} />
            <Button variant="outlined" size="small" disabled={copyBusy} onClick={() => copyCommand(adminRequest)}>Copy request for admin</Button>
          </Stack>}
          {copied === adminRequest && <Typography variant="s3" role="status">Copied the request for an admin.</Typography>}
          <CodeBlock label={`${Noun} install plan command`} code={visibleCommand} copyLabel="Copy" ariaLabel="Copy plan command" action={<Button aria-label="Copy plan command" {...reasonProps(`${noun}-copy-reason`, viewerReason)} variant="outlined" size="small" startIcon={<Iconify icon="solar:copy-linear" width={16} />} disabled={copyBusy || isViewer} onClick={() => copyCommand(command)}>Copy</Button>} />
          {copied === command && <Typography variant="s3" role="status">Copied the complete plan command.</Typography>}
          {copyError && <Alert severity="error">{copyError}</Alert>}
          <Stack spacing={1}>
            <Typography variant="m3" component="h3">4. Install</Typography>
            <Typography variant="s3" color="text.secondary">Check every file change in the preview and confirm the version number{snapshot.version != null ? ` (v${snapshot.version})` : ''} in Before you install. Stop the coding tool before installing. This page does not run commands.</Typography>
            <CodeBlock label={`${Noun} apply command`} code={applyCommand} copyLabel="Copy" ariaLabel="Copy apply command" action={<Button aria-label="Copy apply command" {...reasonProps(`${noun}-copy-reason`, viewerReason)} variant="outlined" size="small" startIcon={<Iconify icon="solar:copy-linear" width={16} />} disabled={copyBusy || isViewer} onClick={() => copyCommand(applyCommand)}>Copy</Button>} />
            {copied === applyCommand && <Typography variant="s3" role="status">Copied the apply command.</Typography>}
          </Stack>
        </>}
        {afterInstall}
      </Stack>
    </Section>
    <TechnicalDetails title="Before you install">
      <Stack spacing={1}>
        {!problem && <>
          <Typography variant="s3" color="text.secondary">Before you run the plan, set FI_CONTEXT_CREDENTIAL to your read-only key in your terminal. Never paste the key into the command. The flag --allow-preview opts in to the preview setup for {toolName}.</Typography>
          <Typography variant="s3" color="text.secondary">Environment variables, included when you copy the plan command:</Typography>
          <Box component="pre" sx={{ m: 0, typography: 's1', fontFamily: 'monospace', overflowX: 'auto', maxWidth: 1 }}>{environment}</Box>
        </>}
        {preview && <Typography variant="s3" color="text.secondary">Requires an environment with the Context SDK CLI installed (python -m {isSkill ? 'fi.skills' : 'fi.context'}). This plan opts in to a preview host profile. No released package or verified model delivery is claimed.</Typography>}
        {profileId === 'claude-code' && !isSkill && <Typography variant="s3" color="text.secondary">Existing CLAUDE.md or local instructions may affect AGENTS.md discovery. Settings, imports and permissions are not changed.</Typography>}
        {workspaceName && <Typography variant="s1">Workspace: {workspaceName}</Typography>}
        <Typography variant="s1">{snapshot.label ? `This plan installs ${snapshot.name} from the ${snapshot.label} label${snapshot.version != null ? `, which points to v${snapshot.version} now` : ''}. Check the version number in the preview before you apply it.` : `This plan installs ${snapshot.name} v${snapshot.version}. Check that the version number matches before you apply it.`}</Typography>
        <IdentityChip value={workspaceId} kind="id" label="Workspace" />
        <IdentityChip value={organizationId} kind="id" label="Organization" />
        {snapshot.versionId && <IdentityChip value={snapshot.versionId} kind="id" label="Version" />}
        {snapshot.digest && <IdentityChip value={snapshot.digest} label="Digest" />}
        <Typography variant="s3">{snapshot.label ? 'The label can move. The plan records the digest it resolved, and a later release needs a new reviewed plan.' : 'Pinned version. A later release requires a new reviewed plan.'} Installed files are not proof of model delivery.</Typography>
      </Stack>
    </TechnicalDetails>
    {footer}
  </Stack>;
}
InstallSteps.propTypes = { snapshot: PropTypes.object.isRequired, origin: PropTypes.string, originError: PropTypes.string, organizationId: PropTypes.string, workspaceId: PropTypes.string, qualification: PropTypes.object, workspaceName: PropTypes.string, onViewFiles: PropTypes.func, onOpenLinks: PropTypes.func, runtimeAccess: PropTypes.node, productionVersion: PropTypes.number, viewingVersion: PropTypes.number, useViewed: PropTypes.bool, onUseViewed: PropTypes.func, title: PropTypes.string, afterInstall: PropTypes.node, footer: PropTypes.node };

export default function ContextUsePanel({ scopeKey, state = 'unavailable', snapshot, onRetry, ...props }) {
  if (!scopeKey || state !== 'ready' || !snapshot) return <ReviewState state={!scopeKey ? 'forbidden' : state} onRetry={onRetry} />;
  return <InstallSteps key={`${scopeKey}:${snapshot.versionId}`} {...props} snapshot={snapshot} />;
}
ContextUsePanel.propTypes = { scopeKey: PropTypes.string, state: PropTypes.string, snapshot: PropTypes.object, onRetry: PropTypes.func };
