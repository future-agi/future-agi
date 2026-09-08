package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const (
	pythonRetirementBuildToken = "66666666-6666-4666-8666-666666666666"
	pythonRetirementHotStream  = "55555555-5555-4555-8555-555555555555"
	pythonRetirementBuildLease = "7a0fd1048887e6fc5122046a17381755f48e588cb467874e1402b48cf4deccc7"
	pythonRetirementSHA        = "4313b4f96614cd60dac703929146aa802041ac19116109476f2e49bd3b68c634"
)

func pythonRetirementFixture(t *testing.T) []byte {
	t.Helper()
	raw, err := os.ReadFile("testdata/producer_retirement_v1.json")
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func writePythonRetirementFixture(t *testing.T, directory string) {
	t.Helper()
	if err := os.WriteFile(
		filepath.Join(directory, producerRetirementFileName),
		pythonRetirementFixture(t),
		0o600,
	); err != nil {
		t.Fatal(err)
	}
}

func physicalSnapshotRetirementFixture(t *testing.T) (ProducerStateRetirement, buildPlanDocumentJSON) {
	t.Helper()
	var document producerRetirementDocument
	if err := json.Unmarshal(pythonRetirementFixture(t), &document); err != nil {
		t.Fatal(err)
	}
	value := document.Retirements[0]
	var plan buildPlanDocumentJSON
	if err := json.Unmarshal([]byte(value.BuildPlanJSON), &plan); err != nil {
		t.Fatal(err)
	}
	for index := range plan.Streams {
		plan.Streams[index].SourceCutoff.Label = fmt.Sprintf("physical_snapshot_r%d", value.CatalogRevision)
		plan.Streams[index].SourceCutoff.Value = 123456789
	}
	setPhysicalRetirementPlan(t, &value, plan)
	return value, plan
}

func setPhysicalRetirementPlan(t *testing.T, value *ProducerStateRetirement, plan buildPlanDocumentJSON) {
	t.Helper()
	raw, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	value.BuildPlanJSON = string(raw)
	value.BuildLeaseSHA256 = testDigest(value.BuildPlanJSON)
	value.RetirementSHA256 = producerRetirementSHA256(*value)
}

func TestProducerRetirementPhysicalSnapshotCompatibility(t *testing.T) {
	for _, revision := range []uint64{3, 41} {
		t.Run(fmt.Sprintf("revision_%d", revision), func(t *testing.T) {
			value, plan := physicalSnapshotRetirementFixture(t)
			value.CatalogEpoch, value.ProjectionVersion = 7, 9
			value.CatalogRevision, value.LineageAnchorRevision = revision, revision
			plan.CatalogEpoch, plan.ProjectionVersion = value.CatalogEpoch, value.ProjectionVersion
			plan.CatalogRevision = revision
			for index := range plan.Streams {
				plan.Streams[index].SourceCutoff.Label = fmt.Sprintf("physical_snapshot_r%d", revision)
			}
			setPhysicalRetirementPlan(t, &value, plan)
			if err := validateProducerRetirement(value); err != nil {
				t.Fatalf("valid physical snapshot retirement rejected: %v", err)
			}
			raw, err := json.Marshal(producerRetirementDocument{
				Format: producerRetirementFormat, Version: producerRetirementVersion,
				Retirements: []ProducerStateRetirement{value},
			})
			if err != nil {
				t.Fatal(err)
			}
			raw = append(raw, '\n')
			directory := t.TempDir()
			path := filepath.Join(directory, producerRetirementFileName)
			if err := os.WriteFile(path, raw, 0o600); err != nil {
				t.Fatal(err)
			}
			loaded, err := loadProducerRetirements(directory)
			if err != nil || loaded[producerRetirementTenant{value.OrganizationID, value.WorkspaceID}] != value {
				t.Fatalf("loading did not preserve the exact retirement: %v", err)
			}
			after, err := os.ReadFile(path)
			if err != nil || !bytes.Equal(raw, after) {
				t.Fatalf("loading changed persisted proof bytes: %v", err)
			}
		})
	}
}

func TestProducerRetirementPhysicalSnapshotRejectsInvalidProofs(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*ProducerStateRetirement, *buildPlanDocumentJSON)
		want   string
	}{
		{"wrong revision label", func(_ *ProducerStateRetirement, p *buildPlanDocumentJSON) {
			p.Streams[0].SourceCutoff.Label = "physical_snapshot_r999"
		}, "lifecycle mode differs"},
		{"mixed labels", func(_ *ProducerStateRetirement, p *buildPlanDocumentJSON) {
			p.Streams[0].SourceCutoff.Label = "initial_backfill_source_cutoff"
		}, "lifecycle mode differs"},
		{"mixed generations", func(_ *ProducerStateRetirement, p *buildPlanDocumentJSON) { p.Streams[0].SourceCutoff.Value++ }, "lifecycle mode differs"},
		{"zero generation", func(_ *ProducerStateRetirement, p *buildPlanDocumentJSON) {
			for i := range p.Streams {
				p.Streams[i].SourceCutoff.Value = 0
			}
		}, "stream 0 is invalid"},
		{"full repair", func(v *ProducerStateRetirement, _ *buildPlanDocumentJSON) { v.LifecycleMode = "full_repair" }, "lifecycle mode differs"},
		{"incremental", func(v *ProducerStateRetirement, _ *buildPlanDocumentJSON) {
			v.LifecycleMode = "incremental"
			v.LineageAnchorRevision--
			v.ActivationSequence++
			v.ActiveRevisionsSinceAnchor = 1
		}, "lifecycle mode differs"},
		{"empty inventory", func(_ *ProducerStateRetirement, p *buildPlanDocumentJSON) { p.Streams = nil }, "exact revision lease"},
		{"missing stream", func(_ *ProducerStateRetirement, p *buildPlanDocumentJSON) { p.Streams = p.Streams[1:] }, "exact revision lease"},
		{"wrong role", func(_ *ProducerStateRetirement, p *buildPlanDocumentJSON) { p.Streams[0].Role = "hot_values" }, "only span_attribute"},
		{"wrong tenant", func(_ *ProducerStateRetirement, p *buildPlanDocumentJSON) { p.WorkspaceID = testStream }, "exact revision lease"},
		{"wrong hot stream", func(v *ProducerStateRetirement, _ *buildPlanDocumentJSON) { v.HotProducerStreamID = testStream }, "current source stream is absent"},
		{"wrong lineage", func(v *ProducerStateRetirement, _ *buildPlanDocumentJSON) { v.LineageAnchorRevision-- }, "not its own lineage anchor"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			value, plan := physicalSnapshotRetirementFixture(t)
			test.mutate(&value, &plan)
			// Re-sign mutations so structural failures are not masked by checksums.
			setPhysicalRetirementPlan(t, &value, plan)
			if err := validateProducerRetirement(value); err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("want %q, got %v", test.want, err)
			}
		})
	}
	for _, digest := range []string{"lease", "retirement"} {
		t.Run(digest+" digest", func(t *testing.T) {
			value, _ := physicalSnapshotRetirementFixture(t)
			want := "retirement digest does not match"
			if digest == "lease" {
				value.BuildLeaseSHA256 = testDigest("corrupt lease")
				value.RetirementSHA256 = producerRetirementSHA256(value)
				want = "build plan digest does not match"
			} else {
				value.RetirementSHA256 = testDigest("corrupt retirement")
			}
			if err := validateProducerRetirement(value); err == nil || !strings.Contains(err.Error(), want) {
				t.Fatalf("want %q, got %v", want, err)
			}
		})
	}
}

func TestProducerRetirementPreservesNormalLifecycleCutoffs(t *testing.T) {
	for _, mode := range []string{"initial_backfill", "full_repair", "incremental"} {
		t.Run(mode, func(t *testing.T) {
			value, plan := physicalSnapshotRetirementFixture(t)
			value.LifecycleMode = mode
			if mode == "incremental" {
				value.LineageAnchorRevision--
				value.ActivationSequence++
				value.ActiveRevisionsSinceAnchor = 1
			}
			for index := range plan.Streams {
				plan.Streams[index].SourceCutoff.Label = mode + "_source_cutoff"
				plan.Streams[index].SourceCutoff.Value += uint64(index)
			}
			setPhysicalRetirementPlan(t, &value, plan)
			if err := validateProducerRetirement(value); err != nil {
				t.Fatalf("normal lifecycle retirement rejected: %v", err)
			}
		})
	}
}

func retirementRevisionFence(revision uint64, status string) RevisionFence {
	buildToken, buildLease := pythonRetirementBuildToken, pythonRetirementBuildLease
	if revision != 17 {
		buildToken, buildLease = "77777777-7777-4777-8777-777777777777", testDigest("newer-build-lease")
	}
	fence := RevisionFence{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: revision, ProjectionVersion: 1,
		BuildLeaseSHA256: buildLease, BuildToken: buildToken,
		ProjectIDs:  []string{testProject},
		SpanSinceUS: 1786705200000000, SpanUntilUS: 1786708800000000,
		IssuedAt: "2026-08-14 12:00:00.000000", ExpiresAt: "2026-08-14 12:10:00.000000",
		Status: status,
	}
	if status == "draining" {
		fence.DrainDeadline = "2026-08-14 12:08:00.000000"
	}
	fence.FenceSHA256 = RevisionFenceSHA256(fence)
	return fence
}

func newRetirementRuntime(
	t *testing.T,
) (*HotRuntime, *mutableRevisionProvider, *recordingEnvelopePublisher, RuntimeConfig) {
	t.Helper()
	cfg := validRuntimeConfig(t).WithDefaults()
	cfg.ProducerStreamID = pythonRetirementHotStream
	provider := &mutableRevisionProvider{fences: make(map[string]RevisionFence)}
	provider.set(retirementRevisionFence(17, "building"))
	downstream := &recordingEnvelopePublisher{}
	runtime, err := NewHotRuntime(cfg, provider, downstream)
	if err != nil {
		t.Fatal(err)
	}
	return runtime, provider, downstream, cfg
}

// acknowledgeRetirementTerminal creates the exact crash-recoverable terminal
// producer checkpoint for the Python fixture's active revision. keepSpool
// models a crash after Kafka/state ACK but before spool removal.
func acknowledgeRetirementTerminal(
	t *testing.T,
	runtime *HotRuntime,
	provider *mutableRevisionProvider,
	keepSpool bool,
) streamKey {
	t.Helper()
	intent := retirementRevisionFence(17, "draining")
	provider.set(intent)
	if err := runtime.observeDraining(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := runtime.persistDrainProofs(context.Background()); err != nil {
		t.Fatal(err)
	}
	proofs, err := runtime.DrainProofs(context.Background())
	if err != nil || len(proofs) != 1 || proofs[0].Phase != "prepared" || proofs[0].TerminalSequence != 1 {
		t.Fatalf("prepared retirement proof=%+v err=%v", proofs, err)
	}
	bound := intent
	bound.FencedSequence = proofs[0].TerminalSequence
	bound.FenceSHA256 = RevisionFenceSHA256(bound)
	provider.set(bound)
	if err := runtime.observeDraining(context.Background()); err != nil {
		t.Fatal(err)
	}
	pending, err := runtime.spool.PendingEnvelopes()
	if err != nil || len(pending) != 1 || !pending[0].Snapshot().Terminal {
		t.Fatalf("terminal spool=%d err=%v", len(pending), err)
	}
	if keepSpool {
		if err := runtime.publisher.Publish(context.Background(), pending[0]); err != nil {
			t.Fatal(err)
		}
	} else if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err != nil {
		t.Fatal(err)
	}
	checkpoint := runtime.publisher.state.snapshot()
	if len(checkpoint) != 1 {
		t.Fatalf("terminal producer state=%+v", checkpoint)
	}
	for key, value := range checkpoint {
		if !value.Terminal || value.Sequence != 1 || value.BuildToken != pythonRetirementBuildToken {
			t.Fatalf("terminal checkpoint=%+v", value)
		}
		return key
	}
	t.Fatal("terminal checkpoint disappeared")
	return streamKey{}
}

func TestProducerRetirementConsumesExactCanonicalPythonProof(t *testing.T) {
	directory := t.TempDir()
	writePythonRetirementFixture(t, directory)
	loaded, err := loadProducerRetirements(directory)
	if err != nil || len(loaded) != 1 {
		t.Fatalf("retirements=%+v err=%v", loaded, err)
	}
	value := loaded[producerRetirementTenant{testOrganization, testWorkspace}]
	if value.RetirementSHA256 != pythonRetirementSHA ||
		value.BuildLeaseSHA256 != pythonRetirementBuildLease ||
		value.BuildToken != pythonRetirementBuildToken ||
		value.HotProducerStreamID != pythonRetirementHotStream ||
		producerRetirementSHA256(value) != pythonRetirementSHA {
		t.Fatalf("cross-language retirement=%+v", value)
	}

	t.Run("tamper", func(t *testing.T) {
		directory := t.TempDir()
		raw := bytes.Replace(
			pythonRetirementFixture(t),
			[]byte(pythonRetirementSHA),
			[]byte(strings.Repeat("c", 64)),
			1,
		)
		if err := os.WriteFile(filepath.Join(directory, producerRetirementFileName), raw, 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := loadProducerRetirements(directory); err == nil || !strings.Contains(err.Error(), "digest") {
			t.Fatalf("tampered proof error=%v", err)
		}
	})

	t.Run("non_private_mode", func(t *testing.T) {
		directory := t.TempDir()
		path := filepath.Join(directory, producerRetirementFileName)
		if err := os.WriteFile(path, pythonRetirementFixture(t), 0o640); err != nil {
			t.Fatal(err)
		}
		if _, err := loadProducerRetirements(directory); err == nil || !strings.Contains(err.Error(), "unsafe") {
			t.Fatalf("non-private proof error=%v", err)
		}
	})
}

func TestNewRuntimeAllowsEmptyPreBootstrapVolumeWithoutFence(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	provider, err := NewFileRevisionProvider(cfg.RevisionFenceFile)
	if err != nil {
		t.Fatal(err)
	}
	runtime, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatalf("empty pre-bootstrap runtime required a fence: %v", err)
	}
	if len(runtime.publisher.state.snapshot()) != 0 || len(runtime.tails) != 0 {
		t.Fatal("empty pre-bootstrap runtime invented producer state")
	}
	ctx, cancel := context.WithCancel(context.Background())
	if err := runtime.Start(ctx); err != nil {
		t.Fatalf("empty pre-bootstrap runtime did not start: %v", err)
	}
	cancel()
	if err := runtime.Shutdown(context.Background()); err != nil {
		t.Fatalf("empty pre-bootstrap runtime did not stop cleanly: %v", err)
	}
	select {
	case err := <-runtime.Gaps():
		t.Fatalf("missing pre-bootstrap fence was reported as a delivery gap: %v", err)
	default:
	}
}

func TestNewRuntimeRejectsMissingFenceAfterDurableEvidenceAppears(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	provider, err := NewFileRevisionProvider(cfg.RevisionFenceFile)
	if err != nil {
		t.Fatal(err)
	}
	runtime, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(runtime.DrainProofPath(), []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := runtime.Start(context.Background()); err == nil || !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("missing fence was tolerated after durable evidence: %v", err)
	}
}

func TestProducerRetirementPersistsBeforeMemoryRemovalAndSurvivesRestart(t *testing.T) {
	runtime, provider, _, cfg := newRetirementRuntime(t)
	key := acknowledgeRetirementTerminal(t, runtime, provider, false)
	before, err := os.ReadFile(filepath.Join(cfg.SpoolDirectory, producerStateFileName))
	if err != nil {
		t.Fatal(err)
	}
	writePythonRetirementFixture(t, cfg.SpoolDirectory)
	provider.set(retirementRevisionFence(18, "building"))
	fences, _ := provider.CurrentRevisions(context.Background())
	runtime.publisher.state.persistHook = func(map[streamKey]StreamCheckpoint) error {
		return errors.New("simulated crash before producer-state replace")
	}
	if err := runtime.compactProducerState(context.Background(), fences); err == nil ||
		!strings.Contains(err.Error(), "simulated crash") {
		t.Fatalf("failed retirement persistence error=%v", err)
	}
	if _, exists := runtime.publisher.state.snapshot()[key]; !exists {
		t.Fatal("failed durable removal changed producer state")
	}
	runtime.mu.Lock()
	_, tailExists := runtime.tails[key]
	runtime.mu.Unlock()
	if !tailExists {
		t.Fatal("failed durable removal changed the in-memory tail")
	}
	afterFailure, _ := os.ReadFile(filepath.Join(cfg.SpoolDirectory, producerStateFileName))
	if !bytes.Equal(before, afterFailure) {
		t.Fatal("failed retirement changed the durable producer-state bytes")
	}

	runtime.publisher.state.persistHook = nil
	if err := runtime.compactProducerState(context.Background(), fences); err != nil {
		t.Fatal(err)
	}
	if len(runtime.publisher.state.snapshot()) != 0 {
		t.Fatal("activation-proven terminal checkpoint was not retired")
	}
	runtime.mu.Lock()
	_, tailExists = runtime.tails[key]
	_, drainExists := runtime.drains[key]
	runtime.mu.Unlock()
	if tailExists || drainExists {
		t.Fatalf("retired memory tail=%t drain=%t", tailExists, drainExists)
	}
	raw, err := os.ReadFile(filepath.Join(cfg.SpoolDirectory, producerStateFileName))
	if err != nil || !bytes.Contains(raw, []byte(`"checkpoints":[]`)) {
		t.Fatalf("compacted state=%q err=%v", raw, err)
	}

	restarted, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatal(err)
	}
	if len(restarted.publisher.state.snapshot()) != 0 || len(restarted.tails) != 0 || len(restarted.drains) != 0 {
		t.Fatalf(
			"restart resurrected retired state: checkpoints=%d tails=%d drains=%d",
			len(restarted.publisher.state.snapshot()), len(restarted.tails), len(restarted.drains),
		)
	}
}

func TestProducerRetirementRetainsCurrentPendingStaleAndCrossTenantState(t *testing.T) {
	t.Run("current_fence", func(t *testing.T) {
		runtime, provider, _, cfg := newRetirementRuntime(t)
		key := acknowledgeRetirementTerminal(t, runtime, provider, false)
		writePythonRetirementFixture(t, cfg.SpoolDirectory)
		provider.set(retirementRevisionFence(17, "fenced"))
		fences, _ := provider.CurrentRevisions(context.Background())
		if err := runtime.compactProducerState(context.Background(), fences); err != nil {
			t.Fatal(err)
		}
		if _, exists := runtime.publisher.state.snapshot()[key]; !exists {
			t.Fatal("checkpoint matching the current fence was retired")
		}
	})

	t.Run("pending_spool", func(t *testing.T) {
		runtime, provider, downstream, cfg := newRetirementRuntime(t)
		key := acknowledgeRetirementTerminal(t, runtime, provider, true)
		writePythonRetirementFixture(t, cfg.SpoolDirectory)
		provider.set(retirementRevisionFence(18, "building"))
		fences, _ := provider.CurrentRevisions(context.Background())
		if err := runtime.compactProducerState(context.Background(), fences); err != nil {
			t.Fatal(err)
		}
		if _, exists := runtime.publisher.state.snapshot()[key]; !exists {
			t.Fatal("checkpoint with a matching spool envelope was retired")
		}
		if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err != nil {
			t.Fatal(err)
		}
		if len(downstream.envelopes) != 1 {
			t.Fatalf("already-acknowledged terminal was republished: %d", len(downstream.envelopes))
		}
		if err := runtime.compactProducerState(context.Background(), fences); err != nil {
			t.Fatal(err)
		}
		if _, exists := runtime.publisher.state.snapshot()[key]; exists {
			t.Fatal("checkpoint remained after its spool envelope was removed")
		}
	})

	t.Run("stale_high_water", func(t *testing.T) {
		runtime, provider, _, cfg := newRetirementRuntime(t)
		key := acknowledgeRetirementTerminal(t, runtime, provider, false)
		writePythonRetirementFixture(t, cfg.SpoolDirectory)
		checkpoint := runtime.publisher.state.snapshot()[key]
		checkpoint.CatalogRevision = 18
		checkpoint.BuildToken = "88888888-8888-4888-8888-888888888888"
		newKey := checkpointKey(checkpoint)
		runtime.publisher.state.mu.Lock()
		runtime.publisher.state.checkpoints = map[streamKey]StreamCheckpoint{newKey: checkpoint}
		runtime.publisher.state.mu.Unlock()
		runtime.mu.Lock()
		delete(runtime.tails, key)
		runtime.tails[newKey] = producerTail{
			sequence: checkpoint.Sequence, payload: checkpoint.PayloadSHA256,
			envelope: checkpoint.EnvelopeID, projection: checkpoint.ProjectionVersion, terminal: true,
		}
		runtime.mu.Unlock()
		provider.set(retirementRevisionFence(19, "building"))
		fences, _ := provider.CurrentRevisions(context.Background())
		if err := runtime.compactProducerState(context.Background(), fences); err != nil {
			t.Fatal(err)
		}
		if _, exists := runtime.publisher.state.snapshot()[newKey]; !exists {
			t.Fatal("checkpoint newer than the active retirement high-water was retired")
		}
	})

	t.Run("cross_tenant", func(t *testing.T) {
		runtime, provider, _, cfg := newRetirementRuntime(t)
		key := acknowledgeRetirementTerminal(t, runtime, provider, false)
		writePythonRetirementFixture(t, cfg.SpoolDirectory)
		checkpoint := runtime.publisher.state.snapshot()[key]
		checkpoint.WorkspaceID = testWorkspaceTwo
		newKey := checkpointKey(checkpoint)
		runtime.publisher.state.mu.Lock()
		runtime.publisher.state.checkpoints = map[streamKey]StreamCheckpoint{newKey: checkpoint}
		runtime.publisher.state.mu.Unlock()
		otherFence := retirementRevisionFence(18, "building")
		otherFence.WorkspaceID = testWorkspaceTwo
		provider.set(otherFence)
		provider.set(retirementRevisionFence(18, "building"))
		fences, _ := provider.CurrentRevisions(context.Background())
		if err := runtime.compactProducerState(context.Background(), fences); err != nil {
			t.Fatal(err)
		}
		if _, exists := runtime.publisher.state.snapshot()[newKey]; !exists {
			t.Fatal("one tenant's retirement proof removed another tenant's checkpoint")
		}
	})
}
