import { useContext } from "react";
import SelectedNodeContext from "./selectedNodeContext";

export const useSelectedNode = () => useContext(SelectedNodeContext);
