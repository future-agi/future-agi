import { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { AuthoringError, useAuthoringOperation } from "../editor/HotfixHeadReview";

// Intent: accept a specific dependency plan without following moving upstream bytes.
// Hierarchy: included version versus upstream, explicit desired set, reviewed apply.
// Palette/depth/surfaces: existing neutral/divider and uncertainty roles, flat groups.
// Typography/spacing: m3/s1/s3 and compact 12px spacing; no dependency graph.
const selectorLabel = (selector) => selector?.version ? `Pin v${selector.version}` : `Follow ${selector?.label}`;
function Links({ scopeKey, session, availableSkills = [], upstream = {}, onPreview, onApply, readOnly = false }) {
  const accepted = session.contextSpec?.links || [];
  const [links, setLinks] = useState(accepted);
  const [refresh, setRefresh] = useState([]);
  const [selected, setSelected] = useState("");
  const [policy, setPolicy] = useState("label");
  const [selector, setSelector] = useState("production");
  const [preview, setPreview] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const operation = useAuthoringOperation(scopeKey);
  const lock = session.resolvedLock?.links || [];
  const invalidate = () => { setPreview(null); setConfirmed(false); };
  const validSelector = policy === "version" ? /^[1-9]\d*$/.test(selector) && Number.isSafeInteger(Number(selector)) : /^[a-z0-9][a-z0-9-]{0,39}$/.test(selector);
  const current = preview && Array.isArray(preview.changes) && Boolean(preview.fingerprint) && Boolean(preview.candidateDigest) && Number.isInteger(preview.checks?.summary?.errors) && !session.dirty && !session.mutationError && !session.pendingMutations && preview.revision === session.revision;
  const prepare = async () => {
    invalidate();
    const result = await operation.run(async () => {
      const saved = await session.flush();
      if (!saved) throw new Error("Save blocked");
      return onPreview({ revision: saved.revision, links, refreshSkillIds: refresh });
    }, { action: "preview-links", links, refresh, editSequence: session.editSequence }, { retainUnknown: false });
    if (result) setPreview(result);
  };
  const apply = async () => {
    if (!current || !confirmed) return;
    const result = await operation.run(async (input, options) => {
      const saved = await session.mutateDraft((draft) => {
        if (draft.revision !== preview.revision) throw Object.assign(new Error("Draft changed"), { code: "draft_conflict" });
        return onApply(input, options);
      }, { operationKey: options.idempotencyKey });
      if (!saved) throw new Error("Apply blocked");
      return saved;
    }, { revision: preview.revision, links, refreshSkillIds: refresh, expectedFingerprint: preview.fingerprint });
    if (result) { setLinks(result.contextSpec.links); setRefresh([]); invalidate(); }
  };
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="m3" component="h2">Linked skills · {lock.length} accepted locks</Typography>
    <Typography variant="s1">Following offers updates. Published contexts stay unchanged. Linked paths are read-only; edit their source skill separately.</Typography>
    <AuthoringError error={operation.error} />
    {session.mutationError && operation.error && <Button variant="outlined" disabled={operation.pending} onClick={async () => { const result = await operation.retry(); if (result) { setLinks(result.contextSpec.links); setRefresh([]); invalidate(); } }}>Retry dependency update</Button>}
    {!links.length && <Typography variant="s3">No linked skills. AGENTS.md remains standing context.</Typography>}
    {links.map((link) => {
      const included = lock.find((entry) => entry.skillId === link.skillId);
      const skill = availableSkills.find((entry) => entry.id === link.skillId);
      const newer = upstream[link.skillId];
      return <Box key={link.skillId} sx={{ borderBottom: 1, borderColor: "divider", py: 1, minWidth: 0 }}>
        <Typography variant="m3" sx={{ overflowWrap: "anywhere" }}>{included?.mount || skill?.name || link.skillId}</Typography>
        <Typography variant="s1">{selectorLabel(link.selector)} · {included ? `Included v${included.version}` : "Not yet accepted"}</Typography>
        <Typography variant="s3" sx={{ overflowWrap: "anywhere" }}>{included?.digest || "Preview required to resolve this link"}</Typography>
        {newer && newer.version !== included?.version && <Typography variant="s3">v{newer.version} available; this draft still includes {included ? `v${included.version}` : "no version"}.</Typography>}
        {!readOnly && <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          {link.selector.label && <FormControlLabel control={<Checkbox checked={refresh.includes(link.skillId)} disabled={operation.pending || Boolean(session.mutationError)} onChange={(event) => { setRefresh(event.target.checked ? [...refresh, link.skillId] : refresh.filter((id) => id !== link.skillId)); invalidate(); }} />} label={`Review update for ${skill?.name || link.skillId}`} />}
          <Button disabled={operation.pending || Boolean(session.mutationError)} onClick={() => { setLinks(links.filter((entry) => entry.skillId !== link.skillId)); setRefresh(refresh.filter((id) => id !== link.skillId)); invalidate(); }}>Remove from context</Button>
        </Stack>}
      </Box>;
    })}
    {!readOnly && <>
      <Typography variant="s3">Remove from this context; keep the shared skill. To change a selector, remove and re-add the skill before previewing.</Typography>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <TextField select fullWidth size="small" label="Published leaf skill" value={selected} disabled={operation.pending || Boolean(session.mutationError)} onChange={(event) => setSelected(event.target.value)}>
          <MenuItem value="">Choose a skill</MenuItem>
          {availableSkills.filter((skill) => skill.kind === "skill" && skill.latestVersion && !links.some((link) => link.skillId === skill.id)).map((skill) => <MenuItem key={skill.id} value={skill.id}>{skill.name}</MenuItem>)}
        </TextField>
        <TextField select size="small" label="Update policy" value={policy} disabled={operation.pending || Boolean(session.mutationError)} onChange={(event) => { setPolicy(event.target.value); setSelector(event.target.value === "version" ? "1" : "production"); }} sx={{ minWidth: 140 }}><MenuItem value="label">Follow label</MenuItem><MenuItem value="version">Pin version</MenuItem></TextField>
        <TextField size="small" label={policy === "version" ? "Version" : "Label"} value={selector} disabled={operation.pending || Boolean(session.mutationError)} onChange={(event) => setSelector(event.target.value)} error={!validSelector} />
      </Stack>
      <Button variant="outlined" disabled={!selected || !validSelector || operation.pending || Boolean(session.mutationError) || links.length >= 20} onClick={() => { setLinks([...links, { skillId: selected, selector: policy === "version" ? { version: Number(selector) } : { label: selector } }]); setSelected(""); invalidate(); }}>Add to proposed links</Button>
      <Button variant="outlined" disabled={!onPreview || operation.pending || Boolean(session.mutationError)} onClick={prepare}>Preview dependency changes</Button>
      {preview && <>
        <Typography variant="s3" sx={{ overflowWrap: "anywhere" }}>Compiled candidate {preview.candidateDigest}</Typography>
        {(preview.changes || []).map((change) => <Typography key={change.skillId} variant="s1">{change.mount} · {change.change} · {change.before ? `v${change.before.version}` : "absent"} → {change.after ? `v${change.after.version}` : "removed"}</Typography>)}
        {preview.checks?.summary?.errors > 0 && <Alert severity="error">The compiled context has blocking checks. No partial dependency set can be applied.</Alert>}
        {!current && <Alert severity="warning">{session.mutationError ? "Recover the original dependency update with Retry dependency update before previewing or publishing." : "The draft changed. Preview again before applying."}</Alert>}
        <FormControlLabel control={<Checkbox checked={confirmed} disabled={!current || operation.pending} onChange={(event) => setConfirmed(event.target.checked)} />} label="Accept these exact resolved versions and selector policies." />
        <Button variant="contained" disabled={!onApply || !current || !confirmed || operation.pending || preview.checks?.summary?.errors > 0} onClick={apply}>Apply selected updates to draft</Button>
      </>}
    </>}
  </Stack>;
}
Links.propTypes = { scopeKey: PropTypes.string.isRequired, session: PropTypes.object.isRequired, availableSkills: PropTypes.array, upstream: PropTypes.object, onPreview: PropTypes.func, onApply: PropTypes.func, readOnly: PropTypes.bool };
export default function ContextLinksPanel(props) { return <Links key={`${props.scopeKey}:${JSON.stringify(props.session.contextSpec?.links)}:${JSON.stringify(props.session.resolvedLock)}`} {...props} />; }
ContextLinksPanel.propTypes = Links.propTypes;
