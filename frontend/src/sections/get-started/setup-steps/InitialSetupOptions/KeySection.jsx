import { Box, Button } from "@mui/material";
import React, { useMemo } from "react";
import { useApiKeysStatus } from "src/api/model/api-keys";
import KeyCard from "src/sections/develop-detail/Common/ConfigureKeys/KeyCard";
import HeaderContent from "./HeaderContent";
import CustomModalKeyCard from "src/components/custom-model-dropdown/CustomModalKeyCard";
import CloudProviderCard from "src/components/custom-model-dropdown/CloudProviderCard";
import PropTypes from "prop-types";
import { filterAndSortProviders } from "../../../../components/custom-model-dropdown/KeysHelper";
import {
  useDeleteApiKey,
  DELETE_MODAL_TYPE,
} from "src/hooks/use-delete-api-key";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import SvgColor from "src/components/svg-color";
import ModelRoutes from "../../ModelRoutes";
import { useCustomModels } from "../../useModelRoutes";

const KeySection = ({ setCurrentLabel }) => {
  const { data, isFetching } = useApiKeysStatus({});

  const { data: filteredCustomModels } = useCustomModels();

  const defaultModelProviders = useMemo(() => {
    return filterAndSortProviders(data, "text", "");
  }, [data]);

  const cloudProviders = useMemo(() => {
    return filterAndSortProviders(data, "json", "");
  }, [data]);
  const {
    openDeleteModal,
    setOpenDeleteModal,
    handleDeleteApiKey,
    isDeleting,
  } = useDeleteApiKey();

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <HeaderContent
          title="Add provider keys"
          description="Add a key for each model provider you use so your prompts and experiments can call its models."
        />
        <Button
          variant="text"
          size="small"
          sx={{ color: "text.secondary" }}
          onClick={() => setCurrentLabel("createFirstDataset")}
        >
          Skip
        </Button>
      </Box>
      <ModelRoutes
        inKeyCards
        sentence="Your organization has Future AGI credits, so there are two ways to call models."
      />
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
      <ConfirmDialog
        content="Are you sure you want to delete this API key?"
        action={
          <LoadingButton
            loading={isDeleting}
            size="small"
            variant="contained"
            color="error"
            onClick={() => handleDeleteApiKey()}
            sx={{ color: "common.white" }}
            startIcon={
              <SvgColor
                // @ts-ignore
                width={2}
                sx={{ mt: -0.5 }}
                src={"/assets/icons/ic_delete.svg"}
              />
            }
          >
            Delete
          </LoadingButton>
        }
        open={!!openDeleteModal}
        onClose={() => setOpenDeleteModal(null)}
        title="Delete API key"
      />
    </Box>
  );
};

export default KeySection;

KeySection.propTypes = {
  setCurrentLabel: PropTypes.func,
};
