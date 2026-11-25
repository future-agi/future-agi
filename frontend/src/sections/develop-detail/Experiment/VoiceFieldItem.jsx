import React, { useEffect } from "react";
import PropTypes from "prop-types";
import FieldSelection from "src/sections/develop-detail/Common/FieldSelection";
import { useVoiceOptions } from "src/api/develop/develop-detail";
import { ShowComponent } from "src/components/show";
import HelperText from "../Common/HelperText";

export default function VoiceFieldItem({
  model,
  modelIndex,
  index,
  control,
  setValue,
  useWatch,
}) {
  const fieldName = `promptConfig.${index}.voice.${modelIndex}.voices`;
  const currentValue = useWatch({
    control,
    name: fieldName,
  });

  const { data: voiceOptions, isLoading: loadingVoices } = useVoiceOptions({
    model: model?.value,
    enabled: true,
  });

  useEffect(() => {
    if (voiceOptions?.default && !currentValue?.length) {
      const timeoutId = setTimeout(() => {
        setValue(fieldName, [voiceOptions.default], {
          shouldDirty: true,
          shouldValidate: true,
          shouldTouch: true,
        });
      }, 0);

      return () => clearTimeout(timeoutId);
    }
  }, [voiceOptions?.default, currentValue, fieldName, setValue]);

  return (
    <div key={model?.value}>
      <FieldSelection
        field={model?.value}
        fieldName={fieldName}
        allColumns={
          voiceOptions?.voices?.map((voice) => ({
            headerName: voice?.label,
            field: voice?.value,
          })) || []
        }
        control={control}
        fullWidth
        isMultipleColumn={true}
        check={undefined}
        placeholder="Choose Voice"
        isChecked={false}
        handleCheckbox={undefined}
        dropdownLabel="Voice"
        checkbox
      />
      <ShowComponent
        condition={model && !voiceOptions?.voices?.length && !loadingVoices}
      >
        <HelperText
          sx={{
            color: "status.danger.fg",
            typography: "s2",
          }}
          text="Voice support isn't available for the selected model."
        />
      </ShowComponent>
    </div>
  );
}

VoiceFieldItem.propTypes = {
  model: PropTypes.string,
  modelIndex: PropTypes.number,
  index: PropTypes.number,
  control: PropTypes.object,
  setValue: PropTypes.func,
  useWatch: PropTypes.func,
};
