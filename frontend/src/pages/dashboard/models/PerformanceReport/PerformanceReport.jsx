import React from "react";
import { Helmet } from "react-helmet-async";
import PerformanceReportView from "src/sections/model/performance-report/PerformanceReportView";

const PerformanceReport = () => {
  return (
    <>
      <Helmet>
        <title>Performance Report</title>
      </Helmet>
      <PerformanceReportView />
    </>
  );
};

export default PerformanceReport;
