import { useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import { MenuItem, TextField } from '@mui/material';
import { useSkillLabels, useSkillVersions } from 'src/api/skills';

export default function VersionSelect({ skillId, value, onChange, includeDraft = false, includeLabels = true }) {
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [skillId]);
  const versions = useSkillVersions(skillId, { page, pageSize: 100 });
  const labels = useSkillLabels(includeLabels ? skillId : null);
  const options = (versions.data?.results || []).map((version) => ({ value: version.number, label: `v${version.number}` }));
  if (includeLabels) {
    options.unshift(...(labels.data || []).map((label) => ({ value: `label:${label.name}`, label: `${label.name} · v${label.version}` })));
    if (versions.data?.count) options.unshift({ value: 'label:latest', label: 'Latest version' });
  }
  if (includeDraft) options.unshift({ value: 'draft', label: 'Your draft' });
  // Keep a controlled value visible even when paging through older versions.
  if (value != null && value !== '' && !options.some((option) => option.value === value)) options.unshift({ value, label: typeof value === 'number' ? `v${value}` : value === 'draft' ? 'Your draft' : value.replace(/^label:/, '') });
  const failed = versions.isError || (includeLabels && labels.isError);
  return <TextField select fullWidth size="small" label="Version" value={value ?? ''} error={failed}
    helperText={failed ? 'Could not load versions. Open the menu to retry.' : versions.isPending ? 'Loading versions…' : undefined}
    onChange={(event) => {
      if (event.target.value === '__next') setPage((current) => current + 1);
      else if (event.target.value === '__previous') setPage((current) => Math.max(1, current - 1));
      else if (event.target.value === '__retry') { versions.refetch(); if (includeLabels) labels.refetch(); }
      else onChange(event.target.value);
    }}>
    {options.map((option) => <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>)}
    {!options.length && <MenuItem value="" disabled>{versions.isPending ? 'Loading…' : 'No published versions'}</MenuItem>}
    {page > 1 && <MenuItem value="__previous">Newer versions</MenuItem>}
    {page < (versions.data?.totalPages || 1) && <MenuItem value="__next">Older versions</MenuItem>}
    {failed && <MenuItem value="__retry">Retry loading versions</MenuItem>}
  </TextField>;
}
VersionSelect.propTypes = { skillId: PropTypes.string, value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]), onChange: PropTypes.func.isRequired, includeDraft: PropTypes.bool, includeLabels: PropTypes.bool };
