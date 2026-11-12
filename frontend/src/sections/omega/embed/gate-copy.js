// The words on the gate panel for each state. Consumers can override any of
// them through EmbeddedSurface's `copy` prop.
import { diagnosisCopy } from "./diagnosis";
import { EMBED_STATUS } from "./use-embed-session";

const capitalize = (text) =>
  text ? text[0].toUpperCase() + text.slice(1) : text;

export function gateCopy(status, { label, diagnosis, copy = {} }) {
  switch (status) {
    case EMBED_STATUS.needsKey:
      return (
        copy.needsKey || {
          title: `${capitalize(label)} needs to be reconnected`,
          body: "This connection has no key yet. Open the link again, or reconnect.",
        }
      );
    case EMBED_STATUS.refused:
      return (
        copy.refused || {
          title: `${capitalize(label)} refused the connection`,
          body: "The key was not accepted. It may have expired; get a new link and open it again.",
        }
      );
    case EMBED_STATUS.timeout: {
      const cause = diagnosis?.cause;
      const override = copy.diagnosis?.(cause, {
        label,
        origin: diagnosis?.origin,
      });
      return (
        override || diagnosisCopy(cause, { label, origin: diagnosis?.origin })
      );
    }
    default:
      return null;
  }
}
