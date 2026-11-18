import { Box, Skeleton } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useSingleImageViewContext } from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageContext";
import { useRowHover } from "src/hooks/use-row-hover";
import AudioCellRenderer from "../../common/DevelopCellRenderer/CellRenderers/AudioCellRenderer";
import { DataTypes } from "../../common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import FloatIntegerCellRenderer from "../../common/DevelopCellRenderer/CellRenderers/FloatIntegerCellRenderer";
import DatetimeCellRenderer from "../../common/DevelopCellRenderer/CellRenderers/DatetimeCellRenderer";
import JsonCellRenderer from "../../common/DevelopCellRenderer/CellRenderers/JsonCellRenderer";
import TextArrayJsonCellRenderer from "../../common/DevelopCellRenderer/CellRenderers/TextArrayJsonCellRenderer";
import ImageCellRenderer from "../../common/DevelopCellRenderer/CellRenderers/ImageCellRenderer";
import FileCellRenderer from "../../common/DevelopCellRenderer/CellRenderers/FileCellRenderer";
import PersonaCellRenderer from "src/sections/common/DevelopCellRenderer/CellRenderers/PersonaCellRenderer";

const ScenarioCellRenderer = (props) => {
  const dataType = props?.value?.data_type;
  const value = props?.value?.value;
  const column = props?.column;
  const cellData = props?.data?.[props?.column?.colId];
  const { setImageUrl } = useSingleImageViewContext();
  const { isRowHovered, cellRef } = useRowHover(props);
  const formattedValueReason = () => "";

  if (dataType === DataTypes.AUDIO) {
    const cacheKey = `wavesurfer-${column.id}-${value}`;
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          flexGrow: 1,
        }}
      >
        <AudioCellRenderer
          value={value}
          editable={false}
          cacheKey={cacheKey}
          getWaveSurferInstance={column?.getWaveSurferInstance}
          storeWaveSurferInstance={column?.storeWaveSurferInstance}
          updateWaveSurferInstance={column?.updateWaveSurferInstance}
          onEditCell={() => {}}
          onCellValueChanged={() => {}}
          params={props}
        />
      </Box>
    );
  }

  switch (dataType) {
    case DataTypes.FLOAT:
    case DataTypes.INTEGER:
      return (
        <FloatIntegerCellRenderer
          value={value}
          valueReason={""}
          formattedValueReason={formattedValueReason}
          originType="OTHERS"
          metadata={{}}
          valueInfos={{}}
        />
      );

    case DataTypes.DATETIME:
      return (
        <DatetimeCellRenderer
          value={value}
          valueReason=""
          formattedValueReason={formattedValueReason}
          originType="OTHERS"
          metadata={{}}
        />
      );
    case DataTypes.JSON:
      return (
        <Box ref={cellRef} sx={{ height: "100%" }}>
          <JsonCellRenderer
            isHover={isRowHovered}
            value={value}
            valueReason=""
            formattedValueReason={formattedValueReason}
            originType="OTHERS"
            metadata={{}}
            valueInfos={{}}
          />
        </Box>
      );
    case DataTypes.TEXT:
    case DataTypes.ARRAY:
    case DataTypes.BOOLEAN:
      return (
        <Box ref={cellRef} sx={{ height: "100%" }}>
          <TextArrayJsonCellRenderer
            isHover={isRowHovered}
            value={value}
            valueReason=""
            formattedValueReason={formattedValueReason}
            originType="OTHERS"
            metadata={{}}
            valueInfos={{}}
            cellData={cellData}
          />
        </Box>
      );

    case DataTypes.IMAGE:
      return (
        <ImageCellRenderer
          value={value}
          editable={false}
          valueReason=""
          formattedValueReason={formattedValueReason}
          originType="OTHERS"
          metadata={{}}
          setImageUrl={setImageUrl}
          onEditCell={() => {}}
          params={props}
          onCellValueChanged={() => {}}
        />
      );

    case DataTypes.FILE:
      return (
        <FileCellRenderer
          value={value}
          editable={false}
          valueReason=""
          formattedValueReason={formattedValueReason}
          originType="OTHERS"
          metadata={{}}
          setImageUrl={setImageUrl}
          onEditCell={() => {}}
          params={props}
          onCellValueChanged={() => {}}
          valueInfos={{}}
        />
      );
    case DataTypes.PERSONA:
      return <PersonaCellRenderer value={value} />;
  }
};

ScenarioCellRenderer.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
  data: PropTypes.object,
};

export const LoadingHeader = () => (
  <Skeleton variant="text" width={100} height={20} />
);

export default ScenarioCellRenderer;
