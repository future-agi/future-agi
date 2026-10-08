import { ReactQueryDevtools } from "@tanstack/react-query-devtools";

/** Keep the developer control away from app pagination controls. */
export default function ObserveQueryDevtools() {
  return (
    <ReactQueryDevtools initialIsOpen={false} buttonPosition="top-right" />
  );
}
