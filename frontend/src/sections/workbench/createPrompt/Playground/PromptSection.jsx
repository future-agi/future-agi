import React, { useState } from "react";
import { Box, useTheme } from "@mui/material";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragOverlay,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import PromptCard from "src/components/PromptCards/PromptCard";
import SVGColor from "src/components/svg-color";
import { getRandomId } from "src/utils/utils";
import GeneratePromptDrawer from "src/components/GeneratePromptDrawer";
import ImprovePromptDrawer from "src/components/ImprovePromptDrawer";
import { PromptRoles } from "src/utils/constants";
import PropTypes from "prop-types";
import { useLocation, useParams } from "react-router";
import { useSocket } from "src/hooks/use-socket";
import LoadingPromptSection from "../LoadingSkeleton/LoadingPromptSection";
import { usePromptWorkbenchContext } from "../WorkbenchContext";
import { AddMessageButton } from "./PromptContainerStyleComponent";
import ModelContainer from "./ModelContainer";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { enqueueSnackbar } from "notistack";

// Sortable wrapper component for each prompt card
const SortablePromptCard = ({
  id,
  role,
  content,
  idx,
  onRoleChange,
  disabled,
  onPromptChange,
  onRemove,
  onCopyPrompt,
  openVariableEditor,
  appliedVariableData,
  isSync,
  onSyncChange,
  onGeneratePrompt,
  onImprovePrompt,
  existingRoles,
  allowAttachment = false,
  jinjaMode = false,
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

  const [expandPrompt, setExpandPrompt] = useState({});

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 0 : 1,
  };

  return (
    <Box
      ref={setNodeRef}
      style={style}
      sx={{
        width: "100%",
      }}
    >
      <PromptCard
        key={id}
        role={role}
        prompt={content}
        index={idx}
        onRoleChange={onRoleChange}
        disabled={disabled}
        onPromptChange={onPromptChange}
        onRemove={onRemove}
        onCopyPrompt={onCopyPrompt}
        openVariableEditor={openVariableEditor}
        appliedVariableData={appliedVariableData}
        isSync={isSync}
        onSyncChange={onSyncChange}
        onGeneratePrompt={onGeneratePrompt}
        onImprovePrompt={onImprovePrompt}
        viewOptions={{
          allowRoleChange: true,
          allowAllRoleChange: true,
          allowRemove: true,
          sortable: true,
          allowAttachment,
        }}
        existingRoles={existingRoles}
        // Pass drag handle props to the card
        dragHandleProps={{ ...attributes, ...listeners }}
        expandable
        expandPrompt={expandPrompt?.[idx] ?? false}
        setExpandPrompt={(value) => {
          setExpandPrompt((prev) => {
            const copy = { ...prev };
            copy[idx] = value;
            return copy;
          });
        }}
        hideExpandedHeader
        showEditEmbed
        jinjaMode={jinjaMode}
      />
    </Box>
  );
};

SortablePromptCard.propTypes = {
  id: PropTypes.string.isRequired,
  role: PropTypes.string,
  content: PropTypes.array,
  idx: PropTypes.number,
  onRoleChange: PropTypes.func,
  disabled: PropTypes.bool,
  onPromptChange: PropTypes.func,
  onRemove: PropTypes.func,
  onCopyPrompt: PropTypes.func,
  openVariableEditor: PropTypes.func,
  appliedVariableData: PropTypes.object,
  isSync: PropTypes.bool,
  onSyncChange: PropTypes.func,
  onGeneratePrompt: PropTypes.func,
  onImprovePrompt: PropTypes.func,
  existingRoles: PropTypes.array,
  allowAttachment: PropTypes.bool,
};

const PromptSection = ({
  prompts,
  setPrompts,
  modelConfig,
  setModelConfig,
  index,
  isSync,
  onSyncChange,
  syncSystemPrompt,
}) => {
  const theme = useTheme();
  const { id } = useParams();
  const { role: userRole } = useAuthContext();
  const location = useLocation();
  const existingRoles = prompts.map((p) => p?.role);
  const isChatModel =
    !modelConfig?.model_detail?.type ||
    modelConfig?.model_detail?.type === "chat" ||
    modelConfig?.model_detail?.type === "stt";

  const {
    setVariableDrawerOpen,
    variableData,
    loadingPrompt,
    openSelectModel,
    setOpenSelectModel,
    templateFormat,
  } = usePromptWorkbenchContext();

  const [openGeneratePromptDrawer, setOpenGeneratePromptDrawer] = useState({
    state: location.state?.fromOption === "gen_ai",
    index: location.state?.fromOption === "gen_ai" ? 1 : null,
  });
  const [openImprovePromptDrawer, setOpenImprovePromptDrawer] = useState({
    state: false,
    index: null,
  });

  const { socket, sendMessage: sendSocketMessage } = useSocket();

  // Track active dragged item
  const [activeId, setActiveId] = useState(null);

  // Setup sensors for drag and drop
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8, // Require 8px of movement before drag starts
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const onAddPrompt = () => {
    setPrompts((prev) => [
      ...prev,
      { id: getRandomId(), role: "user", content: [] },
    ]);
  };

  const onRemovePrompt = (id) => {
    setPrompts((prev) => {
      if (prev.length <= 1) {
        enqueueSnackbar("You must have at least one prompt.", {
          variant: "warning",
        });
        return prev;
      }

      return prev?.filter((prompt) => prompt?.id !== id);
    });
  };

  const handleApplyPrompt = (promptIndex, prompt) => {
    if (promptIndex < 0 || promptIndex >= prompts.length) {
      return;
    }

    const copy = [...prompts];
    copy[promptIndex] = {
      ...copy[promptIndex],
      id: getRandomId(),
      content: [
        {
          type: "text",
          text: prompt,
        },
      ],
    };

    setPrompts({ prompts: copy, id: getRandomId() });
  };

  // Handle drag end event
  const handleDragEnd = (event) => {
    const { active, over } = event;

    if (over && active.id !== over.id) {
      setPrompts((items) => {
        const oldIndex = items.findIndex((item) => item.id === active.id);
        const newIndex = items.findIndex((item) => item.id === over.id);

        return arrayMove(items, oldIndex, newIndex);
      });
    }
    setActiveId(null);
    // makes api call on every change so no need to call additionally
  };

  if (loadingPrompt) {
    return <LoadingPromptSection />;
  }

  const validVariables =
    variableData &&
    typeof variableData === "object" &&
    !Array.isArray(variableData)
      ? Object.keys(variableData)
      : [];

  const handleSubsribeToMessage = () => {
    if (socket) {
      sendSocketMessage({
        type: "subscribe",
        uuid: id,
      });
    }
  };

  const normalizeNestedArrays = (obj) => {
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
      return {};
    }

    return Object.fromEntries(
      Object.entries(obj).map(([k, v]) => [
        k,
        v.map((item) =>
          Array.isArray(item) ? item.flat(Infinity).join(", ") : item,
        ),
      ]),
    );
  };

  return (
    <>
      <Box
        sx={{
          display: "flex",
          gap: theme.spacing(2),
          flexDirection: "column",
          height: "100%",
        }}
      >
        <ModelContainer
          modelConfig={modelConfig}
          setModelConfig={setModelConfig}
          open={openSelectModel === index}
          setOpen={(v) => setOpenSelectModel(v ? index : null)}
          promptIndex={index}
        />
        <Box
          sx={{
            display: "flex",
            gap: theme.spacing(2),
            flexDirection: "column",
            overflowY: "auto",
            height: "100%",
          }}
        >
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
            onDragStart={(event) => setActiveId(event.active.id)}
          >
            <SortableContext
              items={prompts.map((p) => p.id)}
              strategy={verticalListSortingStrategy}
              disabled={false}
            >
              {prompts.map(({ id, role, content }, idx) => {
                const onCopyPrompt = () => {
                  if (content && Array.isArray(content) && content.length > 0) {
                    const textContent = content
                      .map((c) =>
                        c?.type === "text" && c?.text?.trim() && c.text !== "n"
                          ? c.text
                          : "",
                      )
                      .filter(Boolean)
                      .join("\n");

                    if (textContent) {
                      navigator.clipboard.writeText(textContent);
                      enqueueSnackbar("Prompt copied to clipboard", {
                        variant: "success",
                      });
                    } else {
                      enqueueSnackbar("No text content to copy", {
                        variant: "warning",
                      });
                    }
                  } else {
                    enqueueSnackbar("No text content to copy", {
                      variant: "warning",
                    });
                  }
                };

                const onRoleChange = (newRole) => {
                  setPrompts((prev) => {
                    const newPrompts = [...prev];
                    newPrompts[idx].role = newRole;
                    return newPrompts;
                  });
                };

                const onPromptChange = (newPrompt) => {
                  setPrompts((prev) => {
                    const newPrompts = [...prev];
                    newPrompts[idx].content = newPrompt;
                    return newPrompts;
                  });
                };

                return (
                  <SortablePromptCard
                    key={id}
                    id={id}
                    role={role}
                    content={content}
                    idx={idx}
                    onRoleChange={onRoleChange}
                    disabled={
                      !RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]
                    }
                    onPromptChange={(newPrompt) => {
                      if (
                        role === PromptRoles.SYSTEM &&
                        isSync &&
                        syncSystemPrompt
                      ) {
                        syncSystemPrompt(newPrompt, index);
                      }
                      onPromptChange(newPrompt);
                    }}
                    onRemove={() => onRemovePrompt(id)}
                    onCopyPrompt={onCopyPrompt}
                    openVariableEditor={() => setVariableDrawerOpen(true)}
                    appliedVariableData={normalizeNestedArrays(variableData)}
                    isSync={isSync}
                    onSyncChange={onSyncChange}
                    onGeneratePrompt={() => {
                      setOpenGeneratePromptDrawer({
                        state: true,
                        index: idx,
                      });
                      trackEvent(Events.promptGeneratepromptClicked, {
                        [PropertyName.promptId]: id,
                      });
                    }}
                    onImprovePrompt={() =>
                      setOpenImprovePromptDrawer({
                        state: true,
                        index: idx,
                      })
                    }
                    existingRoles={existingRoles}
                    allowAttachment={isChatModel}
                    jinjaMode={templateFormat === "jinja"}
                  />
                );
              })}
            </SortableContext>
            <DragOverlay>
              {activeId ? (
                <Box
                  sx={{
                    opacity: 0.6,
                    background: theme.palette.background.paper,
                    borderRadius: 1,
                    boxShadow: theme.shadows[16],
                    border: `2px dashed ${theme.palette.primary.main}`,
                    padding: 1,
                    position: "relative",
                    "&::before": {
                      content: '""',
                      position: "absolute",
                      inset: 0,
                      background: `linear-gradient(45deg, ${theme.palette.primary.main}20 25%, transparent 25%, transparent 50%, ${theme.palette.primary.main}20 50%, ${theme.palette.primary.main}20 75%, transparent 75%, transparent)`,
                      backgroundSize: "20px 20px",
                      borderRadius: "inherit",
                      pointerEvents: "none",
                    },
                  }}
                >
                  {(() => {
                    const draggedPrompt = prompts.find(
                      (p) => p.id === activeId,
                    );
                    const draggedIndex = prompts.findIndex(
                      (p) => p.id === activeId,
                    );
                    if (!draggedPrompt) return null;

                    return (
                      <PromptCard
                        role={draggedPrompt.role}
                        prompt={draggedPrompt.content}
                        index={draggedIndex}
                        onRoleChange={() => {}}
                        disabled={true}
                        onPromptChange={() => {}}
                        onRemove={() => {}}
                        onCopyPrompt={() => {}}
                        openVariableEditor={() => {}}
                        appliedVariableData={normalizeNestedArrays(
                          variableData,
                        )}
                        isSync={isSync}
                        onSyncChange={() => {}}
                        onGeneratePrompt={() => {}}
                        onImprovePrompt={() => {}}
                        viewOptions={{
                          allowRoleChange: true,
                          allowAllRoleChange: true,
                          allowRemove: true,
                          sortable: true,
                          allowAttachment: isChatModel,
                        }}
                        existingRoles={existingRoles}
                        expandable
                      />
                    );
                  })()}
                </Box>
              ) : null}
            </DragOverlay>
          </DndContext>

          <Box sx={{ display: "flex", gap: 2 }}>
            <AddMessageButton
              variant="outlined"
              onClick={onAddPrompt}
              startIcon={
                <SVGColor
                  src="/assets/icons/components/ic_add.svg"
                  sx={{ width: 16, height: 16 }}
                />
              }
              sx={{ "& .MuiButton-startIcon": { marginRight: "4px" } }}
              disabled={!RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]}
            >
              Add Message
            </AddMessageButton>
          </Box>

          <GeneratePromptDrawer
            allColumns={validVariables?.map((v) => ({
              headerName: v,
            }))}
            onApplyPrompt={handleApplyPrompt}
            promptFor={openGeneratePromptDrawer.index}
            open={openGeneratePromptDrawer.state}
            onClose={() => {
              handleSubsribeToMessage();
              setOpenGeneratePromptDrawer({
                index: null,
                state: false,
              });
            }}
          />
          <ImprovePromptDrawer
            open={openImprovePromptDrawer.state}
            onClose={() => {
              handleSubsribeToMessage();
              setOpenImprovePromptDrawer({
                index: null,
                state: false,
              });
            }}
            variables={validVariables}
            existingPrompt={prompts[openImprovePromptDrawer.index]?.content}
            onApplyPrompt={handleApplyPrompt}
            promptFor={openImprovePromptDrawer.index}
          />
        </Box>
      </Box>
    </>
  );
};

PromptSection.propTypes = {
  prompts: PropTypes.array,
  setPrompts: PropTypes.func,
  modelConfig: PropTypes.object,
  setModelConfig: PropTypes.func,
  index: PropTypes.number,
  isSync: PropTypes.bool,
  onSyncChange: PropTypes.func,
  syncSystemPrompt: PropTypes.func,
  placeholders: PropTypes.array,
  setPlaceholders: PropTypes.func,
};

export default PromptSection;
