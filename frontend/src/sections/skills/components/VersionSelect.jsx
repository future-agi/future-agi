import { useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import { MenuItem, TextField } from '@mui/material';
import { useSkillLabels, useSkillVersions } from 'src/api/skills';

// `annotate` (opt-in): list each version once, with its label names appended
// ("v2 · production · latest"). Values stay version numbers; label aliases are
// not offered, so callers resolve a `label:` value to its number first. The
// closed field shows only "v2": the row beside it already names production and
// latest (UX iteration 4, SH9), so the menu alone carries the labels.
// `ariaLabel` (opt-in): no visible field label; the surrounding text labels it.
export default function VersionSelect({ skillId, value, onChange, includeDraft = false, includeLabels = true, label = "Version", annotate = false, ariaLabel }) {
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [skillId]);
  const versions = useSkillVersions(skillId, { page, pageSize: 100 });
  const labels = useSkillLabels(includeLabels || annotate ? skillId : null);
  const results = versions.data?.results || [];
  // Latest is only knowable on the first (newest) page.
  const latestNumber = page === 1 && results.length ? Math.max(...results.map((version) => version.number)) : null;
  const names = (number) => [...new Set([
    ...(annotate ? (labels.data || []).filter((entry) => entry.version === number).map((entry) => entry.name) : []),
    ...(annotate && number === latestNumber ? ['latest'] : []),
  ])];
  const options = results.map((version) => ({ value: version.number, label: [`v${version.number}`, ...names(version.number)].join(' · ') }));
  if (includeLabels) {
    options.unshift(...(labels.data || []).map((label) => ({ value: `label:${label.name}`, label: `${label.name} · v${label.version}` })));
    if (versions.data?.count) options.unshift({ value: 'label:latest', label: 'Latest version' });
  }
  if (includeDraft) options.unshift({ value: 'draft', label: 'Your draft' });
  // Keep a controlled value visible even when paging through older versions.
  if (value != null && value !== '' && !options.some((option) => option.value === value)) options.unshift({ value, label: typeof value === 'number' ? `v${value}` : value === 'draft' ? 'Your draft' : value.replace(/^label:/, '') });
  const failed = versions.isError || ((includeLabels || annotate) && labels.isError);
  const shortValue = annotate ? (selected) => (typeof selected === 'number' ? `v${selected}` : options.find((option) => option.value === selected)?.label ?? '') : undefined;
  const selectProps = { ...(ariaLabel ? { inputProps: { 'aria-label': ariaLabel } } : {}), ...(shortValue ? { renderValue: shortValue } : {}) };
  return <TextField select fullWidth size="small" label={ariaLabel ? undefined : label} value={value ?? ''} error={failed}
    SelectProps={Object.keys(selectProps).length ? selectProps : undefined}
    helperText={failed ? 'Could not load versions. Open the menu to retry.' : versions.isPending ? 'Loading versions…' : undefined}
    onChange={(event) => {
      if (event.target.value === '__next') setPage((current) => current + 1);
      else if (event.target.value === '__previous') setPage((current) => Math.max(1, current - 1));
      else if (event.target.value === '__retry') { versions.refetch(); if (includeLabels || annotate) labels.refetch(); }
      else onChange(event.target.value);
    }}>
    {options.map((option) => <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>)}
    {!options.length && <MenuItem value="" disabled>{versions.isPending ? 'Loading…' : 'No published versions'}</MenuItem>}
    {page > 1 && <MenuItem value="__previous">Newer versions</MenuItem>}
    {page < (versions.data?.totalPages || 1) && <MenuItem value="__next">Older versions</MenuItem>}
    {failed && <MenuItem value="__retry">Retry loading versions</MenuItem>}
  </TextField>;
}
VersionSelect.propTypes = { annotate: PropTypes.bool, ariaLabel: PropTypes.string, label: PropTypes.string, skillId: PropTypes.string, value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]), onChange: PropTypes.func.isRequired, includeDraft: PropTypes.bool, includeLabels: PropTypes.bool };
