import React from "react";
import { Helmet } from "react-helmet-async";
import OptimizationRunDetail from "../../../sections/test-detail/OptimizationRunDetail/OptimizationRunDetail";

const TestExecutionOptimizationRunsDetail = () => {
  return (
    <>
      <Helmet>
        <title>Optimization Runs</title>
      </Helmet>
      <OptimizationRunDetail />
    </>
  );
};

export default TestExecutionOptimizationRunsDetail;
