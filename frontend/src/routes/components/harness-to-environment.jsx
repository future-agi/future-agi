import React from "react";
import { Navigate, useParams } from "react-router-dom";

import { paths } from "src/routes/paths";

// The harness pages were the first version of Environments. A harness job's id
// is its environment's id, so an old job link opens that environment.
const HarnessToEnvironment = () => {
  const { jobId } = useParams();
  return (
    <Navigate
      to={paths.dashboard.simulate.environments.detail(jobId)}
      replace
    />
  );
};

export default HarnessToEnvironment;
