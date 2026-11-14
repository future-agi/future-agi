// Lane-local seam for the shared pieces planned in UX iteration 1 (SH3 access, SH4 ActionReason,
// SH5 When/formatWhen). They are not in this branch's base yet, so the Tests lane imports them
// from here only. When the shared lane lands, this file becomes plain re-exports of
// `../components/ActionReason`, `../components/When`, `../utils/format` and `../utils/access`.
// Signatures match the plan exactly so call sites never change.
import PropTypes from 'prop-types';
import { Box, Tooltip, Typography } from '@mui/material';
import { useOrganization } from 'src/contexts/OrganizationContext';
import { useWorkspace } from 'src/contexts/WorkspaceContext';
import { useSkillV11Scope } from 'src/api/skills/testing';

// SH4: the reason a primary action is disabled, in one line beside it.
export function ActionReason({ id, reason }) {
  if (!reason) return null;
  return <Typography id={id} component="p" variant="s3" color="text.secondary" sx={{ m: 0 }}>{reason}</Typography>;
}
ActionReason.propTypes = { id: PropTypes.string.isRequired, reason: PropTypes.node };
export const reasonProps = (id, reason) => (reason ? { 'aria-describedby': id } : {});

// SH5: one date format for people; the full UTC time is one hover or focus away.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad = (value) => String(value).padStart(2, '0');
const parse = (iso) => { const time = Date.parse(iso); return Number.isFinite(time) ? new Date(time) : null; };
export function formatWhen(iso, { now = Date.now() } = {}) {
  const date = parse(iso);
  if (!date) return 'Time not reported';
  const ago = now - date.getTime();
  if (ago >= 0 && ago < 60_000) return 'Just now';
  if (ago >= 0 && ago < 3_600_000) return `${Math.floor(ago / 60_000)} min ago`;
  if (ago >= 0 && ago < 86_400_000) { const hours = Math.floor(ago / 3_600_000); return `${hours} hour${hours === 1 ? '' : 's'} ago`; }
  return `${date.getDate()} ${MONTHS[date.getMonth()]}, ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
export function formatUtcFull(iso) {
  const date = parse(iso);
  if (!date) return 'Time not reported';
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}, ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`;
}
export function When({ value, variant = 's3', color }) {
  if (!parse(value)) return <Typography component="span" variant={variant} color={color || 'text.secondary'}>Time not reported</Typography>;
  return <Tooltip title={formatUtcFull(value)} describeChild>
    <Box component="time" dateTime={value} tabIndex={0} sx={{ typography: variant, color, whiteSpace: 'nowrap' }}>{formatWhen(value)}</Box>
  </Tooltip>;
}
When.propTypes = { value: PropTypes.string, variant: PropTypes.string, color: PropTypes.string };

// SH3: presentation-only access level for the selected scope (the server stays authoritative).
// Same sameScope rule as the workspace allowance page; nothing is assumed while it loads.
export function useSkillsAccess() {
  const access = useSkillV11Scope();
  const organization = useOrganization();
  const workspace = useWorkspace();
  const ready = Boolean(organization?.isReady && workspace?.isReady
    && organization.currentOrganizationId === access.scope?.organizationId
    && workspace.currentWorkspaceId === access.scope?.workspaceId);
  const level = ready ? Math.max(organization.orgLevel || 0, workspace.wsLevel || 0) : 0;
  return { ready, level, isViewer: ready && level < 3, canWrite: ready && level >= 3, canAdmin: ready && level >= 8 };
}
