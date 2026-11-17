import React from "react";
import { useParams } from "react-router-dom";

import AlertsSection from "./AlertsSection";
import MonitoringSection from "./MonitoringSection";

// /monitoring is the Monitoring page; /monitoring/rules and
// /monitoring/channels are the Alerts page's two tabs (the rail's "Alerts").
// The old /monitoring/metrics URL is the Monitoring page.
export default function AlertingMonitoringSection() {
  const { tab } = useParams();
  if (tab === "rules" || tab === "channels") return <AlertsSection tab={tab} />;
  return <MonitoringSection />;
}
