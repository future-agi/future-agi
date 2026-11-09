"""Readiness of the parts of the hosted Omega products: whether one control works for a real user
today. A part that does not work is hidden, by its route and its API as well as in the UI, never
shown as "soon", "preview" or "unavailable" (owner, 2026-10-19).

Products themselves are not hidden here. Rollout (decision.py, service.py) decides who sees a product.
The owner's ruling of 2026-10-19 16:50 (program/OWNER-RULINGS-20261019.md, #4) keeps the Studio tab
and the Hub visible and has them finished instead; only broken controls inside a product are hidden.

Release facts the server cannot observe are declared by the operator at deploy time, by the same
release that publishes the Omega CLI, in `OMEGA_RELEASED_PARTS` (a Django setting, else the
environment; a comma list or a list):
- `hub_cli`: a customer can install an Omega CLI with `omega hub add/run/login/publish` (the Hub's
  "Use this" CLI item, its terminal publish block and Hub tokens);
- `deploy_cli`: a customer can install an Omega CLI with `omega deploy` (the Deployments "Use the
  CLI" source);
- `deployment_studio`: a deployed server opens inside the Studio tab: it lets the Future AGI origin
  frame it and Studio attaches to it (DS:W4-FA-STUDIO-ATTACH, READINESS E16). Until then "Open in
  Studio" fails every time (console-reality audit, deployments row 1).

The parity program's hide switches (PARITY-PROGRAM.md, lane P22). Each ships off, in its hidden or
honest state, and the lane that closes its gap has the release declare it once that lane is accepted
(round 7 decides which ship); the frontend words both states in sections/omega/shell/switches.js:
- `deploy_no_downtime` (P19): a revision switch keeps the serving revision up until the next one is
  healthy, so the redeploy and rollback notes stop saying "about 3 seconds without service";
- `deploy_langgraph_js` (P23): an unmodified LangGraph.js project deploys, so Deployments stops saying
  "Omega projects (JS/TS)" only;
- `sdk_python`, `sdk_typescript` (SDK publish): the package installs from its public registry, so
  the code panels offer its tab next to curl;
- `hosted_sandboxes`, `hosted_deployments` (parity Q1, then the hosted lanes): on hosted Future AGI
  (DeploymentLocation.CLOUD) the product is withheld from every org until declared
  (service._hosted_withheld). Self-hosted installs ignore these two.
The managed learning service's parts (learning-service PLAN section 2; rulings 157.3, 168 OD1 and
377: every new surface ships off, phase 1 never reaches the team build or a release):
- `agent_observe` (P1-08): each Console agent gets its own Observe project
  (`POST/DELETE /tracer/omega/agents/<id>/observe/`). Off, that route answers 404 like a dark
  product and writes nothing.
- `learning_curves` (P1-09): a Console agent's cost, latency and outcome curves from its Observe
  project (`GET /tracer/omega/agents/<id>/curves/`). Off, that route answers 404 like a dark
  product and reads nothing. PLAN section 2 names the same switch for the Console's curve
  surfaces (EE, P1-04).
Two of the program's switches are not parts, because a fact decides them more truly than a flag: a
deployment reads "Live" only when EE says its URL is reachable from outside its host (`urlReach`,
frontend deployments/lib/reach.js; P09 makes it so), and the Studio tab shows its install step once
the release names the package that runs `omega dev` (`VITE_OMEGA_CLI_PACKAGE`, set by P03 at
publish).
Unknown names are ignored and logged, so a typo never opens anything. The Omega server (EE) reads the
same variable for its own switches (PEER_PARTS); those names are skipped here without a log line.

A fact the server observes itself:
- `hub_publish`: an effective Hub checker token is configured (`omega_hub.conf.checker_token`).
  Standalone and Distributed require the Compose `hub` profile (or `*`); a generated secret
  cannot enable uploads while its checker service is disabled. Blank and published placeholder
  tokens are not configuration. Without an effective token a submission would wait forever on
  "checks start in a moment". Runtime liveness remains HUB-OPS's responsibility (READINESS L13,
  "a dead checker is silent"); the Hub records no idle poll this module could read.
  This one is a configuration error, not an unfinished control, so it hides nothing (main-session
  ruling 3, 2026-10-21: "DON'T hide Studio/Hub, finish them"): the Publish page stays and says
  what is missing (the operator's message to an organization owner or admin, "ask your
  administrator" to anyone else), and an upload is refused by intake with 503
  OMEGA_HUB_CHECKER_NOT_CONFIGURED, nothing stored.

The UI reads each part through `/api/capabilities/` as `omega_part_<part>`
(tfc/capabilities/registry.py); the Hub API refuses a declared part's writes through
`hub_gate.require_hub_part`.
"""

from __future__ import annotations

import os

import structlog
from django.conf import settings

from omega_rollout import products as p

logger = structlog.get_logger(__name__)

SETTING = "OMEGA_RELEASED_PARTS"

HUB_CLI = "hub_cli"
DEPLOY_CLI = "deploy_cli"
DEPLOYMENT_STUDIO = "deployment_studio"

# The parity program's hide switches (P22).
DEPLOY_NO_DOWNTIME = "deploy_no_downtime"
DEPLOY_LANGGRAPH_JS = "deploy_langgraph_js"
SDK_PYTHON = "sdk_python"
SDK_TYPESCRIPT = "sdk_typescript"
HOSTED_SANDBOXES = "hosted_sandboxes"
HOSTED_DEPLOYMENTS = "hosted_deployments"
SWITCH_PARTS: tuple[str, ...] = (
    DEPLOY_NO_DOWNTIME,
    DEPLOY_LANGGRAPH_JS,
    SDK_PYTHON,
    SDK_TYPESCRIPT,
    HOSTED_SANDBOXES,
    HOSTED_DEPLOYMENTS,
)

# The hosted product each hosted switch offers (service._hosted_withheld).
HOSTED_PARTS: dict[str, str] = {
    p.SANDBOXES: HOSTED_SANDBOXES,
    p.DEPLOYMENTS: HOSTED_DEPLOYMENTS,
}

# The managed learning service's parts (learning-service PLAN section 2).
AGENT_OBSERVE = "agent_observe"
LEARNING_CURVES = "learning_curves"
LEARNING_PARTS: tuple[str, ...] = (AGENT_OBSERVE, LEARNING_CURVES)

DECLARED_PARTS: tuple[str, ...] = (
    HUB_CLI,
    DEPLOY_CLI,
    DEPLOYMENT_STUDIO,
    *SWITCH_PARTS,
    *LEARNING_PARTS,
)

# Switches the Omega server (EE) reads from the same OMEGA_RELEASED_PARTS (its release-switches
# module): known names, so they are not logged as typos here, and never ready here.
PEER_PARTS: tuple[str, ...] = (
    "usage_spend",
    "unchecked_mark",
    "console_emergency_stop",
    "agent_ownership",
    "console_otlp",
    "console_signed_triggers",
    "sandbox_network_on",
)

HUB_PUBLISH = "hub_publish"
OBSERVED_PARTS: tuple[str, ...] = (HUB_PUBLISH,)

PARTS: tuple[str, ...] = DECLARED_PARTS + OBSERVED_PARTS


def _raw_setting():
    value = getattr(settings, SETTING, None)
    if value is None:
        value = os.environ.get(SETTING, "")
    return value


def released_parts() -> frozenset[str]:
    """The declared parts the operator says are released. Read on every call (no restart needed)."""
    raw = _raw_setting()
    if isinstance(raw, str):
        names = raw.split(",")
    elif isinstance(raw, (list, tuple, set, frozenset)):
        names = [str(name) for name in raw]
    else:
        names = []
    cleaned = {name.strip().lower() for name in names if str(name).strip()}
    unknown = cleaned - set(DECLARED_PARTS) - set(PEER_PARTS)
    if unknown:
        logger.warning("omega_readiness_unknown_parts", parts=sorted(unknown))
    return frozenset(cleaned & set(DECLARED_PARTS))


def _checker_configured() -> bool:
    from omega_hub import conf as hub_conf

    return bool(hub_conf.checker_token())


def part_ready(part: str) -> bool:
    """Whether one part works today. An unknown part never does."""
    if part == HUB_PUBLISH:
        return _checker_configured()
    if part in DECLARED_PARTS:
        return part in released_parts()
    return False
