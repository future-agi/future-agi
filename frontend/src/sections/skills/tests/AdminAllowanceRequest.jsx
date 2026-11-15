import { useState } from 'react';
import PropTypes from 'prop-types';
import { Button, Stack, Typography } from '@mui/material';

// A member can't set the workspace allowance, so the blocked test hands them a ready-to-send request
// for a workspace admin instead of a page they can't change (the viewer pattern of ContextUsePanel's
// "Copy request for admin"). The request names what it is for and links straight to the allowance
// page, which leads the admin back to this test. Nothing is changed or sent from here.
export default function AdminAllowanceRequest({ request }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const copy = async () => {
    setBusy(true); setCopied(false); setFailed(false);
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Unavailable');
      await navigator.clipboard.writeText(request);
      setCopied(true);
    } catch { setFailed(true); } finally { setBusy(false); }
  };
  return <Stack spacing={0.5} alignItems="flex-start" sx={{ minWidth: 0 }}>
    <Button variant="outlined" size="small" disabled={busy} onClick={copy}>Copy request for admin</Button>
    {copied && <Typography variant="s3" color="text.secondary" role="status">Copied the request for an admin.</Typography>}
    {failed && <Typography variant="s3" color="error.main" role="alert" sx={{ overflowWrap: 'anywhere' }}>Couldn&apos;t copy. Send a workspace admin this request: {request}</Typography>}
  </Stack>;
}
AdminAllowanceRequest.propTypes = { request: PropTypes.string.isRequired };

// The request text, with an absolute link to the allowance page (carrying ?returnTo= back to the test).
// eslint-disable-next-line react-refresh/only-export-components -- the request wording ships with its button.
export function allowanceRequestText({ purpose, ceilingUsd, href }) {
  const url = typeof window === 'undefined' ? href : new URL(href, window.location.origin).href;
  const limit = ceilingUsd != null && ceilingUsd !== '' ? ` (up to $${ceilingUsd})` : '';
  return `Please set a Skills workspace allowance so I can run ${purpose}${limit}: ${url}`;
}
