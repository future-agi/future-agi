import PropTypes from "prop-types";

import { ENV_SHAPE, ENV_STATE_SHAPE } from "../../workspace/overview/overview.constants";
import ChatSplitPane from "../../components/ChatSplitPane";
import BuilderConsole from "../console/BuilderConsole";
import PanelBoundary from "./PanelBoundary";
import BuildingPane from "./BuildingPane";

/**
 * The two-pane building stage.
 *
 * Chat narrow, artifacts wide — the shape Lovable, Figma Make and v0 all landed
 * on for the same reason: the input is a column of text and the output is a
 * whole surface. The console drives the build on the left; the right pane shows
 * the engine working, guarded by a component-scoped error boundary so a bad
 * derived shape can't take the chat down with it.
 *
 * `progress` is the `useBuildProgress` return; it may be null mid-init, so we
 * null-guard it and let the panes fall through to their own defaults.
 *
 * The right pane's pipeline animation is driven by `progress`; the left console
 * is the REAL builder chat (`chat`, from useWorkspaceChat) so the user can talk
 * to the active ALK run and watch its reading events while it authors. `chat`
 * falls back to `progress` when absent (e.g. a mock-only build).
 */
export default function BuildingStage({ progress, chat, env, envState, patch, primed, source, world }) {
  const p = progress || {};
  const console_ = chat || p;
  return (
    <ChatSplitPane
      busy={!!(console_.running || console_.inFlight || console_.waiting)}
      chat={({ collapse, open, collapseRef }) => (
        <BuilderConsole
          turns={console_.turns}
          running={console_.running}
          onSend={console_.send}
          onStop={console_.stop}
          canStop={console_.inFlight}
          frozen={console_.frozen}
          frozenReason={console_.frozenReason}
          onCollapse={collapse}
          collapseRef={collapseRef}
          active={open}
        />
      )}
    >
      <PanelBoundary>
        <BuildingPane
          done={p.done}
          running={p.running}
          failure={p.failure}
          env={env}
          envState={envState}
          patch={patch}
          primed={primed}
          source={source}
          world={world}
        />
      </PanelBoundary>
    </ChatSplitPane>
  );
}

BuildingStage.propTypes = {
  progress: PropTypes.shape({
    done: PropTypes.arrayOf(PropTypes.string),
    running: PropTypes.bool,
    failure: PropTypes.shape({
      stepId: PropTypes.string,
      title: PropTypes.string,
      detail: PropTypes.string,
      retryable: PropTypes.bool,
    }),
    turns: PropTypes.array,
    send: PropTypes.func,
  }),
  chat: PropTypes.shape({
    turns: PropTypes.array,
    running: PropTypes.bool,
    send: PropTypes.func,
    stop: PropTypes.func,
    inFlight: PropTypes.bool,
    waiting: PropTypes.bool,
    frozen: PropTypes.bool,
    frozenReason: PropTypes.string,
  }),
  env: ENV_SHAPE,
  envState: ENV_STATE_SHAPE,
  patch: PropTypes.func,
  primed: PropTypes.bool,
  source: PropTypes.string,
  world: PropTypes.object,
};
