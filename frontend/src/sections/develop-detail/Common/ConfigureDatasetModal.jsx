import { zodResolver } from "@hookform/resolvers/zod";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import SvgColor from "src/components/svg-color";
import { useParams } from "src/routes/hooks";
import DeleteDataset from "src/sections/develop/DeleteDataset/DeleteDataset";
import axios, { endpoints } from "src/utils/axios";
import { z } from "zod";

const ConfigureDatasetModal = ({
  open,
  onClose,
  datasetName = "",
  title = "Configure Dataset",
}) => {
  const theme = useTheme();
  const typographyTheme = theme.typography;
  const { dataset } = useParams();
  const [openDelete, setOpenDelete] = useState(false);
  const { control, handleSubmit, reset } = useForm({
    defaultValues: { datasetName: datasetName },
    resolver: zodResolver(
      z.object({ datasetName: z.string().min(1, "Dataset name is required") }),
    ),
  });
  const queryClient = useQueryClient();
  const { mutate: updateDataset } = useMutation({
    mutationFn: (d) => axios.put(endpoints.develop.updateDataset(dataset), d),
    onSuccess: () => {
      enqueueSnackbar("Dataset name updated successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["develop", "dataset-list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      onClose();
    },
  });

  const handleUpdate = (data) => {
    updateDataset({ dataset_name: data.datasetName });
    onClose();
  };
  const handleDelete = () => {
    setOpenDelete(true);
    onClose();
  };
  return (
    <>
      <Dialog
        open={open}
        onClose={onClose}
        PaperProps={{
          sx: {
            p: theme.spacing(2),
            width: "480px",
            height: "180px",
          },
        }}
      >
        <DialogTitle
          sx={{
            p: 0,
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <Typography
            variant="m2"
            fontWeight={typographyTheme.fontWeightMedium}
          >
            {title}
          </Typography>
          <IconButton
            aria-label="close-share"
            onClick={onClose}
            sx={{
              padding: 0,
              width: "24px",
              height: "24px",
              display: "flex",
              alignItems: "center",
            }}
          >
            <Iconify icon="line-md:close" />
          </IconButton>
        </DialogTitle>
        <DialogContent
          sx={{
            p: 0,
            display: "flex",
            alignItems: "center",
          }}
        >
          <FormTextFieldV2
            onBlur={() => {}}
            helperText={""}
            defaultValue={datasetName}
            label="Dataset Name"
            size="small"
            control={control}
            placeholder="Enter dataset name"
            fieldName="datasetName"
            isSpinnerField={false}
            fullWidth
            sx={{
              ":active": {
                borderWidth: 1,
              },
            }}
          />
        </DialogContent>
        <DialogActions
          sx={{
            p: 0,
            justifyContent: "flex-end",
          }}
        >
          <Button
            size="small"
            onClick={() => {
              onClose();
              reset();
            }}
            variant="outlined"
            sx={{
              minWidth: "90px",
            }}
          >
            <Typography variant="s2">Cancel</Typography>
          </Button>
          <Button
            variant="contained"
            onClick={handleDelete}
            size="small"
            startIcon={
              <SvgColor
                sx={{
                  width: "16px",
                  height: "16px",
                }}
                src="/assets/icons/ic_delete.svg"
              />
            }
            sx={{
              bgcolor: "red.500",
              color: "common.white",
              minWidth: "90px",
              ":hover": {
                bgcolor: "red.700",
              },
            }}
          >
            <Typography variant="s2">Delete</Typography>
          </Button>
          <Button
            variant="contained"
            size="small"
            onClick={handleSubmit(handleUpdate)}
            sx={{
              bgcolor: "primary.main",
              minWidth: "90px",
              ":hover": {
                bgcolor: "primary.dark",
              },
            }}
            startIcon={
              <Iconify
                icon="grommet-icons:update"
                width={14}
                height={14}
                sx={{
                  path: {
                    strokeWidth: 1.5,
                  },
                }}
              />
            }
          >
            <Typography variant="s2">Update</Typography>
          </Button>
        </DialogActions>
      </Dialog>
      <DeleteDataset
        open={openDelete}
        onClose={() => setOpenDelete(false)}
        selected={[{ id: dataset }]}
        redirect
      />
    </>
  );
};

export default ConfigureDatasetModal;

ConfigureDatasetModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  datasetName: PropTypes.string,
  title: PropTypes.string,
};
