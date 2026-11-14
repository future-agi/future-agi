export const getUploadSourceItems = (variables = {}) => {
  if (variables?.files?.length) return variables.files;
  return variables?.links || [];
};

export const getUploadedMediaName = (item, sourceItem) =>
  item?.file_name || item?.fileName || sourceItem?.name;

export const mapUploadedMedia = ({
  uploadedUrl = [],
  sourceItems = [],
  type,
}) =>
  uploadedUrl.reduce((acc, item, index) => {
    if (!item.url) return acc;
    const sourceItem = sourceItems[index] || {};
    const uploadedName = getUploadedMediaName(item, sourceItem);
    if (type === "image") {
      acc.push({
        url: item.url,
        img_name: uploadedName,
        img_size: sourceItem.size,
      });
    }
    if (type === "audio") {
      acc.push({
        url: item.url,
        audio_name: uploadedName,
        audio_size: sourceItem.size,
        audio_type: sourceItem.type,
      });
    }
    if (type === "pdf") {
      acc.push({
        url: item.url,
        pdf_name: uploadedName,
        pdf_size: sourceItem.size,
      });
    }
    return acc;
  }, []);
