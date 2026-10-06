import * as React from "react";
import PropTypes from "prop-types";
import Tabs from "@mui/material/Tabs";
import Tab from "@mui/material/Tab";
import Box from "@mui/material/Box";
import BottomEvalsTab from "./bottom-evals-tab";
import BottomAttributesTab from "./bottom-attributes-tab";
import BottomAnnotationsTab from "./BottomAnnotationTab/bottom-annotations-tab";
import { useSelectedNode } from "./useSelectedNode";
import BottomFunctionsTab from "./BottomFunctionsTab/bottom-functions-tab";
import Label from "../label/label";
import { useScoresForSource, useSpanNotes } from "src/api/scores/scores";
import BottomGuardrailTab from "./BottomGuardrailTab";
import { useTheme } from "@mui/material";
import BottomEventsTab from "./BottomEventsTab/BottomEventsTab";
import {
  getSpanAttributes,
  getSpanEvents,
} from "./DrawerRightRenderer/getSpanData";
import { extractToolCalls, extractToolDefinitions } from "./extractToolData";

function CustomTabPanel(props) {
  const { children, value, index, sx = {}, ...other } = props;

  return (
    <Box
      role="tabpanel"
      hidden={value !== index}
      id={`simple-tabpanel-${index}`}
      aria-labelledby={`simple-tab-${index}`}
      sx={{
        display: value === index ? "block" : "none",
        "&::-webkit-scrollbar": {
          width: "6px",
        },
        "&::-webkit-scrollbar-thumb": {
          backgroundColor: "rgba(0, 0, 0, 0.3)",
          borderRadius: "3px",
        },
        "&::-webkit-scrollbar-track": {
          backgroundColor: "transparent",
        },
        ...sx,
      }}
      {...other}
    >
      {children}
    </Box>
  );
}

CustomTabPanel.propTypes = {
  children: PropTypes.node,
  index: PropTypes.number.isRequired,
  value: PropTypes.number.isRequired,
  sx: PropTypes.object,
};

const LabelWithCount = ({ count, label, selected }) => {
  if (count === null) {
    return label;
  }
  let countColors = "";

  if (selected) {
    countColors = "success";
  } else {
    countColors = "default";
  }

  return (
    <Box>
      {label}{" "}
      <Label
        color={countColors}
        sx={{ fontWeight: countColors == "default" ? 500 : 600 }}
      >
        {count}
      </Label>
    </Box>
  );
};

LabelWithCount.propTypes = {
  count: PropTypes.number,
  label: PropTypes.string,
  selected: PropTypes.bool,
};

function a11yProps(index) {
  return {
    id: `simple-tab-${index}`,
    "aria-controls": `simple-tabpanel-${index}`,
  };
}

/**
 * Unwrap the structured Score JSON value into the flat format that
 * NewAnnotationCellRenderer expects.
 */
function unwrapScoreValue(value, labelType) {
  if (value == null || typeof value !== "object") return value;
  switch (labelType) {
    case "categorical":
      return value.selected ?? value;
    case "thumbs_up_down":
      return value.value ?? value;
    case "star":
      return value.rating ?? value;
    case "numeric":
      return value.value ?? value;
    case "text":
      return value.text ?? value;
    default:
      return value;
  }
}

const DrawerBottom = ({
  traceData,
  showAnnotation,
  observationSpan,
  observationSpanLoading,
  projectId,
}) => {
  const theme = useTheme();
  const [value, setValue] = React.useState(0);

  const { selectedNode } = useSelectedNode();

  const rootSpanId = React.useMemo(
    () =>
      traceData?.observation_spans?.find(
        (entry) => !entry?.observation_span?.parent_span_id,
      )?.observation_span?.id ?? null,
    [traceData?.observation_spans],
  );

  const [selectedAnnotators, setSelectedAnnotators] = React.useState([]);
  const [annotatorFilter, setAnnotatorFilter] = React.useState("contains");

  // Fetch scores at both span and trace level — annotations may be stored
  // on either source depending on how they were created (inline vs queue).
  // The same trace / span id can exist in several projects; read the copy
  // this drawer shows.
  const { data: spanScoresData } = useScoresForSource(
    "observation_span",
    selectedNode?.id,
    { projectId, refetchOnWindowFocus: true },
  );

  const { data: spanNotes } = useSpanNotes(selectedNode?.id || rootSpanId, {
    projectId,
    refetchOnWindowFocus: true,
  });
  const { data: traceScoresData } = useScoresForSource(
    "trace",
    traceData?.trace?.id,
    { projectId, refetchOnWindowFocus: true },
  );

  const scoresData = React.useMemo(() => {
    const spanScores = Array.isArray(spanScoresData) ? spanScoresData : [];
    const traceScores = Array.isArray(traceScoresData) ? traceScoresData : [];
    const seen = new Set();
    const merged = [];
    for (const s of [...spanScores, ...traceScores]) {
      if (!seen.has(s.id)) {
        seen.add(s.id);
        merged.push(s);
      }
    }
    return merged;
  }, [spanScoresData, traceScoresData]);

  const spanAnnotations = React.useMemo(() => {
    if (!scoresData || scoresData.length === 0) return [];

    let filtered = scoresData;

    // Client-side annotator filtering
    if (selectedAnnotators.length > 0) {
      if (annotatorFilter === "contains") {
        filtered = filtered.filter(
          (s) =>
            selectedAnnotators.includes(s.annotator_name) ||
            selectedAnnotators.includes(s.annotator_email),
        );
      } else {
        filtered = filtered.filter(
          (s) =>
            !selectedAnnotators.includes(s.annotator_name) &&
            !selectedAnnotators.includes(s.annotator_email),
        );
      }
    }

    return filtered.map((score) => ({
      id: score.id,
      annotationLabelId: score.label_id,
      annotationLabelName: score.label_name,
      annotationValue: unwrapScoreValue(score.value, score.label_type),
      annotator:
        score.annotator_name || score.annotator_email || score.score_source,
      updatedAt: score.updated_at,
      annotationType: score.label_type,
      settings: score.label_settings,
      notes: score.notes || "",
    }));
  }, [scoresData, selectedAnnotators, annotatorFilter]);

  const handleChange = (event, newValue) => {
    setValue(newValue);
  };

  const observationType = selectedNode?.observation_type;

  const innerSpan = observationSpan?.observation_span;

  const toolCalls = React.useMemo(
    () => extractToolCalls(innerSpan),
    [innerSpan],
  );

  const toolDefinitions = React.useMemo(
    () => extractToolDefinitions(innerSpan),
    [innerSpan],
  );

  const tabs = React.useMemo(() => {
    // maintain the same order
    const allTabs = [
      "Evals",
      "Functions",
      "Attributes",
      "Guardrail",
      "Annotations",
      "Events",
    ];

    const availableTabs = new Set(["Evals", "Attributes"]); // always present

    // Add Guardrail tab if observation type is guardrail
    if (observationType === "guardrail") {
      availableTabs.add("Guardrail");
    }

    // Add Functions tab if observation type is llm
    if (observationType === "llm") {
      availableTabs.add("Functions");
    }

    if (showAnnotation) {
      availableTabs.add("Annotations");
    }

    const spanEvents = getSpanEvents(innerSpan);
    if (spanEvents && spanEvents.length > 0) {
      availableTabs.add("Events");
    }

    return allTabs.filter((tab) => availableTabs.has(tab));
  }, [showAnnotation, observationType, innerSpan]);

  // Fix tab index out-of-bound when tab list changes, select last tab
  React.useEffect(() => {
    if (value >= tabs.length && tabs.length > 0) {
      setValue(tabs.length - 1);
    }
  }, [tabs?.length, value]);

  return (
    <Box
      sx={{
        width: "100%",
        backgroundColor: "background.paper",
        zIndex: 1,
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
        <Tabs
          value={value}
          onChange={handleChange}
          aria-label="basic tabs example"
          textColor="primary"
          indicatorColor="primary"
          sx={{
            marginLeft: "2%",
            "& .Mui-selected": {
              color: "primary.main",
            },
            "& .MuiTabs-indicator": {
              backgroundColor: "primary.main",
            },
            marginRight: 1,
            minHeight: 0,
            "& .MuiTab-root": {
              margin: "0 !important",
              fontWeight: "600",
              typography: "s1",
              "&:not(.Mui-selected)": {
                color: "text.secondary",
                fontWeight: "500",
              },
            },
          }}
        >
          {tabs.map((tab, index) => {
            let count = null;
            if (tab === "Annotations") {
              // @ts-ignore
              count = spanAnnotations?.length || 0;
            } else if (tab === "Functions") {
              count = toolCalls.length + toolDefinitions.length;
            } else if (tab === "Evals") {
              count = Object.keys(observationSpan?.evals_metrics || {}).length;
            } else if (tab === "Guardrail") {
              // Get guardrail rules count from spanAttributes (with fallback to evalAttributes)
              const spanAttrs = getSpanAttributes(innerSpan);
              const guardrailRules =
                spanAttrs["guardrail.rules"] ||
                spanAttrs["raw.input"]?.protectRules ||
                [];
              count = guardrailRules.length;
            } else if (tab === "Events") {
              count =
                traceData?.observation_spans[0]?.observation_span?.span_events
                  ?.length || 0;
            }

            const isSelected = value === index;

            return (
              <Tab
                key={tab}
                label={
                  <LabelWithCount
                    count={count}
                    label={tab}
                    selected={isSelected}
                  />
                }
                {...a11yProps(index)}
                sx={{
                  margin: theme.spacing(0),
                  px: theme.spacing(1.875),
                  "&.MuiTab-root": {
                    color: "text.secondary",
                    fontWeight: 500,
                  },
                  "&.Mui-selected": {
                    color: "primary.main",
                    fontWeight: 600,
                  },
                }}
              />
            );
          })}
        </Tabs>
      </Box>
      <CustomTabPanel
        value={value}
        index={tabs.indexOf("Evals")}
        sx={{ p: "20px", height: "100%", overflow: "auto" }}
      >
        <BottomEvalsTab
          observationSpan={observationSpan}
          isLoading={observationSpanLoading}
        />
      </CustomTabPanel>
      {observationType === "llm" && (
        <CustomTabPanel
          value={value}
          index={tabs.indexOf(`Functions`)}
          sx={{ overflow: "auto" }}
        >
          <BottomFunctionsTab
            toolCalls={toolCalls}
            toolDefinitions={toolDefinitions}
          />
        </CustomTabPanel>
      )}

      {observationType === "guardrail" && (
        <CustomTabPanel
          value={value}
          index={tabs.indexOf(`Guardrail`)}
          sx={{ overflow: "auto" }}
        >
          <BottomGuardrailTab observationSpan={observationSpan} />
        </CustomTabPanel>
      )}
      {/* Attributes Tab */}
      <CustomTabPanel
        value={value}
        index={tabs.indexOf("Attributes")}
        sx={{ p: "20px", height: "100%", overflow: "auto" }}
      >
        <BottomAttributesTab
          observationSpan={innerSpan}
          isLoading={observationSpanLoading}
        />
      </CustomTabPanel>

      {/* Annotations Tab (if applicable) */}
      {showAnnotation && (
        <CustomTabPanel
          value={value}
          index={tabs.indexOf("Annotations")}
          sx={{ p: "20px", height: "100%", overflow: "auto" }}
          style={{ flex: 1 }}
        >
          <BottomAnnotationsTab
            selectedAnnotators={selectedAnnotators}
            setSelectedAnnotators={setSelectedAnnotators}
            annotatorFilter={annotatorFilter}
            setAnnotatorFilter={setAnnotatorFilter}
            spanAnnotations={spanAnnotations}
            spanNotes={spanNotes}
          />
        </CustomTabPanel>
      )}
      {traceData?.observation_spans?.[0]?.observation_span?.span_events
        ?.length > 0 && (
        <CustomTabPanel
          value={value}
          index={tabs.indexOf("Events")}
          sx={{ p: "20px", height: "100%", overflow: "auto" }}
          style={{ flex: 1 }}
        >
          <BottomEventsTab
            spanEvents={
              traceData?.observation_spans[0]?.observation_span?.span_events
            }
          />
        </CustomTabPanel>
      )}
    </Box>
  );
};

DrawerBottom.propTypes = {
  traceData: PropTypes.object,
  showAnnotation: PropTypes.bool,
  observationSpan: PropTypes.object,
  observationSpanLoading: PropTypes.bool,
  projectId: PropTypes.string,
};

export default DrawerBottom;
