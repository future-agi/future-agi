package propertycatalog

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func validRuntimeConfig(t *testing.T, workspaces ...string) RuntimeConfig {
	t.Helper()
	if len(workspaces) == 0 {
		workspaces = []string{testWorkspace}
	}
	return RuntimeConfig{
		Mode: RuntimeDirectKafkaDevelopment, Environment: DevelopmentEnvironment,
		DevelopmentAcknowledgement: DevelopmentAcknowledgement,
		CatalogEpoch:               3, ProjectionVersion: 1, ProducerStreamID: testStream,
		WorkspaceAllowlist: workspaces,
		RevisionFenceFile:  filepath.Join(t.TempDir(), "revision-fence.json"),
		SpoolDirectory:     t.TempDir(),
		Kafka:              KafkaRuntimeConfig{Brokers: []string{"kafka:9092"}, Topic: "property-catalog-v1-dev"},
	}
}

func TestRuntimeConfigDefaultsDisabledAndEnabledModesRequireEnvironmentSpecificAcknowledgement(t *testing.T) {
	if mode, err := (RuntimeConfig{}).SelectedMode(); err != nil || mode != RuntimeDisabled {
		t.Fatalf("zero mode=%q err=%v", mode, err)
	}
	cfg := validRuntimeConfig(t)
	if mode, err := cfg.SelectedMode(); err != nil || mode != RuntimeDirectKafkaDevelopment {
		t.Fatalf("mode=%q err=%v", mode, err)
	}
	defaults := cfg.WithDefaults()
	if defaults.WorkspaceScopeMode != WorkspaceScopeStatic ||
		defaults.QueueDepth != defaultQueueDepth ||
		defaults.ShutdownTimeout != defaultShutdownTimeout ||
		defaults.MaxChunkRows != defaultMaxChunkRows ||
		defaults.Kafka.DeliveryTimeout != DefaultDeliveryTransportTimeout ||
		defaults.Kafka.ClientID != "fi-property-catalog-sequencer-output-v1-dev" {
		t.Fatalf("defaults=%+v", defaults)
	}
	production := cfg
	production.Mode = RuntimeSequencer
	production.Environment = ProductionEnvironment
	production.DevelopmentAcknowledgement = ""
	production.ProductionAcknowledgement = ProductionAcknowledgement
	if mode, err := production.SelectedMode(); err != nil || mode != RuntimeSequencer {
		t.Fatalf("production mode=%q err=%v", mode, err)
	}
	if clientID := production.WithDefaults().Kafka.ClientID; clientID != "fi-property-catalog-sequencer-output-v1-prod" {
		t.Fatalf("production client ID=%q", clientID)
	}
	candidate := cfg
	candidate.Mode = RuntimeKafka
	candidate.ProducerStreamID = ""
	candidate.WorkspaceAllowlist = nil
	candidate.RevisionFenceFile = ""
	candidate.SpoolDirectory = ""
	if mode, err := candidate.SelectedMode(); err != nil || mode != RuntimeKafka {
		t.Fatalf("candidate mode=%q err=%v", mode, err)
	}
	if clientID := candidate.WithDefaults().Kafka.ClientID; clientID != "fi-collector-property-candidate-v1-dev" {
		t.Fatalf("candidate client ID=%q", clientID)
	}

	for name, mutate := range map[string]func(*RuntimeConfig){
		"production with dev acknowledgement": func(c *RuntimeConfig) { c.Environment = ProductionEnvironment },
		"both acknowledgements": func(c *RuntimeConfig) {
			c.ProductionAcknowledgement = ProductionAcknowledgement
		},
		"unknown environment":     func(c *RuntimeConfig) { c.Environment = "staging" },
		"missing acknowledgement": func(c *RuntimeConfig) { c.DevelopmentAcknowledgement = "" },
		"missing fence":           func(c *RuntimeConfig) { c.RevisionFenceFile = "" },
		"direct-like destination": func(c *RuntimeConfig) { c.Kafka.Brokers = nil },
		"delivery timeout above ceiling": func(c *RuntimeConfig) {
			c.Kafka.DeliveryTimeout = MaxDeliveryTimeout + time.Second
		},
		"shutdown timeout above ceiling": func(c *RuntimeConfig) {
			c.ShutdownTimeout = MaxShutdownTimeout + time.Second
		},
		"spool bytes above ceiling": func(c *RuntimeConfig) {
			c.MaxSpoolBytes = maxRuntimeSpoolBytes + 1
		},
		"unsorted allowlist": func(c *RuntimeConfig) {
			c.WorkspaceAllowlist = []string{"33333333-3333-4333-8333-333333333333", testWorkspace}
		},
	} {
		t.Run(name, func(t *testing.T) {
			candidate := cfg
			mutate(&candidate)
			if err := candidate.Validate(); err == nil {
				t.Fatal("unsafe runtime config was accepted")
			}
		})
	}
}

func TestRuntimeConfigWorkspaceScopeDefaultsStaticAndFenceModeIsExplicit(t *testing.T) {
	static := validRuntimeConfig(t)
	if err := static.Validate(); err != nil {
		t.Fatal(err)
	}
	if static.normalizedWorkspaceScopeMode() != WorkspaceScopeStatic ||
		!static.WorkspaceAllowed(testWorkspace) ||
		static.WorkspaceAllowed(testWorkspaceTwo) {
		t.Fatalf("static workspace scope drifted: %+v", static)
	}

	fenceScoped := static
	fenceScoped.WorkspaceScopeMode = WorkspaceScopeRevisionFence
	fenceScoped.WorkspaceAllowlist = nil
	if err := fenceScoped.Validate(); err != nil {
		t.Fatal(err)
	}
	if fenceScoped.WorkspaceAllowed(testWorkspace) ||
		!fenceScoped.workspaceWithinConfiguredScope(testWorkspace) {
		t.Fatalf("revision-fence scope became a static allowlist: %+v", fenceScoped)
	}
	fence := testRevisionFence(17, "building")
	if !fenceScoped.fenceAllowsTenant(fence, testOrganization, testWorkspace) ||
		fenceScoped.fenceAllowsTenant(fence, testOrganization, testWorkspaceTwo) {
		t.Fatal("revision-fence scope did not require an exact tenant assignment")
	}
}

func TestRuntimeConfigRejectsUnsafeWorkspaceScopeModes(t *testing.T) {
	for name, mutate := range map[string]func(*RuntimeConfig){
		"revision fence in production": func(c *RuntimeConfig) {
			c.WorkspaceScopeMode = WorkspaceScopeRevisionFence
			c.WorkspaceAllowlist = nil
			c.Environment = ProductionEnvironment
			c.DevelopmentAcknowledgement = ""
			c.ProductionAcknowledgement = ProductionAcknowledgement
		},
		"revision fence with static allowlist": func(c *RuntimeConfig) {
			c.WorkspaceScopeMode = WorkspaceScopeRevisionFence
		},
		"revision fence without exact dev acknowledgement": func(c *RuntimeConfig) {
			c.WorkspaceScopeMode = WorkspaceScopeRevisionFence
			c.WorkspaceAllowlist = nil
			c.DevelopmentAcknowledgement = "wrong"
		},
		"unknown workspace scope": func(c *RuntimeConfig) {
			c.WorkspaceScopeMode = "dynamic"
		},
		"default static scope without allowlist": func(c *RuntimeConfig) {
			c.WorkspaceAllowlist = nil
		},
	} {
		t.Run(name, func(t *testing.T) {
			cfg := validRuntimeConfig(t)
			mutate(&cfg)
			if err := cfg.Validate(); err == nil {
				t.Fatal("unsafe workspace scope was accepted")
			}
		})
	}
}

func TestRuntimeConfigRejectsUnknownModeWithoutNormalizingToEnabled(t *testing.T) {
	cfg := validRuntimeConfig(t)
	cfg.Mode = "prod"
	if err := cfg.Validate(); err == nil || !strings.Contains(err.Error(), "invalid runtime mode") {
		t.Fatalf("error=%v", err)
	}
}

func TestRuntimeConfigReportsReviewedWorkspaceAllowlistLimit(t *testing.T) {
	workspaces := make([]string, maxWorkspaceAllowlist+1)
	cfg := validRuntimeConfig(t, workspaces...)
	want := fmt.Sprintf("1..%d allowlisted workspaces", maxWorkspaceAllowlist)

	if err := cfg.Validate(); err == nil || !strings.Contains(err.Error(), want) {
		t.Fatalf("error=%v, want substring %q", err, want)
	}
}
