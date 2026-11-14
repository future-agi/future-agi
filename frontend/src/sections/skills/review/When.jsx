import PropTypes from 'prop-types';
import { Tooltip, Typography } from '@mui/material';
import { relativeReviewTime } from '../utils/review-display';

// Relative labels stay compact; the absolute time is available to keyboard users.
export default function When({ value }) {
  if (!value || !Number.isFinite(Date.parse(value))) return <Typography variant="s3">Time not reported</Typography>;
  const absolute = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
  return <Tooltip title={absolute}><Typography component="time" dateTime={value} tabIndex={0} variant="s3">{relativeReviewTime(value)}</Typography></Tooltip>;
}
When.propTypes = { value: PropTypes.string };
