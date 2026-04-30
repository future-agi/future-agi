import React, { useState, useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography, useTheme } from "@mui/material";
import { Controller, useController, useFormContext } from "react-hook-form";
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
import { enqueueSnackbar } from "notistack";
import PromptCard from "src/components/PromptCards/PromptCard";
import SvgColor from "src/components/svg-color";
import { getRandomId } from "src/utils/utils";
import GeneratePromptDrawer from "src/components/GeneratePromptDrawer";
import ImprovePromptDrawer from "src/components/ImprovePromptDrawer";

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
  onGeneratePrompt,
  onImprovePrompt,
  existingRoles,
  control,
  name,
  dropdownOptions,
  mentionEnabled,
  variableValidator,
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

  const [expandPrompt, setExpandPrompt] = useState(false);

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
      <Controller
        key={id}
        control={control}
        name={name}
        render={({ field, fieldState: { error } }) => {
          return (
            <Box key={id}>
              <PromptCard
                inputRef={field?.ref}
                key={id}
                role={role}
                prompt={content || []}
                index={idx}
                onRoleChange={onRoleChange}
                disabled={disabled}
                onPromptChange={onPromptChange}
                onRemove={onRemove}
                onCopyPrompt={onCopyPrompt}
                onGeneratePrompt={onGeneratePrompt}
                onImprovePrompt={onImprovePrompt}
                showEditEmbed={false}
                viewOptions={{
                  allowRoleChange: true,
                  allowAllRoleChange: true,
                  allowRemove: true,
                  sortable: true,
                  allowAttachment: false,
                  allowSync: false,
                  compact: true,
                }}
                existingRoles={existingRoles}
                dragHandleProps={{ ...attributes, ...listeners }}
                expandable
                expandPrompt={expandPrompt}
                setExpandPrompt={setExpandPrompt}
                dropdownOptions={dropdownOptions}
                mentionEnabled={mentionEnabled}
                variableValidator={variableValidator}
                allVariablesValid={!variableValidator}
                jinjaMode={jinjaMode}
              />
              {
                // @ts-ignore
                error?.content?.message && (
                  <Typography id={name} variant="caption" color={"error.main"}>
                    {
                      // @ts-ignore
                      error?.content?.message
                    }
                  </Typography>
                )
              }
            </Box>
          );
        }}
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
  onGeneratePrompt: PropTypes.func,
  onImprovePrompt: PropTypes.func,
  existingRoles: PropTypes.array,
  control: PropTypes.object.isRequired,
  name: PropTypes.string,
  dropdownOptions: PropTypes.array,
  mentionEnabled: PropTypes.bool,
  variableValidator: PropTypes.func,
};

export default function PromptMessageRow({
  control,
  name = "messages",
  disabled = false,
  showAddButton = true,
  dropdownOptions = [],
  mentionEnabled = false,
  variableValidator,
  jinjaMode = false,
}) {
  const theme = useTheme();
  const { formState } = useFormContext();

  // Use controller to integrate with react-hook-form
  const { field } = useController({
    name,
    control,
    defaultValue: [],
  });

  const messages = useMemo(() => field.value || [], [field.value]);

  // Get error for messages field (from form validation)
  const formMessagesError = formState.errors?.messages;

  const errorMessage = useMemo(() => {
    if (!formMessagesError) return null;
    const message =
      typeof formMessagesError === "string"
        ? formMessagesError
        : formMessagesError?.message;
    if (typeof message === "string") return message;
    if (message != null) return String(message);
    return null;
  }, [formMessagesError]);

  const existingRoles = useMemo(() => messages.map((p) => p?.role), [messages]);

  // Generate / Improve prompt drawer state
  const [openGeneratePromptDrawer, setOpenGeneratePromptDrawer] = useState({
    state: false,
    index: null,
  });
  const [openImprovePromptDrawer, setOpenImprovePromptDrawer] = useState({
    state: false,
    index: null,
  });

  // Apply generated/improved prompt to the message at the given index
  const handleApplyPrompt = useCallback(
    (promptIndex, prompt) => {
      if (promptIndex < 0 || promptIndex >= messages.length) return;
      const newMessages = [...messages];
      newMessages[promptIndex] = {
        ...newMessages[promptIndex],
        id: getRandomId(),
        content: [{ type: "text", text: prompt }],
      };
      field.onChange(newMessages);
    },
    [messages, field],
  );

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

  // Handle drag end event
  const handleDragEnd = useCallback(
    (event) => {
      const { active, over } = event;

      if (over && active.id !== over.id) {
        const oldIndex = messages.findIndex((item) => item.id === active.id);
        const newIndex = messages.findIndex((item) => item.id === over.id);
        const newMessages = arrayMove(messages, oldIndex, newIndex);
        field.onChange(newMessages);
      }
      setActiveId(null);
    },
    [messages, field],
  );

  const handleDragStart = useCallback((event) => {
    setActiveId(event.active.id);
  }, []);

  // Add new message
  const handleAddMessage = useCallback(() => {
    const newMessages = [
      ...messages,
      {
        id: getRandomId(),
        role: "user",
        content: [{ type: "text", text: "" }],
      },
    ];
    field.onChange(newMessages);
  }, [messages, field]);

  // Remove message
  const handleRemoveMessage = useCallback(
    (id) => {
      if (messages.length <= 1) {
        enqueueSnackbar("You must have at least one message.", {
          variant: "warning",
        });
        return;
      }
      const newMessages = messages.filter((message) => message.id !== id);
      field.onChange(newMessages);
    },
    [messages, field],
  );

  // Handle role change
  const handleRoleChange = useCallback(
    (idx, newRole) => {
      const newMessages = [...messages];
      newMessages[idx] = { ...newMessages[idx], role: newRole };
      field.onChange(newMessages);
    },
    [messages, field],
  );

  // Handle content change
  const handleContentChange = useCallback(
    (idx, newContent) => {
      const newMessages = [...messages];
      newMessages[idx] = { ...newMessages[idx], content: newContent };
      field.onChange(newMessages);
    },
    [messages, field],
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
        onDragStart={handleDragStart}
      >
        <SortableContext
          items={messages.map((p) => p.id)}
          strategy={verticalListSortingStrategy}
          disabled={disabled}
        >
          {messages.map(({ id, role, content }, idx) => (
            <SortablePromptCard
              control={control}
              key={id}
              id={id}
              role={role}
              content={content}
              idx={idx}
              onRoleChange={(newRole) => handleRoleChange(idx, newRole)}
              disabled={disabled}
              onPromptChange={(newContent) =>
                handleContentChange(idx, newContent)
              }
              onRemove={() => handleRemoveMessage(id)}
              onGeneratePrompt={() =>
                setOpenGeneratePromptDrawer({ state: true, index: idx })
              }
              onImprovePrompt={() =>
                setOpenImprovePromptDrawer({ state: true, index: idx })
              }
              onCopyPrompt={() => {
                if (content && Array.isArray(content) && content.length > 0) {
                  const textContent = content
                    .filter((c) => c?.type === "text" && c?.text?.trim())
                    .map((c) => c.text)
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
              }}
              existingRoles={existingRoles}
              name={`messages.${idx}`}
              dropdownOptions={dropdownOptions}
              mentionEnabled={mentionEnabled}
              variableValidator={variableValidator}
              jinjaMode={jinjaMode}
            />
          ))}
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
                const draggedMessage = messages.find((p) => p.id === activeId);
                const draggedIndex = messages.findIndex(
                  (p) => p.id === activeId,
                );
                if (!draggedMessage) return null;

                return (
                  <PromptCard
                    role={draggedMessage.role}
                    prompt={draggedMessage.content || []}
                    index={draggedIndex}
                    onRoleChange={() => {}}
                    disabled={true}
                    onPromptChange={() => {}}
                    onRemove={() => {}}
                    showEditEmbed={false}
                    viewOptions={{
                      allowRoleChange: true,
                      allowAllRoleChange: true,
                      allowRemove: true,
                      sortable: true,
                      allowAttachment: false,
                      allowImprovePrompt: false,
                      allowGeneratePrompt: false,
                      allowSync: false,
                    }}
                    existingRoles={existingRoles}
                    expandable={true}
                    allVariablesValid={!variableValidator}
                    variableValidator={variableValidator}
                  />
                );
              })()}
            </Box>
          ) : null}
        </DragOverlay>
      </DndContext>

      {/* Display validation error */}
      {errorMessage && (
        <Typography
          variant="caption"
          sx={{
            color: "error.main",
            mt: -1,
            mb: 0.5,
            px: 1,
          }}
        >
          {errorMessage}
        </Typography>
      )}

      {showAddButton && (
        <Box sx={{ display: "flex", justifyContent: "flex-start" }}>
          <Button
            variant="outlined"
            onClick={handleAddMessage}
            size="small"
            startIcon={
              <SvgColor
                src="/assets/icons/components/ic_add.svg"
                sx={{ width: 16, height: 16 }}
              />
            }
            sx={{
              fontWeight: "fontWeightMedium",
              padding: theme.spacing(0.75, 3),
              borderRadius: theme.spacing(0.5),
              borderColor: "whiteScale.500",
              "& .MuiButton-startIcon": { marginRight: "4px" },
            }}
            disabled={disabled}
          >
            Add message
          </Button>
        </Box>
      )}

      <GeneratePromptDrawer
        allColumns={dropdownOptions?.map((v) => ({
          headerName: v?.label ?? v,
        }))}
        onApplyPrompt={handleApplyPrompt}
        promptFor={openGeneratePromptDrawer.index}
        open={openGeneratePromptDrawer.state}
        onClose={() =>
          setOpenGeneratePromptDrawer({ index: null, state: false })
        }
      />
      <ImprovePromptDrawer
        open={openImprovePromptDrawer.state}
        onClose={() =>
          setOpenImprovePromptDrawer({ index: null, state: false })
        }
        variables={dropdownOptions?.map((v) => v?.label ?? v)}
        existingPrompt={messages[openImprovePromptDrawer.index]?.content}
        onApplyPrompt={handleApplyPrompt}
        promptFor={openImprovePromptDrawer.index}
      />
    </Box>
  );
}

PromptMessageRow.propTypes = {
  control: PropTypes.object.isRequired,
  name: PropTypes.string,
  disabled: PropTypes.bool,
  showAddButton: PropTypes.bool,
  dropdownOptions: PropTypes.array,
  mentionEnabled: PropTypes.bool,
  variableValidator: PropTypes.func,
};
