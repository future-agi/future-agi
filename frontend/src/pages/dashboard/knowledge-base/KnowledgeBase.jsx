import React from "react";
import { Helmet } from "react-helmet-async";
import KnowledgeBaseView from "src/sections/knowledge-base/KnowledgeBaseView";
import OSSUpgradeGate from "src/components/oss-upgrade-gate";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

const KnowledgeBase = () => {
  const { isOSS } = useDeploymentMode();

  return (
    <>
      <Helmet>
        <title>Knowledge Base</title>
      </Helmet>
      {isOSS ? <OSSUpgradeGate feature="knowledgeBase" /> : <KnowledgeBaseView />}
    </>
  );
};

export default KnowledgeBase;
