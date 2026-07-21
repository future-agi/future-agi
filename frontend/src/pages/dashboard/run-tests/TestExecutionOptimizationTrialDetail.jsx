import React from "react";
import { Helmet } from "react-helmet-async";
import TrialDetail from "../../../sections/test-detail/TrialDetail/TrialDetail";
import OSSUpgradeGate from "src/components/oss-upgrade-gate";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

const TestExecutionOptimizationTrialDetail = () => {
  const { isOSS } = useDeploymentMode();

  return (
    <>
      <Helmet>
        <title>Trial Detail</title>
      </Helmet>
      {isOSS ? <OSSUpgradeGate feature="optimization" /> : <TrialDetail />}
    </>
  );
};

export default TestExecutionOptimizationTrialDetail;
