import { Box } from "@mui/material";
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import RightMenu from "./Menubar/RightMenu";
import LeftMenu from "./Menubar/LeftMenu";

const TopMenu = ({
  handleLabelsAdd,
  handleModelSettingData,
  variables,
  versions,
  resultState,
  currentIndex,
  setCurrentIndex,
  setExtractedVars,
  initialConfig,
  evalsConfigs,
  setEvalsConfigs,
  handleDelete,
  handleCreateDraft,
  appliedVariableData,
  setAppliedVariableData,
  currentTitle,
  setCurrentTitle,
  versionIndex,
  setVersionIndex,
  setVersionList,
  titles,
  setTitles,
  total,
  templateFormat,
  setTemplateFormat,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  // const [titles, setTitles] = useState([]);
  const { data } = useQuery({
    queryKey: ["prompts", searchQuery],
    queryFn: () =>
      axios.get(endpoints.develop.runPrompt.promptExecutions(), {
        params: { searchQuery },
      }),
  });

  useEffect(() => {
    setTitles(data?.data?.results ?? []);
    titles?.forEach((title, index) => {
      if (title?.name === currentTitle) {
        setCurrentIndex(index);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "8px 17px",
        borderBottom: 3,
        borderBottomStyle: "solid",
        borderBottomColor: "divider",
        backgroundColor: "background.paper",
      }}
    >
      {/* left menu */}
      <LeftMenu
        versionList={versions}
        versionIndex={versionIndex}
        setVersionIndex={setVersionIndex}
        currentIndex={currentIndex}
        setCurrentIndex={setCurrentIndex}
        handleDelete={handleDelete}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        handleCreateDraft={handleCreateDraft}
        currentTitle={currentTitle}
        setCurrentTitle={setCurrentTitle}
        setVersionList={setVersionList}
      />
      {/* right menu */}
      <RightMenu
        handleLabelsAdd={handleLabelsAdd}
        versionList={versions}
        resultState={resultState}
        handleModelSettingData={handleModelSettingData}
        variables={variables}
        titles={titles}
        setTitles={setTitles}
        currentIndex={currentIndex}
        setCurrentIndex={setCurrentIndex}
        setExtractedVars={setExtractedVars}
        initialConfig={initialConfig}
        evalsConfigs={evalsConfigs}
        setEvalsConfigs={setEvalsConfigs}
        appliedVariableData={appliedVariableData}
        setAppliedVariableData={setAppliedVariableData}
        versionIndex={versionIndex}
        setVersionIndex={setVersionIndex}
        setVersionList={setVersionList}
        total={total}
        currentTitle={currentTitle}
        templateFormat={templateFormat}
        setTemplateFormat={setTemplateFormat}
      />
    </Box>
  );
};

export default TopMenu;

TopMenu.propTypes = {
  handleLabelsAdd: PropTypes.any,
  variables: PropTypes.any,
  handleModelSettingData: PropTypes.any,
  versions: PropTypes.array,
  resultState: PropTypes.string,
  currentIndex: PropTypes.any,
  setCurrentIndex: PropTypes.any,
  setExtractedVars: PropTypes.any,
  initialConfig: PropTypes.object,
  evalsConfigs: PropTypes.array,
  setEvalsConfigs: PropTypes.func,
  handleDelete: PropTypes.func,
  handleCreateDraft: PropTypes.func,
  appliedVariableData: PropTypes.any,
  setAppliedVariableData: PropTypes.func,
  currentTitle: PropTypes.string,
  setCurrentTitle: PropTypes.func,
  versionIndex: PropTypes.number,
  setVersionIndex: PropTypes.func,
  setVersionList: PropTypes.func,
  titles: PropTypes.array,
  setTitles: PropTypes.func,
  total: PropTypes.number,
  templateFormat: PropTypes.string,
  setTemplateFormat: PropTypes.func,
};
