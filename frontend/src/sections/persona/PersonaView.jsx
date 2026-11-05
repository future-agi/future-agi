import { Box, Typography, Button } from "@mui/material";
import React, { useState } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import GenericCard from "./GenericCard/GenericCard";
import { useGetPersonas } from "src/api/persona/persona";
import { useDebounce } from "src/hooks/use-debounce";
import PropTypes from "prop-types";
import { PERSONAS_DOCS_URL, extractTagsFromPersona } from "./common";
import PersonaCreateEditDrawer from "./PersonaCreateEdit/PersonaCreateEditDrawer";
import PersonaInfoDrawer from "./PersonaInfo/PersonaInfoDrawer";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import GenericCardSkeleton from "./GenericCard/GenericCardSkeleton";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const LoadingPersonas = () => {
  return Array.from({ length: 20 }).map((v) => <GenericCardSkeleton key={v} />);
};

const PersonaView = ({
  onCreatePersona,
  selectedPersonas,
  onToggleSelect,
  isEditable = false,
  isSelectable = false,
  personaCreateEditType,
}) => {
  const { role } = useAuthContext();
  const [searchText, setSearchText] = useState("");
  const [openPersonaInfoDrawer, setOpenPersonaInfoDrawer] = useState(false);
  const [personaInfo, setPersonaInfo] = useState({});
  const searchQuery = useDebounce(searchText.trim(), 300);
  const [selectedPersonaCategory, setSelectedPersonaCategory] = useState("");
  const {
    personas,
    totalCount,
    isFetchingNextPage: isFetchingPersonaNextPage,
    fetchNextPage,
    isPending: isPendingPersonaList,
  } = useGetPersonas(searchQuery, selectedPersonaCategory);
  const [createEditPersonaDrawerOpen, setCreateEditPersonaDrawerOpen] =
    useState({
      mode: null,
      persona: null,
      personaCreateEditType: personaCreateEditType ?? null,
    });
  const scrollContainer = useScrollEnd(() => {
    if (isPendingPersonaList || isFetchingPersonaNextPage) return;
    fetchNextPage();
  }, [fetchNextPage, isFetchingPersonaNextPage, isPendingPersonaList]);

  return (
    <Box
      sx={{
        padding: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <Typography
            color="text.primary"
            typography="m2"
            fontWeight={"fontWeightSemiBold"}
          >
            Personas
          </Typography>
          <Typography
            typography="s1"
            color="text.primary"
            fontWeight={"fontWeightRegular"}
          >
            Create and manage your personas, and easily activate or deactivate
            them to control scenario creation
          </Typography>
        </Box>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          <Button
            variant="outlined"
            size="small"
            sx={{
              color: "text.primary",
              borderColor: "divider",
              padding: 1.5,
              fontSize: "14px",
              height: "38px",
            }}
            startIcon={<SvgColor src="/assets/icons/ic_docs_single.svg" />}
            component="a"
            href={PERSONAS_DOCS_URL}
            target="_blank"
          >
            View Docs
          </Button>
          <Button
            variant="contained"
            color="primary"
            sx={{
              padding: "8px 12px",
              borderRadius: "4px",
              height: "38px",
            }}
            startIcon={
              <Iconify
                icon="octicon:plus-24"
                color="background.paper"
                sx={{
                  width: "20px",
                  height: "20px",
                }}
              />
            }
            disabled={
              !RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]
            }
            onClick={() => {
              if (onCreatePersona) {
                return onCreatePersona();
              }
              setCreateEditPersonaDrawerOpen({
                mode: "create",
                persona: null,
              });
            }}
          >
            <Typography typography="s1" fontWeight={"fontWeightMedium"}>
              Create your own persona
            </Typography>
          </Button>
        </Box>
      </Box>
      <Typography typography="s2" fontWeight="fontWeightMedium">
        All ({totalCount})
      </Typography>
      <Box display="flex" alignItems="center" gap={2}>
        <Box sx={{ width: "400px" }}>
          <FormSearchField
            placeholder="Search"
            size="small"
            searchQuery={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            fullWidth
            autoFocus
            InputProps={{}}
          />
        </Box>
        <Box sx={{ width: "400px" }}>
          <FormSearchSelectFieldState
            size="small"
            label="Persona Categories"
            options={[
              { label: "View All", value: "" },
              { label: "Prebuilt  Personas", value: "prebuilt" },
              { label: "Custom Built Personas", value: "custom" },
            ]}
            sx={{ width: "400px" }}
            value={selectedPersonaCategory}
            onChange={(e) => setSelectedPersonaCategory(e.target.value)}
          />
        </Box>
      </Box>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 2,
          overflowY: "auto",
        }}
        ref={scrollContainer}
      >
        <ShowComponent condition={isPendingPersonaList}>
          <LoadingPersonas />
        </ShowComponent>
        {personas?.map((persona) => {
          const isSelected = selectedPersonas?.some(
            (selectedPersona) => selectedPersona.id === persona.id,
          );
          return (
            <GenericCard
              key={persona.id}
              personaId={persona?.id}
              isPrebuilt={persona.isDefault}
              titleIcon="persona"
              title={persona?.name}
              description={persona?.description}
              tags={extractTagsFromPersona(persona)}
              viewOptions={{
                editable: isEditable,
                selectable:
                  isSelectable &&
                  persona?.simulationType === personaCreateEditType,
                simulationType: persona?.simulationType,
                isDrawer: isSelectable,
              }}
              selected={isSelected}
              onToggleSelect={(newValue) => onToggleSelect(persona, newValue)}
              onEditClick={() => {
                setCreateEditPersonaDrawerOpen({
                  mode: "edit",
                  persona,
                  personaCreateEditType: persona?.simulationType,
                });
              }}
              onClick={() => {
                setOpenPersonaInfoDrawer(true);
                setPersonaInfo(persona);
              }}
            />
          );
        })}
        <ShowComponent condition={personas?.length === 0}>
          <Box
            sx={{
              gridColumn: "1 / -1",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              minHeight: "300px",
            }}
          >
            <Typography
              typography="m3"
              color="text.primary"
              fontWeight="fontWeightMedium"
            >
              {selectedPersonaCategory === "custom"
                ? "You haven't created any custom personas yet"
                : selectedPersonaCategory === "prebuilt"
                  ? "You don't have any inbuilt personas"
                  : "You don't have any personas"}
            </Typography>
          </Box>
        </ShowComponent>
        <ShowComponent condition={isFetchingPersonaNextPage}>
          <LoadingPersonas />
        </ShowComponent>
        <PersonaCreateEditDrawer
          open={createEditPersonaDrawerOpen.mode !== null}
          onClose={() =>
            setCreateEditPersonaDrawerOpen({
              mode: null,
              persona: null,
              personaCreateEditType: null,
            })
          }
          updatePersonaType={(value) => {
            setCreateEditPersonaDrawerOpen({
              mode: "create",
              persona: null,
              personaCreateEditType: value,
            });
          }}
          editPersona={createEditPersonaDrawerOpen?.persona}
          personaCreateEditType={
            createEditPersonaDrawerOpen?.personaCreateEditType
          }
        />
        <PersonaInfoDrawer
          open={openPersonaInfoDrawer}
          persona={personaInfo}
          onClose={() => {
            setOpenPersonaInfoDrawer(false);
            setPersonaInfo({});
          }}
        />
      </Box>
    </Box>
  );
};

PersonaView.propTypes = {
  onCreatePersona: PropTypes.func,
  selectedPersonas: PropTypes.object,
  onToggleSelect: PropTypes.func,
  isEditable: PropTypes.bool,
  isSelectable: PropTypes.bool,
  personaCreateEditType: PropTypes.string,
  disableGenericCard: PropTypes.any,
};

export default PersonaView;
