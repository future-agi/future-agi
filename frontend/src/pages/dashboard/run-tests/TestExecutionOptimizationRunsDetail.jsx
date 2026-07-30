import React from "react";
import { Helmet } from "react-helmet-async";
import OptimizationRunDetail from "../../../sections/test-detail/OptimizationRunDetail/OptimizationRunDetail";
import OSSUpgradeGate from "src/components/oss-upgrade-gate";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

const TestExecutionOptimizationRunsDetail = () => {
  const { isOSS } = useDeploymentMode();

  return (
    <>
      <Helmet>
        <title>Optimization Runs</title>
      </Helmet>
      {isOSS ? (
        <OSSUpgradeGate feature="optimization" />
      ) : (
        <OptimizationRunDetail />
      )}
    </>
  );
};

export default TestExecutionOptimizationRunsDetail;
