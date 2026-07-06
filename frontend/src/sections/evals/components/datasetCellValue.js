export const unwrapCellValue = (cell) => {
  if (cell && typeof cell === "object" && "cell_id" in cell) {
    return cell.cell_value ?? "";
  }
  return cell ?? "";
};
