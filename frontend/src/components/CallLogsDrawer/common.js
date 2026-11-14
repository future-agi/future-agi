export const getCsatScoreColor = (n) => {
  if (n <= 5) return "status.danger.solid";
  if (n <= 7) return "status.warning.solid";
  return "status.success.solid";
};
