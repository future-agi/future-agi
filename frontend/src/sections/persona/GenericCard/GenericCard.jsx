import { Box, Chip, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useRef } from "react";
import { ShowComponent } from "src/components/show";
import PersonTitleIcon from "../PersonTitleIcon";
import SvgColor from "src/components/svg-color";
import GenericSelection from "./GenericSelection";
import CustomTooltip from "src/components/tooltip";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import DuplicatePersonas from "../PersonaCreateEdit/DuplicatePersonas";
import PersonaCardMenu from "./PersonaCardMenu";
import { AGENT_TYPES } from "src/sections/agents/constants";

const GenericCard = ({
  personaId,
  isPrebuilt,
  titleIcon,
  title,
  description,
  tags = [],
  viewOptions = {
    selectable: false,
    editable: false,
    removable: false,
    simulationType: "",
    isDrawer: false,
  },
  selected,
  onToggleSelect,
  onEditClick,
  onClick,
  onRemove,
  showBuilt: _showBuilt = false,
}) => {
  const isSelectable = viewOptions?.selectable || false;
  const isRemovable = viewOptions.removable || false;
  const isEditable = viewOptions?.editable || false;
  const isDrawer = viewOptions?.isDrawer || false;
  const [open, setOpen] = useState(false);
  const [isDuplicate, setIsDuplicate] = useState(false);
  const anchorEl = useRef(null);

  const queryClient = useQueryClient();

  const { mutate: deletePersona, isPending: isDeleting } = useMutation({
    mutationFn: (id) => axios.delete(endpoints.persona.delete(id)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["personas"] });
      enqueueSnackbar("Persona deleted successfully", { variant: "success" });
    },
  });

  const returnVariant = (value) => {
    return value === AGENT_TYPES.VOICE ? "Voice" : "Chat";
  };

  const onDuplicateClick = (e) => {
    e.stopPropagation();
    setIsDuplicate(true);
  };

  const renderChipOfSimulationType = (simulationType = AGENT_TYPES.VOICE) => {
    const isVoice = simulationType === AGENT_TYPES.VOICE;
    return (
      <Chip
        size="small"
        icon={
          <SvgColor
            width={16}
            src={
              isVoice
                ? "/assets/icons/ic_voice.svg"
                : "/assets/icons/ic_chat_single.svg"
            }
            sx={{
              color: isVoice ? "status.info.fg" : "status.discovery.fg",
            }}
          />
        }
        label={
          simulationType === "text"
            ? "Chat"
            : simulationType?.charAt(0).toUpperCase() + simulationType?.slice(1)
        }
        sx={{
          backgroundColor: isVoice ? "status.info.bg" : "status.discovery.bg",
          color: isVoice ? "status.info.fg" : "status.discovery.fg",
          "& .MuiChip-icon": {
            color: isVoice ? "status.info.fg" : "status.discovery.fg",
          },
          "&:hover": {
            backgroundColor: isVoice ? "status.info.bg" : "status.discovery.bg",
          },
        }}
      />
    );
  };

  const handleCardClick = () => {
    if (open) return;
    onClick();
  };
  return (
    <CustomTooltip
      show={!isSelectable && isDrawer}
      title={`Please choose ${returnVariant(viewOptions?.simulationType)} type agent definition to add this persona`}
      size="small"
      type="black"
    >
      <Box
        sx={{
          borderRadius: "4px",
          border: selected ? "1px solid" : "1px solid",
          borderColor: selected ? "primary.main" : "divider",
          transition: "all 0.2s ease",
        }}
        onClick={() => {
          if (isDrawer && isSelectable) {
            onToggleSelect(!selected);

            return;
          } else {
            handleCardClick();
          }
        }}
      >
        <Box
          sx={{
            borderRadius: "4px",
            opacity: 1,
            height: "100%",
            cursor: "pointer",
          }}
        >
          <Box
            sx={{
              padding: 2,
              backgroundColor: "background.paper",
              borderRadius: "4px",
              display: "flex",
              flexDirection: "column",
              gap: "12px",
              height: "100%",
            }}
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                width: "100%",
                justifyContent: "space-between",
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: "12px",
                }}
              >
                <ShowComponent condition={titleIcon === "persona"}>
                  <PersonTitleIcon />
                </ShowComponent>
                <CustomTooltip
                  title={title}
                  show={title.length > 10}
                  placement="top"
                  size="small"
                  type="dark"
                >
                  <Typography
                    typography="s1_2"
                    fontWeight="fontWeightMedium"
                    noWrap
                    sx={{ maxWidth: 300 }}
                  >
                    {title.length > 10 ? `${title.slice(0, 8)}...` : title}
                  </Typography>
                </CustomTooltip>
                {renderChipOfSimulationType(viewOptions?.simulationType)}
              </Box>
              <ShowComponent condition={isRemovable}>
                <IconButton
                  size="small"
                  sx={{
                    color: "text.primary",
                    ml: "auto",
                  }}
                  onClick={onRemove}
                >
                  <SvgColor
                    sx={{
                      height: "20px",
                      width: "20px",
                    }}
                    src="/assets/icons/ic_close.svg"
                  />
                </IconButton>
              </ShowComponent>

              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <ShowComponent condition={isEditable}>
                  <IconButton
                    sx={{ padding: 0 }}
                    onClick={(e) => {
                      e.stopPropagation();
                      setOpen((prev) => !prev);
                    }}
                    ref={anchorEl}
                  >
                    <SvgColor
                      src="/assets/icons/persona/options-3dots.svg"
                      sx={{
                        width: "24px",
                        height: "24px",
                        color: "text.primary",
                      }}
                    />
                  </IconButton>
                </ShowComponent>

                <ShowComponent condition={isSelectable}>
                  <GenericSelection
                    selected={selected}
                    onToggleSelect={onToggleSelect}
                  />
                </ShowComponent>
              </Box>
            </Box>

            <PersonaCardMenu
              anchorEl={anchorEl.current}
              open={open}
              onClose={() => setOpen(false)}
              isPrebuilt={isPrebuilt}
              onEditClick={onEditClick}
              onDuplicateClick={onDuplicateClick}
              onViewClick={onClick}
              onDeleteClick={(e) => {
                e.stopPropagation();
                deletePersona(personaId);
              }}
              isDeleting={isDeleting}
            />

            <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
              <Typography
                typography="s1_2"
                color="text.primary"
                sx={{
                  display: "-webkit-box",
                  WebkitBoxOrient: "vertical",
                  WebkitLineClamp: 2,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "normal",
                  wordBreak: "break-word",
                }}
              >
                {description}
              </Typography>
              <ShowComponent condition={tags.length > 0}>
                <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
                  <Box
                    sx={{
                      padding: "4px 12px",
                      borderRadius: "4px",
                      color: "primary.main",
                      backgroundColor: "action.hover",
                    }}
                  >
                    <Typography typography="s2" fontWeight="fontWeightMedium">
                      {!isPrebuilt ? "Custom Built" : "Future AGI Built"}
                    </Typography>
                  </Box>

                  {tags.map((tag) => (
                    <Box
                      key={tag}
                      sx={{
                        padding: "4px 12px",
                        borderRadius: "4px",
                        border: "1px solid",
                        borderColor: "divider",
                      }}
                    >
                      <Typography typography="s2" fontWeight="fontWeightMedium">
                        {tag}
                      </Typography>
                    </Box>
                  ))}
                </Box>
              </ShowComponent>
            </Box>
          </Box>
        </Box>

        <DuplicatePersonas
          open={isDuplicate}
          onClose={() => {
            setIsDuplicate(false);
            setOpen(false);
          }}
          personaId={personaId}
        />
      </Box>
    </CustomTooltip>
  );
};

GenericCard.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
  personaId: PropTypes.string,
  isPrebuilt: PropTypes.bool,
  titleIcon: PropTypes.string,
  tags: PropTypes.array,
  viewOptions: PropTypes.shape({
    selectable: PropTypes.bool,
    editable: PropTypes.bool,
    removable: PropTypes.bool,
    simulationType: PropTypes.string,
    isDrawer: PropTypes.bool,
  }),
  selected: PropTypes.bool,
  onToggleSelect: PropTypes.func,
  onEditClick: PropTypes.func,
  onClick: PropTypes.func,
  onRemove: PropTypes.func,
  showBuilt: PropTypes.bool,
};

export default GenericCard;
