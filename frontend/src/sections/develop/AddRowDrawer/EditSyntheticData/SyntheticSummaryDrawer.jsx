import React, { useCallback, useMemo } from "react";
import {
  Box,
  Button,
  DialogContent,
  Divider,
  Drawer,
  IconButton,
  Skeleton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { useEditSyntheticDataStore } from "./state";
import SvgColor from "../../../../components/svg-color";
import PropTypes from "prop-types";
import EachColumnSummary from "../CreateSyntheticData/Summary/EachColumnSummary";
import { transformColumnPayload } from "../CreateSyntheticData/common";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useNavigate, useParams } from "react-router";
import { ShowComponent } from "src/components/show";
import { enqueueSnackbar } from "notistack";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import { useDatasetOriginStore } from "../../../develop-detail/states";
import CustomDialog from "../../../develop-detail/Common/CustomDialog/CustomDialog";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";

const titleProps = {
  variant: "s1",
  fontWeight: "fontWeightMedium",
  color: "text.primary",
};
const valueProps = {
  variant: "s1",
  fontWeight: "fontWeightRegular",
  color: "text.secondary",
};

const SyntheticSummary = ({ summaryData = {}, onClose, isLoading }) => {
  const theme = useTheme();
  const {
    dataset = {},
    num_rows: rowNumber = "",
    kb_id: kbId = "",
    columns = [],
  } = summaryData;

  const { data: knowledgeBaseList } = useKnowledgeBaseList("", null);

  const knowledgeBaseOptions = useMemo(
    () =>
      (knowledgeBaseList || []).map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [knowledgeBaseList],
  );

  const selectedKB = useMemo(() => {
    if (kbId) {
      return knowledgeBaseOptions.find((item) => item.value === kbId)?.label;
    }
    return "";
  }, [knowledgeBaseOptions, kbId]);

  const {
    name = "",
    description = "",
    objective: useCase = "",
    patterns: pattern = "",
  } = dataset;

  const transformedColumns = useMemo(() => {
    return transformColumnPayload(columns);
  }, [columns]);

  if (isLoading) {
    return (
      <Stack
        direction={"column"}
        sx={{
          padding: theme.spacing(1.5, 2),
        }}
        gap={2}
      >
        <Stack
          direction={"row"}
          sx={{
            width: "100%",
          }}
          justifyContent={"space-between"}
        >
          <Skeleton
            animation="pulse"
            variant="rounded"
            height={"20px"}
            width={"120px"}
          />
          <Skeleton
            animation="pulse"
            variant="rounded"
            height={"20px"}
            width={"40px"}
          />
        </Stack>
        <Stack gap={1.5}>
          <Skeleton animation="pulse" variant="rounded" height={200} />
          <Skeleton animation="pulse" variant="rounded" height={200} />
          <Skeleton animation="pulse" variant="rounded" height={200} />
        </Stack>
      </Stack>
    );
  }

  return (
    <Box sx={{ padding: theme.spacing(1.5, 2) }}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: theme.spacing(2) }}
      >
        <Typography
          variant="m3"
          color="text.primary"
          fontWeight="fontWeightMedium"
        >
          Synthetic Data Details
        </Typography>
        <IconButton onClick={onClose}>
          <SvgColor
            sx={{ height: 24, width: 24, color: "text.primary" }}
            src="/assets/icons/ic_close.svg"
          />
        </IconButton>
      </Stack>

      <Stack direction={"column"} gap={1.5}>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            backgroundColor: "background.neutral",
            border: "1px solid",
            borderColor: "divider",
            padding: "16px",
            borderRadius: "8px",
          }}
        >
          {name && (
            <FieldBlock
              label="Name"
              value={name}
              titleProps={titleProps}
              valueProps={valueProps}
            />
          )}
          {selectedKB && (
            <FieldBlock
              label="Knowledge Base"
              value={selectedKB}
              titleProps={titleProps}
              valueProps={valueProps}
            />
          )}
          {description && (
            <FieldBlock
              label="Description"
              value={description}
              titleProps={titleProps}
              valueProps={valueProps}
            />
          )}
          {useCase && (
            <FieldBlock
              label="Objective"
              value={useCase}
              titleProps={titleProps}
              valueProps={valueProps}
            />
          )}
          {pattern && (
            <FieldBlock
              label="Pattern"
              value={pattern}
              titleProps={titleProps}
              valueProps={valueProps}
            />
          )}
          {rowNumber && (
            <Box sx={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              <Typography {...titleProps}>No. of rows</Typography>
              <Typography {...valueProps}>{rowNumber}</Typography>
            </Box>
          )}
        </Box>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: "16px",
          }}
        >
          {transformedColumns?.map((item, index) => {
            return <EachColumnSummary key={index} data={item} index={index} />;
          })}
        </Box>
      </Stack>
    </Box>
  );
};

const FieldBlock = ({ label, value, titleProps, valueProps }) => (
  <Box sx={{ display: "flex", flexDirection: "column", gap: "16px" }}>
    <Box sx={{ display: "flex", flexDirection: "column", gap: "4px" }}>
      <Typography {...titleProps}>{label}</Typography>
      <Typography {...valueProps}>{value}</Typography>
    </Box>
    <Divider orientation="horizontal" sx={{ borderColor: "divider" }} />
  </Box>
);

FieldBlock.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  titleProps: PropTypes.object.isRequired,
  valueProps: PropTypes.object.isRequired,
};

SyntheticSummary.propTypes = {
  summaryData: PropTypes.object,
  onClose: PropTypes.func,
  isLoading: PropTypes.bool,
};

export default function SyntheticSummaryDrawer() {
  const { dataset } = useParams();
  const { refreshGrid } = useDevelopDetailContext();
  const queryClient = useQueryClient();
  const {
    openSummaryDrawer,
    setOpenSummaryDrawer,
    openEditDrawer: _openEditDrawer,
    setOpenEditDrawer,
    setOpenConfirmEdit,
    failedToGenerateData,
    openConfirmEdit,
  } = useEditSyntheticDataStore();
  const navigate = useNavigate();
  const { processingComplete } = useDatasetOriginStore();

  const { data: tableData } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", { enabled: false }),
  );

  const { data, isLoading } = useQuery({
    queryKey: [
      dataset,
      tableData?.data?.result?.synthetic_dataset,
      openSummaryDrawer,
    ],
    queryFn: () => axios.get(endpoints.develop.getSyntheticConfig(dataset)),
    enabled: !!dataset && !!tableData?.data?.result?.synthetic_dataset,
    select: (d) => d.data?.result?.data,
  });

  const { mutate: updateSyntheticData, isPending: isUpdating } = useMutation({
    mutationFn: (data) => {
      return axios.put(endpoints.develop.updateSyntheticDataset(dataset), data);
    },
    onSuccess: (res) => {
      refreshGrid();
      setOpenSummaryDrawer(false);
      enqueueSnackbar(res?.data?.result?.message || "Dataset updated", {
        variant: "success",
      });
      setTimeout(() => {
        navigate(
          `/dashboard/develop/${res?.data?.result?.data?.datasetId}?tab=data`,
          {
            replace: true,
          },
        );
      }, 0);
    },
  });

  const handleRegenerate = useCallback(() => {
    if (!dataset) return;
    updateSyntheticData({ ...data, regenerate: true });
  }, [data, dataset, updateSyntheticData]);

  const _onEditSuccessCallback = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: ["develop", "dataset-name-list"], // replace with your key
    });
    if (refreshGrid) {
      refreshGrid();
    }
  }, [queryClient, refreshGrid]);

  const handleConfirmEdit = useCallback(() => {
    setOpenConfirmEdit(false);
    setOpenSummaryDrawer(false);
    // setOpenEditDrawer(true);
    navigate(
      `/dashboard/develop/edit-synthetic-dataset/${dataset}?editMode=true`,
    );

    enqueueSnackbar("Synthetic Data generation has been terminated", {
      variant: "error",
    });
  }, [setOpenConfirmEdit, setOpenEditDrawer, setOpenSummaryDrawer]);

  return (
    <>
      <Drawer
        anchor="right"
        open={openSummaryDrawer}
        onClose={() => setOpenSummaryDrawer(false)}
        PaperProps={{
          sx: {
            height: "100vh",
            position: "fixed",
            zIndex: 10,
            boxShadow: "-10px 0px 100px #00000035",
            borderRadius: "0px !important",
            backgroundColor: "background.paper",
            minWidth: "400px",
            maxWidth: "500px",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: {
              backgroundColor: "transparent",
              borderRadius: "0px !important",
            },
          },
        }}
      >
        <Box
          sx={{
            position: "relative",
            height: "100vh",
          }}
        >
          <SyntheticSummary
            onClose={() => setOpenSummaryDrawer(false)}
            summaryData={data}
            isLoading={isLoading}
          />
          <Stack
            sx={{
              mb: 2,
              position: "sticky",
              bottom: 0,
              left: 0,
              right: 0,
              backgroundColor: "background.paper",
              padding: 2,
            }}
            direction={"row"}
            justifyContent={"flex-end"}
            gap={2}
          >
            <ShowComponent condition={Boolean(failedToGenerateData)}>
              <Button
                disabled={isUpdating}
                onClick={handleRegenerate}
                startIcon={
                  <SvgColor
                    sx={{
                      height: 16,
                      width: 16,
                    }}
                    src="/assets/icons/ic_reload.svg"
                  />
                }
                size="small"
                color="primary"
                variant="contained"
              >
                Re-Generate same Configuration
              </Button>
            </ShowComponent>
            <Button
              onClick={() => {
                if (processingComplete) {
                  setOpenSummaryDrawer(false);
                  // setOpenEditDrawer(true);
                  navigate(
                    `/dashboard/develop/edit-synthetic-dataset/${dataset}?editMode=true`,
                  );
                } else {
                  setOpenConfirmEdit(true);
                }
              }}
              startIcon={
                <SvgColor
                  sx={{
                    height: 16,
                    width: 16,
                  }}
                  src="/assets/icons/ic_edit.svg"
                />
              }
              size="small"
              color="primary"
              variant="contained"
            >
              Edit Configuration
            </Button>
          </Stack>
        </Box>
      </Drawer>
      {/* <EditSyntheticDataDrawer
        open={openEditDrawer}
        onClose={() => setOpenEditDrawer(false)}
        onEditSuccessCallback={onEditSuccessCallback}
        editData={data}
      /> */}
      <CustomDialog
        open={openConfirmEdit}
        onClose={() => {
          setOpenConfirmEdit(false);
        }}
        title={"Edit Synthetic Data"}
        actionButton={"Edit Synthetic Data"}
        color="error"
        actionStartIcon={
          <SvgColor
            sx={{
              height: 16,
              width: 16,
            }}
            src="/assets/icons/ic_edit.svg"
          />
        }
        titleProps={{
          variant: "m3",
          color: "text.primary",
          fontWeight: "fontWeightBold",
        }}
        onClickAction={handleConfirmEdit}
      >
        <DialogContent
          sx={{
            padding: 0,
            mt: 0.25,
          }}
        >
          <Typography
            color={"text.primary"}
            typography="s1"
            fontWeight={"fontWeightRegular"}
          >
            Are you sure you want to edit synthetic data? This will terminate
            the current synthetic data generation.
          </Typography>
        </DialogContent>
      </CustomDialog>
    </>
  );
}
