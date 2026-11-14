export const formatISOCustom = (date: string | number | Date) => {
  return new Date(date).toISOString();
};
