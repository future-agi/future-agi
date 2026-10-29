import PropTypes from "prop-types";
import { Tab, Tabs } from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";

const tabs = [
  { label: "Evaluators", path: paths.dashboard.evals },
  { label: "Metrics", path: paths.dashboard.evalMetrics },
  { label: "Usage", path: `${paths.dashboard.evals}/usage` },
  { label: "Groups", path: `${paths.dashboard.evals}/groups` },
];

// Compose the existing Evals navigation appearance, without a theme bridge.
export default function EvalsSectionTabs({ onNavigate, onUsage }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const active =
    tabs
      .slice(1)
      .find(({ path }) => pathname === path || pathname.startsWith(`${path}/`))
      ?.path || paths.dashboard.evals;
  return (
    <Tabs
      aria-label="Evals navigation"
      textColor="primary"
      value={active}
      sx={{ borderBottom: 1, borderColor: "divider", flexShrink: 0 }}
      TabIndicatorProps={{ sx: { bgcolor: "primary.main" } }}
      onChange={(_, path) => {
        const go = () => {
          if (path.endsWith("/usage")) onUsage?.();
          navigate(path);
        };
        if (onNavigate) onNavigate(go);
        else go();
      }}
    >
      {tabs.map(({ label, path }) => (
        <Tab
          key={path}
          label={label}
          value={path}
          sx={(theme) => ({
            px: "24px",
            ...theme.typography.s1,
            fontWeight: theme.typography.fontWeightSemiBold,
            "&:not(.Mui-selected)": {
              color: "text.disabled",
              fontWeight: theme.typography.fontWeightMedium,
            },
            "&:not(:last-of-type)": { mr: "4px" },
          })}
        />
      ))}
    </Tabs>
  );
}
EvalsSectionTabs.propTypes = {
  onNavigate: PropTypes.func,
  onUsage: PropTypes.func,
};
