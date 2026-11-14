import { Box, Button } from "@mui/material";
import React, { useMemo } from "react";
import { useApiKeysStatus } from "src/api/model/api-keys";
import KeyCard from "src/sections/develop-detail/Common/ConfigureKeys/KeyCard";
import HeaderContent from "./HeaderContent";
import CustomModalKeyCard from "src/components/custom-model-dropdown/CustomModalKeyCard";
import CloudProviderCard from "src/components/custom-model-dropdown/CloudProviderCard";
import PropTypes from "prop-types";
import { filterAndSortProviders } from "../../../../components/custom-model-dropdown/KeysHelper";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import {
  useDeleteApiKey,
  DELETE_MODAL_TYPE,
} from "src/hooks/use-delete-api-key";

const KeySection = ({ setCurrentLabel }) => {
  const { data, isFetching } = useApiKeysStatus({});

  const { data: filteredCustomModels } = useQuery({
    queryKey: ["customModals"],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.settings.customModal.getCustomModal,
      );
      return data;
    },
    select: (d) => d?.results || [],
  });

  const defaultModelProviders = useMemo(() => {
    return filterAndSortProviders(data, "text", "");
  }, [data]);

  const cloudProviders = useMemo(() => {
    return filterAndSortProviders(data, "json", "");
  }, [data]);
  const {
    openDeleteModal: _openDeleteModal,
    setOpenDeleteModal,
    handleDeleteApiKey: _handleDeleteApiKey,
    isDeleting: _isDeleting,
  } = useDeleteApiKey();

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <HeaderContent
          title="Add Keys"
          description="This tour will guide you through the key features and functionalities"
        />
        <Button
          variant="text"
          size="small"
          sx={{ color: "text.disabled" }}
          onClick={() => setCurrentLabel("createFirstDataset")}
        >
          Skip
        </Button>
      </Box>
      <Box
        sx={{
          border: "1px solid",
          borderBottom: "0px",
          borderColor: "divider",
          borderRadius: "8px 8px 0px 0px",

          padding: "13px",
          marginTop: "20px",
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "18px 13px",
          overflow: "auto",
          height: "330px",
        }}
      >
        {defaultModelProviders?.map((d) => (
          <KeyCard
            key={d.provider}
            data={d}
            onClose={() => {}}
            isFetching={isFetching}
            onDeleteClick={() =>
              setOpenDeleteModal({
                id: d.id,
                type: DELETE_MODAL_TYPE.NORMAL,
              })
            }
          />
        ))}
        {(filteredCustomModels || [])?.map((model) => (
          <CustomModalKeyCard
            key={model.id}
            data={model}
            onDeleteClick={() =>
              setOpenDeleteModal({
                id: model?.id,
                type: DELETE_MODAL_TYPE.CUSTOM,
              })
            }
          />
        ))}
        {cloudProviders.map((provider) =>
          provider.type === "json" ? (
            <CloudProviderCard
              key={provider.provider}
              provider={provider}
              showJsonField={false}
              onDeleteClick={() =>
                setOpenDeleteModal({
                  id: provider.id,
                  type: DELETE_MODAL_TYPE.NORMAL,
                })
              }
            />
          ) : (
            <KeyCard
              key={provider.provider}
              data={provider}
              onClose={() => {}}
              isFetching={isFetching}
              onDeleteClick={() =>
                setOpenDeleteModal({
                  id: provider.id,
                  type: DELETE_MODAL_TYPE.NORMAL,
                })
              }
            />
          ),
        )}
      </Box>
    </Box>
  );
};

export default KeySection;

KeySection.propTypes = {
  setCurrentLabel: PropTypes.func,
};
