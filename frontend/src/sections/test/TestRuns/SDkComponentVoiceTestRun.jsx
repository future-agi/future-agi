import { Box, Skeleton } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import React from "react";
import { useParams } from "react-router";
import InstructionCodeCopy from "src/sections/project/NewProject/InstructionCodeCopy";
import InstructionTitle from "src/sections/project/NewProject/InstructionTitle";
import axios, { endpoints } from "src/utils/axios";

const SDkComponentVoiceTestRun = () => {
  const { testId } = useParams();
  const { data: codeData, isLoading } = useQuery({
    queryKey: ["test-run-sdk-component-voice"],
    queryFn: () => axios.get(endpoints.runTests.getVoiceSDKCode(testId)),
    select: (d) => d?.data?.result,
  });

  const languageTab = "python";
  const cleanCode = (code) => {
    if (typeof code !== "string") return "Code not available";
    return code.replace(/^\n+/, "").replace(/\n+$/, "");
  };

  const getCodeBySection = (section) => {
    return cleanCode(codeData?.[section]);
  };
  if (isLoading) {
    return <Skeleton height={900} width={600} />;
  }
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        width: "600px",
        justifyContent: "center",
        mx: "auto",
        paddingBottom: 2,
      }}
    >
      <InstructionTitle title="Step 1: Install the SDK" />
      <InstructionCodeCopy
        text={getCodeBySection("installation_guide")}
        language={languageTab}
      />

      <InstructionTitle title="Step 2: Create a simulation run" />
      <InstructionCodeCopy
        text={getCodeBySection("sdk_code")}
        language={languageTab}
      />
    </Box>
  );
};

export default SDkComponentVoiceTestRun;
