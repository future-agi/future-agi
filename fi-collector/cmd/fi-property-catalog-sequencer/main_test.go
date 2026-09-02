package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/propertycatalog"
)

func mapLookup(values map[string]string) lookupEnvFunc {
	return func(name string) (string, bool) {
		value, present := values[name]
		return value, present
	}
}

func validSequencerEnvironment(t *testing.T) map[string]string {
	t.Helper()
	return map[string]string{
		envMode:              string(propertycatalog.RuntimeSequencer),
		envEnvironment:       propertycatalog.DevelopmentEnvironment,
		envDevAck:            propertycatalog.DevelopmentAcknowledgement,
		envEpoch:             "3",
		envProjection:        "1",
		envStreamID:          "44444444-4444-4444-8444-444444444444",
		envScopeMode:         string(propertycatalog.WorkspaceScopeRevisionFence),
		envFenceFile:         filepath.Join(t.TempDir(), "revision-fence.json"),
		envSpoolDir:          t.TempDir(),
		envOutputBrokers:     "kafka-1:9092,kafka-2:9092",
		envOutputTopic:       "futureagi.dev.property-catalog.ordered.v1",
		envTransactionalID:   "futureagi-property-catalog-sequencer-dev-v1",
		envCandidateBrokers:  "kafka-1:9092,kafka-2:9092",
		envCandidateTopic:    "futureagi.dev.property-catalog.candidates.v1",
		envCandidateGroup:    "futureagi-property-catalog-sequencer-dev-v1",
		envCandidateInstance: "futureagi-property-catalog-sequencer-dev-v1",
	}
}

func TestSequencerConfigRequiresTwoTopicsAndFixedOwnerIdentities(t *testing.T) {
	values := validSequencerEnvironment(t)
	cfg, err := loadConfig(mapLookup(values))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.runtime.Mode != propertycatalog.RuntimeSequencer ||
		cfg.runtime.WorkspaceScopeMode != propertycatalog.WorkspaceScopeRevisionFence ||
		cfg.output.TransactionalID != values[envTransactionalID] ||
		cfg.candidate.InstanceID != values[envCandidateInstance] ||
		cfg.candidate.Topic == cfg.output.Topic ||
		cfg.receipts.Directory != filepath.Join(values[envSpoolDir], "candidate-receipts") ||
		cfg.startupTimeout != defaultStartupTimeout {
		t.Fatalf("sequencer config=%+v", cfg)
	}
	if _, err := os.Stat(cfg.receipts.Directory); !os.IsNotExist(err) {
		t.Fatalf("configuration parsing mutated durable state: %v", err)
	}
}

func TestSequencerConfigFailsClosedBeforeRuntimeConstruction(t *testing.T) {
	for name, mutate := range map[string]func(map[string]string){
		"missing transaction identity":  func(values map[string]string) { delete(values, envTransactionalID) },
		"missing static group identity": func(values map[string]string) { delete(values, envCandidateInstance) },
		"same input and output topic":   func(values map[string]string) { values[envCandidateTopic] = values[envOutputTopic] },
		"collector candidate mode":      func(values map[string]string) { values[envMode] = string(propertycatalog.RuntimeKafka) },
		"bad transaction timeout":       func(values map[string]string) { values[envTransactionTimeout] = "121s" },
		"bad startup timeout":           func(values map[string]string) { values[envStartupTimeout] = "0s" },
		"dynamic Kafka member":          func(values map[string]string) { values[envCandidateInstance] = "" },
		"unknown workspace scope":       func(values map[string]string) { values[envScopeMode] = "all" },
	} {
		t.Run(name, func(t *testing.T) {
			values := validSequencerEnvironment(t)
			mutate(values)
			if _, err := loadConfig(mapLookup(values)); err == nil {
				t.Fatal("unsafe sequencer configuration was accepted")
			}
		})
	}
}

func TestProductionSequencerRequiresExactProductionGate(t *testing.T) {
	values := validSequencerEnvironment(t)
	values[envEnvironment] = propertycatalog.ProductionEnvironment
	delete(values, envDevAck)
	values[envProdAck] = propertycatalog.ProductionAcknowledgement
	values[envOutputTopic] = "futureagi.prod.property-catalog.ordered.v1"
	values[envCandidateTopic] = "futureagi.prod.property-catalog.candidates.v1"
	cfg, err := loadConfig(mapLookup(values))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.runtime.Environment != propertycatalog.ProductionEnvironment ||
		cfg.runtime.ProductionAcknowledgement != propertycatalog.ProductionAcknowledgement {
		t.Fatalf("production config=%+v", cfg.runtime)
	}

	for _, bad := range []string{"", propertycatalog.DevelopmentAcknowledgement, "yes"} {
		broken := validSequencerEnvironment(t)
		broken[envEnvironment] = propertycatalog.ProductionEnvironment
		delete(broken, envDevAck)
		if bad != "" {
			broken[envProdAck] = bad
		}
		if _, err := loadConfig(mapLookup(broken)); err == nil ||
			!strings.Contains(err.Error(), "production") {
			t.Fatalf("production acknowledgement %q error=%v", bad, err)
		}
	}
}

func TestSequencerOperationalBoundsAreEnvironmentDriven(t *testing.T) {
	values := validSequencerEnvironment(t)
	values[envStartupTimeout] = "8s"
	values[envTransactionTimeout] = "45s"
	values[envReceiptFiles] = "123"
	values[envReceiptBytes] = "456789"
	values[envRecentCandidateIDs] = "321"
	values[envReplayInterval] = "2s"
	cfg, err := loadConfig(mapLookup(values))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.startupTimeout != 8*time.Second || cfg.output.TransactionTimeout != 45*time.Second ||
		cfg.receipts.MaxPendingFiles != 123 || cfg.receipts.MaxPendingBytes != 456789 ||
		cfg.receipts.MaxRecentIDs != 321 || cfg.runtime.ReplayInterval != 2*time.Second {
		t.Fatalf("operational config=%+v", cfg)
	}
}
