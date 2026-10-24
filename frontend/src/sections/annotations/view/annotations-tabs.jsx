import { Tab, Tabs } from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";

const TABS = [
  { id: "queues", label: "Queues", path: paths.dashboard.annotations.queues },
  { id: "labels", label: "Labels", path: paths.dashboard.annotations.labels },
];

export default function AnnotationsTabs() {
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const currentTabId =
    TABS.find((t) => pathname.startsWith(t.path))?.id || TABS[0].id;

  // @ts-ignore
  const handleTabChange = (_, newTabId) => {
    const tab = TABS.find((t) => t.id === newTabId);
    if (tab) navigate(tab.path);
  };

  return (
    <Tabs
      value={currentTabId}
      onChange={handleTabChange}
      sx={{ borderBottom: "1px solid", borderColor: "divider" }}
    >
      {TABS.map((tab) => (
        <Tab key={tab.id} label={tab.label} value={tab.id} />
      ))}
    </Tabs>
  );
}
