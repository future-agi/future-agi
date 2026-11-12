import React from "react";
import { localProviderLogo } from "src/utils/localProviderLogo";
import { SegmentedTabs } from "src/components/tabs/tabs";
import {
  Box,
  LinearProgress,
  Typography,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Tab,
} from "@mui/material";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";

import InstructionCodeCopy from "./InstructionCodeCopy";

const ObserveInstruments = ({
  data,
  isLoading,
  error,
  isSuccess,
  languageTab,
  onLanguageChange,
}) => {
  const tabOptions = [
    { label: "Python", value: "python", disabled: false },
    { label: "TypeScript", value: "typescript", disabled: false },
  ];

  // const onCopyClick = () => {
  //   trackEvent(Events.telemetryForSpecificProviderCopied);
  // };

  const getAvailableLanguages = (item) => {
    const languages = [];
    if (item.Python) languages.push("python");
    if (item.TypeScript) languages.push("typescript");
    return languages;
  };

  const getCurrentLanguageData = (item) => {
    if (languageTab === "python" && item.Python) {
      return item.Python;
    }
    if (languageTab === "typescript" && item.TypeScript) {
      return item.TypeScript;
    }
    return item.Python || item.TypeScript;
  };

  // On the TypeScript tab, an entry with no TypeScript block keeps its card
  // with a "Python only" note and no code (no Python fallback), and entries
  // with TypeScript come first. The Python tab keeps the API order.
  const onTypeScript = languageTab === "typescript";
  const entries = data ? Object.entries(data) : [];
  const orderedEntries = onTypeScript
    ? [
        ...entries.filter(([, item]) => item?.TypeScript),
        ...entries.filter(([, item]) => !item?.TypeScript),
      ]
    : entries;

  if (isLoading) {
    return <LinearProgress />;
  }
  if (error) {
    return <Box>Error fetching Tools</Box>;
  }

  return (
    <Box
      display="flex"
      gap={2}
      sx={{
        marginTop: 2,
        width: "100%",
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(2, 1fr)",
          gap: 2,
          width: "100%",
          minWidth: 0,
          "& > *:last-child:nth-child(odd)": {
            gridColumn: "1 / 2",
          },
        }}
      >
        <ShowComponent condition={isSuccess}>
          {orderedEntries.length > 0 ? (
            orderedEntries.map(([key, item]) => {
              const availableLanguages = getAvailableLanguages(item);
              const currentLanguageData = getCurrentLanguageData(item);
              const pythonOnly =
                onTypeScript && !item?.TypeScript && Boolean(item?.Python);

              const filteredTabs = tabOptions.filter((tab) =>
                availableLanguages.includes(tab.value),
              );

              const effectiveLanguage = availableLanguages.includes(languageTab)
                ? languageTab
                : availableLanguages[0];

              return (
                <Box key={key} sx={{ alignSelf: "flex-start", minWidth: 0 }}>
                  <Accordion
                    sx={{
                      boxShadow: "none",
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: "8px",
                      paddingY: "5px",
                      width: "100%",
                      minWidth: 0,
                    }}
                  >
                    <AccordionSummary
                      aria-controls={`panel${key}-content`}
                      id={`panel${key}-header`}
                      sx={{
                        position: "relative",
                      }}
                    >
                      <Box display="flex" alignItems="center" gap={2}>
                        <img
                          src={localProviderLogo(
                            item?.provider ?? item?.name ?? key,
                          )}
                          alt={`${item?.name} Logo`}
                          style={{
                            width: "25px",
                            height: "25px",
                            objectFit: "contain",
                          }}
                        />
                        <Typography variant="body1">{item?.name}</Typography>
                        {pythonOnly ? (
                          <Typography
                            variant="body2"
                            sx={{ color: "text.secondary" }}
                          >
                            Python only
                          </Typography>
                        ) : null}
                      </Box>
                      {currentLanguageData?.github ? (
                        <img
                          onClick={(e) => {
                            e.stopPropagation();
                            window.open(
                              currentLanguageData.github,
                              "_blank",
                              "noopener, noreferrer",
                            );
                          }}
                          src="/assets/icons/app/ic_github_grey.svg"
                          alt="github icon"
                          style={{
                            position: "absolute",
                            right: "16px",
                            top: "50%",
                            transform: "translateY(-50%)",
                            width: "24px",
                            height: "24px",
                            cursor: "pointer",
                          }}
                        />
                      ) : null}
                    </AccordionSummary>
                    <AccordionDetails sx={{ minWidth: 0 }}>
                      {pythonOnly ? (
                        <Typography
                          variant="body2"
                          sx={{ color: "text.secondary" }}
                        >
                          No TypeScript instrumentor. Switch to Python to see
                          its setup code.
                        </Typography>
                      ) : (
                        <Box sx={{ minWidth: 0 }}>
                          {filteredTabs.length > 0 && (
                            <Box sx={{ mb: 1.5 }}>
                              <SegmentedTabs
                                value={effectiveLanguage}
                                onChange={(event, newValue) =>
                                  onLanguageChange(newValue)
                                }
                              >
                                {filteredTabs.map((tab) => (
                                  <Tab
                                    key={`${key}-${tab.value}`}
                                    label={tab.label}
                                    value={tab.value}
                                    disabled={
                                      !availableLanguages.includes(tab.value)
                                    }
                                  />
                                ))}
                              </SegmentedTabs>
                            </Box>
                          )}
                          <Box
                            sx={{
                              overflowX: "auto",
                              overflowY: "hidden",
                              width: "100%",
                              minWidth: 0,
                              maxWidth: "100%",
                              "&::-webkit-scrollbar": {
                                height: "6px",
                              },
                              "&::-webkit-scrollbar-track": {
                                backgroundColor: "transparent",
                              },
                              "&::-webkit-scrollbar-thumb": {
                                backgroundColor: "var(--scrollbar-thumb)",
                                borderRadius: "4px",
                              },
                              "&::-webkit-scrollbar-thumb:hover": {
                                backgroundColor: "var(--scrollbar-thumb)",
                              },
                              "& > *": {
                                maxWidth: "100%",
                                boxSizing: "border-box",
                              },
                              "& pre, & code": {
                                whiteSpace: "pre-wrap",
                                overflowWrap: "anywhere",
                                wordBreak: "normal",
                                maxWidth: "100%",
                                boxSizing: "border-box",
                              },
                            }}
                          >
                            <InstructionCodeCopy
                              text={getCurrentLanguageData(item)?.code}
                              language={effectiveLanguage}
                              // onCopy={onCopyClick}
                            />
                          </Box>
                        </Box>
                      )}
                    </AccordionDetails>
                  </Accordion>
                </Box>
              );
            })
          ) : (
            <Typography
              sx={{ gridColumn: "1 / -1", textAlign: "center", py: 2 }}
            >
              No instruments found.
            </Typography>
          )}
        </ShowComponent>
      </Box>
    </Box>
  );
};

ObserveInstruments.propTypes = {
  data: PropTypes.object,
  isLoading: PropTypes.bool,
  error: PropTypes.any,
  isSuccess: PropTypes.bool,
  languageTab: PropTypes.string,
  onLanguageChange: PropTypes.func,
};

export default ObserveInstruments;
