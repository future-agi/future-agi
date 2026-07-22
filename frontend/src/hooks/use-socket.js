import { useContext } from "react";
import { WebSocketContext } from "src/components/websocket/use-socket";

export const useSocket = () => {
  return useContext(WebSocketContext) || {};
};
