package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
)

func TestCandidateV1WireCompatibility(t *testing.T) {
	_, candidate := testCandidateRecord(t, "property-candidates-v1-test", 41, "legacy")
	raw, err := candidate.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	// Captured from the v1 implementation before adding managed candidates.
	if testDigest(string(raw)) != "bd8fca67471fb7bdc0bf86603ffebe15e55a7867a7c71a6e0c5d8fbbc2a407a0" ||
		candidate.Snapshot().CandidateID != "a0b2467076d202031c562a4a2323c869d13521491e5a23931f319a02d9d284db" ||
		candidate.Snapshot().Version != CandidateVersion {
		t.Fatal("legacy candidate bytes or identity changed")
	}
	parsed, err := ParseWireCandidate(raw)
	if err != nil {
		t.Fatal(err)
	}
	after, err := parsed.MarshalBinary()
	if err != nil || !bytes.Equal(raw, after) {
		t.Fatalf("legacy parse changed bytes: %v", err)
	}
}

func managedCandidateConfig(t *testing.T) RuntimeConfig {
	t.Helper()
	cfg := candidateRuntimeConfig(t)
	cfg.CatalogEpoch, cfg.ProjectionVersion = 0, 0
	cfg.ProducerStreamID, cfg.SpoolDirectory, cfg.RevisionFenceFile = "", "", ""
	cfg.WorkspaceAllowlist = nil
	return cfg
}

func managedCandidate(t *testing.T) WireCandidate {
	t.Helper()
	return mustCandidates(t, managedCandidateConfig(t), []ScopedSpan{
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testSeen, map[string]string{"a": "one"}, nil)),
	})[0]
}

func TestCandidateV2ConfigOnlyAllowsManagedZerosInKafkaMode(t *testing.T) {
	for _, mode := range []RuntimeMode{RuntimeKafka, RuntimeSequencer, RuntimeDirectKafkaDevelopment} {
		for _, coordinates := range [][2]uint16{{0, 0}, {0, 1}, {1, 0}, {3, 1}, {1, 3}} {
			t.Run(fmt.Sprintf("%s/%d_%d", mode, coordinates[0], coordinates[1]), func(t *testing.T) {
				cfg := validRuntimeConfig(t)
				cfg.Mode = mode
				cfg.CatalogEpoch, cfg.ProjectionVersion = coordinates[0], coordinates[1]
				defaults := cfg.WithDefaults()
				if defaults.CatalogEpoch != coordinates[0] || defaults.ProjectionVersion != coordinates[1] {
					t.Fatal("WithDefaults changed catalog coordinates")
				}
				wantValid := (coordinates[0] > 0 && coordinates[1] > 0) ||
					(mode == RuntimeKafka && coordinates == [2]uint16{0, 0})
				if err := defaults.Validate(); (err == nil) != wantValid {
					t.Fatalf("valid=%v, error=%v", wantValid, err)
				}
			})
		}
	}
	if err := managedCandidateConfig(t).Validate(); err != nil {
		t.Fatalf("managed collector required catalog identity or state: %v", err)
	}
}

func TestCandidateV2PreservesCanonicalValueProjectionAndDeterminism(t *testing.T) {
	row := scopedHotRow(testWorkspace, testProject, hotRow(
		testProject, testSeen, map[string]string{"plan": "Pro"}, map[string]float64{"score": 1.5},
	))
	row.Row["attrs_bool"] = map[string]uint8{"ok": 1}
	row.Row["attributes_extra"] = map[string]any{"tags": []any{"A", "B"}}
	row.Row["model"] = "Model-X"
	other := scopedHotRow(testWorkspace, testProject, hotRow(testProject, testLastSeen, map[string]string{"plan": "Pro"}, nil))
	rows := []ScopedSpan{row, other}
	legacy := mustCandidates(t, candidateRuntimeConfig(t), rows)[0].Snapshot()
	forward := mustCandidates(t, managedCandidateConfig(t), rows)[0]
	reverse := mustCandidates(t, managedCandidateConfig(t), []ScopedSpan{other, row})[0]
	snapshot := forward.Snapshot()
	if snapshot.Version != CandidateManagedVersion || snapshot.CatalogEpoch != 0 || snapshot.ProjectionVersion != 0 ||
		len(snapshot.Values) != 6 || !reflect.DeepEqual(snapshot.Values, legacy.Values) ||
		!reflect.DeepEqual(snapshot.GapReasons, legacy.GapReasons) || snapshot.SourceRows != legacy.SourceRows {
		t.Fatalf("managed value projection differs from v1: %+v", snapshot)
	}
	raw, err := forward.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	reversed, err := reverse.MarshalBinary()
	if err != nil || !bytes.Equal(raw, reversed) {
		t.Fatalf("managed bytes depend on input order: %v", err)
	}
	parsed, err := ParseWireCandidate(raw)
	if err != nil || !reflect.DeepEqual(parsed.Snapshot(), snapshot) {
		t.Fatalf("managed round trip failed: %v", err)
	}
	snapshot.Values[0].ValueJSON = `"mutated snapshot"`
	after, err := forward.MarshalBinary()
	if err != nil || !bytes.Equal(raw, after) || reflect.DeepEqual(forward.Snapshot().Values, snapshot.Values) {
		t.Fatal("snapshot mutation changed immutable candidate")
	}
}

func candidateDocumentBytes(t *testing.T, document candidateJSON) []byte {
	t.Helper()
	unsigned, err := json.Marshal(candidateUnsigned(document))
	if err != nil {
		t.Fatal(err)
	}
	document.CandidateID = testDigest(string(unsigned))
	raw, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestCandidateV2RejectsInvalidVersionsCoordinatesAndWireBytes(t *testing.T) {
	base := managedCandidate(t)
	for _, test := range []struct {
		name       string
		version    uint16
		epoch      uint16
		projection uint16
	}{
		{"unknown zero version", 0, 0, 0},
		{"unknown future version", 3, 0, 0},
		{"v1 zero coordinates", 1, 0, 0},
		{"v1 missing epoch", 1, 0, 1},
		{"v1 missing projection", 1, 1, 0},
		{"v2 bound coordinates", 2, 3, 1},
		{"v2 bound epoch", 2, 3, 0},
		{"v2 bound projection", 2, 0, 1},
	} {
		t.Run(test.name, func(t *testing.T) {
			document := base.document
			document.Version, document.CatalogEpoch, document.ProjectionVersion = test.version, test.epoch, test.projection
			if _, err := ParseWireCandidate(candidateDocumentBytes(t, document)); err == nil {
				t.Fatal("invalid version/coordinates accepted with a valid digest")
			}
		})
	}
	raw, err := base.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	for name, malformed := range map[string][]byte{
		"unknown format":  bytes.Replace(raw, []byte(CandidateFormat), []byte(CandidateFormat+"-unknown"), 1),
		"corrupt digest":  bytes.Replace(raw, []byte(base.Snapshot().CandidateID), []byte(strings.Repeat("0", 64)), 1),
		"unknown field":   append(bytes.Clone(raw[:len(raw)-1]), []byte(`,"unknown":true}`)...),
		"duplicate field": append(bytes.Clone(raw[:len(raw)-1]), []byte(`,"version":2}`)...),
		"whitespace":      append([]byte(" "), raw...),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := ParseWireCandidate(malformed); err == nil {
				t.Fatal("malformed managed candidate accepted")
			}
		})
	}
}

type rotatingCandidateV2Provider struct {
	*FileRevisionProvider
	rotate func()
}

func (p *rotatingCandidateV2Provider) CurrentRevision(ctx context.Context, organizationID, workspaceID string) (RevisionFence, error) {
	fence, err := p.FileRevisionProvider.CurrentRevision(ctx, organizationID, workspaceID)
	if p.rotate != nil {
		p.rotate()
		p.rotate = nil
	}
	return fence, err
}

func TestCandidateV2FenceRotationCannotSpoolObsoleteBinding(t *testing.T) {
	runtime, provider, _ := newCandidateV2Runtime(t, 3, 1)
	runtime.revisions = &rotatingCandidateV2Provider{
		FileRevisionProvider: provider,
		rotate: func() {
			writeCandidateTestFence(t, runtime.cfg.RevisionFenceFile, testRevisionFence(18, "building"))
		},
	}
	candidate := managedCandidate(t)
	if _, err := runtime.AcceptCandidate(candidate); err == nil || !strings.Contains(err.Error(), "rotated") ||
		errors.Is(err, ErrCandidateNotAdmitted) {
		t.Fatalf("fence rotation did not remain retryable: %v", err)
	}
	if pending, err := runtime.spool.PendingEnvelopes(); err != nil || len(pending) != 0 {
		t.Fatalf("obsolete binding was spooled: count=%d error=%v", len(pending), err)
	}
	if duplicate, err := runtime.AcceptCandidate(candidate); err != nil || duplicate {
		t.Fatalf("retry failed to bind current revision: duplicate=%v error=%v", duplicate, err)
	}
	pending, err := runtime.spool.PendingEnvelopes()
	if err != nil || len(pending) != 1 || pending[0].Snapshot().CatalogRevision != 18 ||
		pending[0].Snapshot().Payload.SourceBatchDigest != candidate.Snapshot().CandidateID {
		t.Fatalf("retry did not preserve candidate identity in current revision: %+v error=%v", pending, err)
	}
}

func writeCandidateTestFence(t *testing.T, path string, fence RevisionFence) {
	t.Helper()
	raw, err := EncodeRevisionFenceFile([]RevisionFence{fence})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
}

func newCandidateV2Runtime(t *testing.T, epoch, projection uint16) (*HotRuntime, *FileRevisionProvider, *recordingEnvelopePublisher) {
	t.Helper()
	cfg := validRuntimeConfig(t).WithDefaults()
	cfg.Mode = RuntimeSequencer
	cfg.CatalogEpoch, cfg.ProjectionVersion = epoch, projection
	fence := testRevisionFence(17, "building")
	fence.CatalogEpoch, fence.ProjectionVersion = epoch, projection
	writeCandidateTestFence(t, cfg.RevisionFenceFile, fence)
	provider, err := NewFileRevisionProvider(cfg.RevisionFenceFile)
	if err != nil {
		t.Fatal(err)
	}
	now, err := time.Parse(dateTime64Layout, testSeen)
	if err != nil {
		t.Fatal(err)
	}
	provider.now = func() time.Time { return now }
	publisher := &recordingEnvelopePublisher{}
	runtime, err := NewHotRuntime(cfg, provider, publisher)
	if err != nil {
		t.Fatal(err)
	}
	return runtime, provider, publisher
}

func TestCandidateV2BindsOnlyCopiedRowsToValidatedFence(t *testing.T) {
	candidate := managedCandidate(t)
	raw, err := candidate.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	for _, coordinates := range [][2]uint16{{3, 1}, {1, 3}, {7, 3}} {
		t.Run(fmt.Sprintf("%d_%d", coordinates[0], coordinates[1]), func(t *testing.T) {
			runtime, _, _ := newCandidateV2Runtime(t, coordinates[0], coordinates[1])
			if duplicate, err := runtime.AcceptCandidate(candidate); err != nil || duplicate {
				t.Fatalf("first acceptance duplicate=%v error=%v", duplicate, err)
			}
			pending, err := runtime.spool.PendingEnvelopes()
			if err != nil || len(pending) != 1 {
				t.Fatalf("ordered spool count=%d error=%v", len(pending), err)
			}
			envelope := pending[0].Snapshot()
			if envelope.CatalogEpoch != coordinates[0] || envelope.ProjectionVersion != coordinates[1] ||
				envelope.Sequence != 1 || envelope.Payload.SourceBatchDigest != candidate.Snapshot().CandidateID ||
				envelope.Payload.ValueRows != 1 || len(envelope.Payload.Chunks) != 1 {
				t.Fatalf("incorrect ordered binding: %+v", envelope)
			}
			var row AttributeValueRow
			if err := json.Unmarshal(bytes.TrimSpace(envelope.Payload.Chunks[0].JSONEachRow), &row); err != nil {
				t.Fatal(err)
			}
			if row.CatalogEpoch != coordinates[0] || row.CatalogRevision != envelope.CatalogRevision ||
				row.BuildToken != envelope.BuildToken || row.ValueJSON != candidate.Snapshot().Values[0].ValueJSON ||
				row.ValueFingerprint != candidate.Snapshot().Values[0].ValueFingerprint {
				t.Fatalf("value row was not bound without changing its value: %+v", row)
			}
			after, err := candidate.MarshalBinary()
			if err != nil || !bytes.Equal(raw, after) || candidate.Snapshot().CatalogEpoch != 0 || candidate.Snapshot().ProjectionVersion != 0 {
				t.Fatal("binding rewrote the original candidate")
			}
		})
	}
}

func TestCandidateV2DoesNotWeakenLegacyOrFenceAdmission(t *testing.T) {
	for _, coordinates := range [][2]uint16{{7, 1}, {3, 3}} {
		t.Run(fmt.Sprintf("v1_exact_match_%d_%d", coordinates[0], coordinates[1]), func(t *testing.T) {
			runtime, _, _ := newCandidateV2Runtime(t, coordinates[0], coordinates[1])
			_, legacy := testCandidateRecord(t, "property-candidates-v1-test", 1, "legacy")
			if _, err := runtime.AcceptCandidate(legacy); err == nil || errors.Is(err, ErrCandidateNotAdmitted) {
				t.Fatalf("legacy identity mismatch did not fail closed: %v", err)
			}
		})
	}
	for _, test := range []struct {
		name   string
		mutate func(*RevisionFence)
		skip   bool
	}{
		{"epoch conflict", func(f *RevisionFence) { f.CatalogEpoch++ }, false},
		{"projection conflict", func(f *RevisionFence) { f.ProjectionVersion++ }, false},
		{"expired lease", func(f *RevisionFence) { f.ExpiresAt = testSeen }, false},
		{"other workspace", func(f *RevisionFence) { f.WorkspaceID = testWorkspaceTwo }, true},
		{"other project", func(f *RevisionFence) { f.ProjectIDs = []string{testProjectTwo} }, true},
		{"fenced build", func(f *RevisionFence) { f.Status = "fenced" }, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			runtime, _, _ := newCandidateV2Runtime(t, 3, 1)
			fence := testRevisionFence(17, "building")
			test.mutate(&fence)
			writeCandidateTestFence(t, runtime.cfg.RevisionFenceFile, fence)
			if _, err := runtime.AcceptCandidate(managedCandidate(t)); err == nil || errors.Is(err, ErrCandidateNotAdmitted) != test.skip {
				t.Fatalf("invalid managed admission skip=%v error=%v", test.skip, err)
			}
			if pending, err := runtime.spool.PendingEnvelopes(); err != nil || len(pending) != 0 {
				t.Fatalf("rejected candidate changed ordered spool: count=%d error=%v", len(pending), err)
			}
		})
	}
}

func TestCandidateV2ReplayAndRestartKeepOneOrderedEffect(t *testing.T) {
	for _, acknowledged := range []bool{false, true} {
		t.Run(fmt.Sprintf("acknowledged_%v", acknowledged), func(t *testing.T) {
			candidate := managedCandidate(t)
			raw, err := candidate.MarshalBinary()
			if err != nil {
				t.Fatal(err)
			}
			topic := "property-candidates-v2-test"
			record := catalogkafka.Record{Topic: topic, Partition: 2, Offset: 41, LeaderEpoch: 7, Key: []byte(testWorkspace), Value: raw}
			directory := t.TempDir()
			store := testReceiptStore(t, directory, topic)
			if _, completed, err := store.Receive(record); err != nil || completed {
				t.Fatalf("receive completed=%v error=%v", completed, err)
			}
			runtime, provider, publisher := newCandidateV2Runtime(t, 1, 3)
			if duplicate, err := runtime.AcceptCandidate(candidate); err != nil || duplicate {
				t.Fatalf("initial acceptance duplicate=%v error=%v", duplicate, err)
			}
			if duplicate, err := runtime.AcceptCandidate(candidate); err != nil || !duplicate {
				t.Fatalf("same-process retry duplicate=%v error=%v", duplicate, err)
			}
			if acknowledged {
				if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err != nil {
					t.Fatal(err)
				}
			}
			// Crash before receipt completion: recover from either the pending
			// ordered spool or the ACK checkpoint after spool removal.
			restarted, err := NewHotRuntime(runtime.cfg, provider, publisher)
			if err != nil {
				t.Fatal(err)
			}
			if duplicate, err := restarted.AcceptCandidate(candidate); err != nil || !duplicate {
				t.Fatalf("restart retry duplicate=%v error=%v", duplicate, err)
			}
			store = testReceiptStore(t, directory, topic)
			source := &scriptedCandidateSource{record: record, store: store}
			sequencer, err := NewCandidateSequencer(topic, source, store, restarted)
			if err != nil {
				t.Fatal(err)
			}
			if err := sequencer.ReplayPending(context.Background()); err != nil {
				t.Fatal(err)
			}
			if pending, err := store.Pending(); err != nil || len(pending) != 0 {
				t.Fatalf("replayed receipt remained: count=%d error=%v", len(pending), err)
			}
			if _, err := restarted.spool.Replay(context.Background(), restarted.publisher); err != nil {
				t.Fatal(err)
			}
			// Completion survives another restart and deduplicates both the
			// original Kafka coordinate and a repeated candidate at a new offset.
			store = testReceiptStore(t, directory, topic)
			for _, offset := range []int64{41, 42} {
				record.Offset = offset
				if _, completed, err := store.Receive(record); err != nil || !completed {
					t.Fatalf("redelivery offset=%d completed=%v error=%v", offset, completed, err)
				}
			}
			if len(publisher.envelopes) != 1 || publisher.envelopes[0].Sequence != 1 ||
				publisher.envelopes[0].Payload.SourceBatchDigest != candidate.Snapshot().CandidateID {
				t.Fatalf("replay changed ordered effects: %+v", publisher.envelopes)
			}
		})
	}
}
