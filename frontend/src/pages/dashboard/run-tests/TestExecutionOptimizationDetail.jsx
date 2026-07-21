import React from "react";
import { Helmet } from "react-helmet-async";
import OptimizationDetail from "../../../sections/test-detail/OptimizationDetail/OptimizationDetail";
import OSSUpgradeGate from "src/components/oss-upgrade-gate";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

const TestExecutionOptimizationDetail = () => {
  const { isOSS } = useDeploymentMode();

  return (
    <>
      <Helmet>
        <title>Optimization Detail</title>
      </Helmet>
      {isOSS ? <OSSUpgradeGate feature="optimization" /> : <OptimizationDetail />}
    </>
  );
};

export default TestExecutionOptimizationDetail;
