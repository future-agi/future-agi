import React from "react";
import PropTypes from "prop-types";
import { Controller, useFormContext } from "react-hook-form";

import FormHelperText from "@mui/material/FormHelperText";

import { Upload, UploadBox, UploadAvatar } from "../upload";

// ----------------------------------------------------------------------

export function RHFUploadAvatar({ name, ...other }) {
  const { control } = useFormContext();

  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState: { error } }) => (
        <div>
          <UploadAvatar error={!!error} file={field.value} {...other} />

          {!!error && (
            <FormHelperText error sx={{ px: 2, textAlign: "center" }}>
              {error.message}
            </FormHelperText>
          )}
        </div>
      )}
    />
  );
}

RHFUploadAvatar.propTypes = {
  name: PropTypes.string,
};

// ----------------------------------------------------------------------

export function RHFUploadBox({ name, ...other }) {
  const { control } = useFormContext();

  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState: { error } }) => (
        <UploadBox files={field.value} error={!!error} {...other} />
      )}
    />
  );
}

RHFUploadBox.propTypes = {
  name: PropTypes.string,
};

// ----------------------------------------------------------------------

export function RHFUpload({
  name,
  showIcon,
  multiple,
  helperText,
  uploadProgress,
  control,
  ...other
}) {
  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState: { error } }) =>
        multiple ? (
          <Upload
            showIcon={showIcon}
            multiple
            accept={{ "image/*": [] }}
            files={field.value}
            error={!!error}
            helperText={
              (!!error || helperText) && (
                <FormHelperText error={!!error} sx={{ px: 2 }}>
                  {error ? error?.message : helperText}
                </FormHelperText>
              )
            }
            {...other}
          />
        ) : (
          <Upload
            showIcon={showIcon}
            accept={{ "image/*": [] }}
            file={field.value}
            error={!!error}
            uploadProgress={uploadProgress}
            helperText={
              (!!error || helperText) && (
                <FormHelperText error={!!error} sx={{ px: 2, m: 0 }}>
                  {error ? error?.message : helperText}
                </FormHelperText>
              )
            }
            {...other}
          />
        )
      }
    />
  );
}

RHFUpload.propTypes = {
  helperText: PropTypes.string,
  multiple: PropTypes.bool,
  name: PropTypes.string,
  control: PropTypes.object,
  showIcon: PropTypes.bool,
  uploadProgress: PropTypes.number,
};
