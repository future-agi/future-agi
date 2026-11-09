import PropTypes from "prop-types";
import { Chip } from "@mui/material";
import Iconify from "src/components/iconify";

const STATUS_CONFIG = {
  active: {
    color: "success",
    label: "Active",
    icon: "solar:check-circle-bold",
  },
  syncing: {
    color: "info",
    label: "Syncing",
    icon: "solar:refresh-bold",
  },
  paused: {
    color: "warning",
    label: "Paused",
    icon: "solar:pause-bold",
  },
  error: {
    color: "error",
    label: "Error",
    icon: "solar:danger-bold",
  },
  backfilling: {
    color: "info",
    label: "Backfilling",
    icon: "solar:download-bold",
  },
};

// An export's first sync sends request logs out; it imports nothing.
const EXPORT_LABELS = { backfilling: "First export" };

export default function StatusBadge({ status, isExport = false }) {
  const known = STATUS_CONFIG[status];
  const config = (known && {
    ...known,
    label: (isExport && EXPORT_LABELS[status]) || known.label,
  }) || {
    color: "default",
    label: status || "Unknown",
    icon: "solar:question-circle-bold",
  };
  return (
    <Chip
      size="small"
      color={config.color}
      label={config.label}
      icon={<Iconify icon={config.icon} width={16} />}
    />
  );
}

StatusBadge.propTypes = {
  status: PropTypes.string,
  isExport: PropTypes.bool,
};
