import { useEffect, useId, useRef } from 'react';
import PropTypes from 'prop-types';
import { Button, MenuItem, Stack, TextField, Typography } from '@mui/material';

export default function PeoplePicker({ people = [], value, onChange, label, loading = false, error, onRetry, disabled = false, helperText, autoSelectSole = true }) {
  const id = useId();
  const selected = people.find((person) => person.id === value);
  const soleId = people.length === 1 ? people[0].id : null;
  const autoSelected = useRef(null);
  useEffect(() => {
    if (!soleId) autoSelected.current = null;
    if (!loading && !error && !disabled && autoSelectSole && !value && soleId && autoSelected.current !== soleId) {
      autoSelected.current = soleId;
      onChange(soleId);
    }
  }, [soleId, loading, error, disabled, autoSelectSole, value, onChange]);
  // Never silently substitute a different reviewer when eligibility changes.
  // The owning form still validates its selected ID and the server authorizes it.
  const unavailableSelection = Boolean(value && !selected);
  return <Stack spacing={0.5} sx={{ minWidth: 0 }}>
    <TextField
      id={id} label={label} select size="small" fullWidth value={value ?? ''}
      disabled={disabled || loading || Boolean(error) || people.length === 0}
      onChange={(event) => onChange(event.target.value)}
      error={Boolean(error)}
      SelectProps={{ displayEmpty: true, SelectDisplayProps: { 'aria-labelledby': `${id}-label` }, renderValue: (chosen) => loading ? 'Loading people…' : selected?.name || (chosen ? 'Reviewer no longer available' : 'Choose an authorized reviewer') }}
      InputLabelProps={{ shrink: true }}
      // A load error is reported once, below; the guidance under the field stays neutral.
      FormHelperTextProps={{ error: false, sx: { typography: 's3', color: 'text.secondary' } }}
      helperText={selected?.email || helperText}
    >
      <MenuItem value="">Choose an authorized reviewer</MenuItem>
      {unavailableSelection && <MenuItem value={value} disabled>Reviewer no longer available</MenuItem>}
      {people.map((person) => <MenuItem key={person.id} value={person.id}>{person.name}</MenuItem>)}
    </TextField>
    {error && <Stack direction="row" alignItems="center" spacing={1} role="alert">
      <Typography variant="s3" color="text.secondary">Couldn&apos;t load reviewers.</Typography>
      {onRetry && <Button size="small" onClick={onRetry}>Retry</Button>}
    </Stack>}
    {!loading && !error && people.length === 0 && <Typography variant="s3" color="text.secondary">No other eligible reviewers in this workspace. Ask an admin to give someone review access.</Typography>}
    {selected?.email && helperText && <Typography variant="s3" color="text.secondary">{helperText}</Typography>}
  </Stack>;
}

PeoplePicker.propTypes = {
  people: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string.isRequired, email: PropTypes.string })),
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  label: PropTypes.string.isRequired,
  loading: PropTypes.bool,
  error: PropTypes.any,
  onRetry: PropTypes.func,
  disabled: PropTypes.bool,
  helperText: PropTypes.node,
  autoSelectSole: PropTypes.bool,
};
