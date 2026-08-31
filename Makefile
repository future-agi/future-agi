.PHONY: slot-help slot-up slot-down slot-status slot-urls slot-logs slot-shell slot-run slots-doctor slots-recover slots-prune slot-purge

PYTHON ?= python3
SLOT ?= auto
SERVICES ?= none
ISOLATE_INFRA ?=
SERVICE ?= frontend
COMMAND ?=
CONFIRM ?=

# `SLOT` is a Make/CLI selector, while generated Compose env files contain the
# resolved numeric slot.  Command-line Make variables are exported by default;
# keeping this one out of child processes prevents SLOT=auto from overriding
# the generated SLOT=1..20 value used in browser URLs.
unexport SLOT

# Keep Make variables data-only when they cross into the recipe shell.  `value`
# prevents a command-line value containing Make syntax from being re-expanded.
shell_quote = '$(subst ','"'"',$(1))'

slot-help:
	@$(PYTHON) -c "print('FutureAGI slots are operated only through these Make targets.\n\nStart: make slot-up SLOT=auto|1..20 SERVICES=none|all|backend,simulation,gateway,collector,serving,executor,peerdb,observability [ISOLATE_INFRA=postgres,clickhouse,redis,rabbitmq,minio,temporal]\nFrontend is always slot-private; SERVICES selects additional private provider groups. A private backend also makes simulation, collector, and peerdb private so they share its state. Omitted groups use validated shared defaults. ISOLATE_INFRA requires SERVICES=backend.\nEach slot inherits application variables from its worktree-root .env; generated slot topology overrides matching reserved values. Rerun slot-up after editing .env.\nA newly built simulation provider requires agent_learning_kit-0.1.0-py3-none-any.whl at the worktree root; slot-up checks before Docker mutation.\nRuntime actions require SLOTS_RUNTIME_APPROVED=1: make slot-up, slot-down, slot-logs SLOT=1 SERVICE=backend, slot-shell SLOT=1 SERVICE=backend, slot-run SLOT=1 SERVICE=backend COMMAND=\"...\", slots-doctor, slots-recover, slots-prune, slot-purge SLOT=1 CONFIRM=1.\nInspect without runtime approval: make slot-status [SLOT=1] and make slot-urls SLOT=1.\nslot-down preserves generated files and provider state. slot-purge removes only exact slot Compose volumes; shared logical state is preserved, while private backend state requires CONFIRM to equal SLOT.\nslots-recover handles interrupted up, replacement, down, and purge operations while preserving volumes. Retry results require rerunning the original command; purge requires renewed approval and exact confirmation. slots-prune cleans stale zero-reference shared-provider metadata.')"

slot-up:
	$(PYTHON) -m slots.cli up --slot $(call shell_quote,$(value SLOT)) --services $(call shell_quote,$(value SERVICES)) --isolate-infra $(call shell_quote,$(value ISOLATE_INFRA))

slot-down:
	$(PYTHON) -m slots.cli down --slot $(call shell_quote,$(value SLOT))

slot-status:
	$(PYTHON) -m slots.cli status $(if $(filter-out auto,$(value SLOT)),--slot $(call shell_quote,$(value SLOT)))

slot-urls:
	$(PYTHON) -m slots.cli urls --slot $(call shell_quote,$(value SLOT))

slot-logs:
	$(PYTHON) -m slots.cli logs --slot $(call shell_quote,$(value SLOT)) --service $(call shell_quote,$(value SERVICE))

slot-shell:
	$(PYTHON) -m slots.cli shell --slot $(call shell_quote,$(value SLOT)) --service $(call shell_quote,$(value SERVICE))

slot-run:
	$(PYTHON) -m slots.cli run --slot $(call shell_quote,$(value SLOT)) --service $(call shell_quote,$(value SERVICE)) --command $(call shell_quote,$(value COMMAND))

slots-doctor:
	$(PYTHON) -m slots.cli doctor

slots-recover:
	$(PYTHON) -m slots.cli recover

slots-prune:
	$(PYTHON) -m slots.cli prune

slot-purge:
	$(PYTHON) -m slots.cli purge --slot $(call shell_quote,$(value SLOT)) --confirm $(call shell_quote,$(value CONFIRM))
