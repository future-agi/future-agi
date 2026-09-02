package propertycatalog

import (
	"bytes"
	"context"
	"errors"
	"os"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
)

const testWorkspaceTwo = "77777777-7777-4777-8777-777777777777"

type mutableRevisionProvider struct {
	mu     sync.Mutex
	fences map[string]RevisionFence
}

type blockingAdmissionRevisionProvider struct {
	base    *mutableRevisionProvider
	once    sync.Once
	entered chan struct{}
	release chan struct{}
}

func (p *blockingAdmissionRevisionProvider) CurrentRevision(
	ctx context.Context, organizationID, workspaceID string,
) (RevisionFence, error) {
	fence, err := p.base.CurrentRevision(ctx, organizationID, workspaceID)
	if err != nil {
		return RevisionFence{}, err
	}
	block := false
	p.once.Do(func() { block = true })
	if block {
		close(p.entered)
		select {
		case <-p.release:
		case <-ctx.Done():
			return RevisionFence{}, ctx.Err()
		}
	}
	return fence, nil
}

func (p *blockingAdmissionRevisionProvider) CurrentRevisions(ctx context.Context) ([]RevisionFence, error) {
	return p.base.CurrentRevisions(ctx)
}

func (p *mutableRevisionProvider) CurrentRevision(_ context.Context, organizationID, workspaceID string) (RevisionFence, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	fence, ok := p.fences[organizationID+"/"+workspaceID]
	if !ok {
		return RevisionFence{}, ErrRevisionNotAssigned
	}
	return fence, nil
}

func (p *mutableRevisionProvider) CurrentRevisions(_ context.Context) ([]RevisionFence, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	fences := make([]RevisionFence, 0, len(p.fences))
	for _, fence := range p.fences {
		fences = append(fences, fence)
	}
	sort.Slice(fences, func(i, j int) bool {
		if fences[i].OrganizationID != fences[j].OrganizationID {
			return fences[i].OrganizationID < fences[j].OrganizationID
		}
		return fences[i].WorkspaceID < fences[j].WorkspaceID
	})
	return fences, nil
}

func (p *mutableRevisionProvider) set(fence RevisionFence) {
	p.mu.Lock()
	defer p.mu.Unlock()
	fence.FenceSHA256 = RevisionFenceSHA256(fence)
	p.fences[fence.OrganizationID+"/"+fence.WorkspaceID] = fence
}

type recordingEnvelopePublisher struct {
	mu        sync.Mutex
	envelopes []EnvelopeSnapshot
	err       error
}

func (p *recordingEnvelopePublisher) Publish(_ context.Context, envelope WireEnvelope) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.err != nil {
		return p.err
	}
	p.envelopes = append(p.envelopes, envelope.Snapshot())
	return nil
}

func hotRow(projectID string, when string, stringsMap map[string]string, numbersMap map[string]float64) map[string]any {
	return map[string]any{
		"org_id": testOrganization, "project_id": projectID, "start_time": when,
		"attrs_string": stringsMap, "attrs_number": numbersMap,
		"attrs_bool": map[string]uint8{}, "attributes_extra": map[string]any{},
		"resource_attrs": map[string]any{"unchanged": "canonical"},
	}
}

func scopedHotRow(workspaceID, projectID string, row map[string]any) ScopedSpan {
	return ScopedSpan{OrganizationID: testOrganization, WorkspaceID: workspaceID, Row: row}
}

func testMutableProvider(revision uint64, workspaces ...string) *mutableRevisionProvider {
	provider := &mutableRevisionProvider{fences: make(map[string]RevisionFence)}
	for _, workspace := range workspaces {
		fence := testRevisionFence(revision, "building")
		fence.WorkspaceID = workspace
		provider.set(fence)
	}
	return provider
}

func drainingTestFence(revision, fencedSequence uint64) RevisionFence {
	fence := testRevisionFence(revision, "draining")
	fence.DrainDeadline = "2026-08-14 12:01:30.000000"
	fence.FencedSequence = fencedSequence
	fence.FenceSHA256 = RevisionFenceSHA256(fence)
	return fence
}

func prepareHotDrain(
	t *testing.T, runtime *HotRuntime, provider *mutableRevisionProvider, revision uint64,
) DrainProof {
	t.Helper()
	provider.set(drainingTestFence(revision, 0))
	if err := runtime.observeDraining(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err != nil {
		t.Fatal(err)
	}
	if err := runtime.persistDrainProofs(context.Background()); err != nil {
		t.Fatal(err)
	}
	proofs, err := runtime.DrainProofs(context.Background())
	if err != nil || len(proofs) != 1 || proofs[0].Phase != "prepared" || proofs[0].Ready {
		t.Fatalf("prepared proofs=%+v err=%v", proofs, err)
	}
	return proofs[0]
}

func bindHotDrain(
	t *testing.T, runtime *HotRuntime, provider *mutableRevisionProvider, revision uint64, proof DrainProof,
) {
	t.Helper()
	provider.set(drainingTestFence(revision, proof.TerminalSequence))
	if err := runtime.observeDraining(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestHotPathIsValueOnlyAcrossProjectsAndMixedTypes(t *testing.T) {
	cfg := validRuntimeConfig(t)
	cfg = cfg.WithDefaults()
	groups, errs := collectHotGroups(cfg, []ScopedSpan{
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testSeen, map[string]string{"shared": "text"}, map[string]float64{})),
		scopedHotRow(testWorkspace, testProjectTwo, hotRow(testProjectTwo, testLastSeen, map[string]string{}, map[string]float64{"shared": 42})),
	})
	if len(errs) != 0 || len(groups) != 2 {
		t.Fatalf("groups=%d errs=%v", len(groups), errs)
	}
	fence := testRevisionFence(17, "building")
	first, err := buildHotEnvelope(cfg, fence, groups[0], 1, ZeroSHA256)
	if err != nil {
		t.Fatal(err)
	}
	second, err := buildHotEnvelope(cfg, fence, groups[1], 2, first.PayloadSHA256())
	if err != nil {
		t.Fatal(err)
	}
	for _, snapshot := range []EnvelopeSnapshot{first.Snapshot(), second.Snapshot()} {
		if snapshot.Payload.DefinitionRows != 0 || snapshot.Payload.ValueRows != 1 {
			t.Fatalf("value-only counts=%+v", snapshot.Payload)
		}
		for _, chunk := range snapshot.Payload.Chunks {
			if chunk.Table != AttributeValueTable {
				t.Fatalf("hot envelope targeted %q", chunk.Table)
			}
		}
	}
}

func TestHotAdmissionRejectsMixedProjectAndHalfOpenBoundaryAtomically(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	provider := testMutableProvider(17, testWorkspace)
	fence := testRevisionFence(17, "building")
	fence.ProjectIDs = []string{testProject}
	provider.set(fence)
	runtime, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatal(err)
	}

	inScope := scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, testSeen, map[string]string{"accepted": "yes"}, map[string]float64{}),
	)
	outOfScopeProject := scopedHotRow(
		testWorkspace, testProjectTwo,
		hotRow(testProjectTwo, testSeen, map[string]string{"rejected": "project"}, map[string]float64{}),
	)
	if submission, err := runtime.admitSubmission([]ScopedSpan{inScope, outOfScopeProject}); err == nil ||
		!strings.Contains(err.Error(), "project is outside") || len(submission.streams) != 0 ||
		len(runtime.pendingAdmissions) != 0 {
		t.Fatalf("mixed-project admission=%+v pending=%v err=%v", submission, runtime.pendingAdmissions, err)
	}

	_, spanUntilUS := testSpanWindow()
	atExclusiveUpper := time.UnixMicro(int64(spanUntilUS)).UTC().Format(dateTime64Layout)
	upperRow := scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, atExclusiveUpper, map[string]string{"rejected": "upper"}, map[string]float64{}),
	)
	if submission, err := runtime.admitSubmission([]ScopedSpan{upperRow}); err == nil ||
		!strings.Contains(err.Error(), "half-open") || len(submission.streams) != 0 ||
		len(runtime.pendingAdmissions) != 0 {
		t.Fatalf("exclusive-upper admission=%+v pending=%v err=%v", submission, runtime.pendingAdmissions, err)
	}

	insideUpper := time.UnixMicro(int64(spanUntilUS - 1)).UTC().Format(dateTime64Layout)
	for _, row := range []ScopedSpan{
		inScope,
		scopedHotRow(
			testWorkspace, testProject,
			hotRow(testProject, insideUpper, map[string]string{"accepted": "upper-minus-one"}, map[string]float64{}),
		),
	} {
		submission, err := runtime.admitSubmission([]ScopedSpan{row})
		if err != nil || len(submission.streams) != 1 {
			t.Fatalf("in-scope boundary admission=%+v err=%v", submission, err)
		}
		if err := runtime.completeSubmission(submission, nil); err != nil {
			t.Fatal(err)
		}
	}
}

func TestRevisionFenceWorkspaceScopeAdmitsOnlyExactCurrentFence(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	cfg.WorkspaceScopeMode = WorkspaceScopeRevisionFence
	cfg.WorkspaceAllowlist = nil
	provider, err := NewFileRevisionProvider(cfg.RevisionFenceFile)
	if err != nil {
		t.Fatal(err)
	}
	provider.now = func() time.Time {
		value, _ := time.Parse(dateTime64Layout, "2026-08-14 12:00:00.000000")
		return value
	}
	runtime, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatal(err)
	}
	row := scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, testSeen, map[string]string{"accepted": "fenced"}, map[string]float64{}),
	)
	if _, err := runtime.admitSubmission([]ScopedSpan{row}); err == nil {
		t.Fatal("fence-scoped admission accepted traffic before a fence existed")
	}

	raw, err := EncodeRevisionFenceFile([]RevisionFence{testRevisionFence(17, "building")})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cfg.RevisionFenceFile, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	submission, err := runtime.admitSubmission([]ScopedSpan{row})
	if err != nil || len(submission.assignments) != 1 || len(submission.streams) != 1 {
		t.Fatalf("exact fence admission=%+v err=%v", submission, err)
	}
	if err := runtime.completeSubmission(submission, nil); err != nil {
		t.Fatal(err)
	}

	unfenced := scopedHotRow(
		testWorkspaceTwo, testProject,
		hotRow(testProject, testSeen, map[string]string{"rejected": "unfenced"}, map[string]float64{}),
	)
	if _, err := runtime.admitSubmission([]ScopedSpan{unfenced}); err == nil ||
		!strings.Contains(err.Error(), "no revision assignment") {
		t.Fatalf("non-fenced tenant error=%v", err)
	}
}

func TestRevisionFenceWorkspaceScopeRejectsMalformedAndExpiredFences(t *testing.T) {
	for _, test := range []struct {
		name      string
		mutateRaw func([]byte) []byte
		now       string
		want      string
	}{
		{
			name: "malformed digest",
			mutateRaw: func(raw []byte) []byte {
				return bytes.Replace(raw, []byte(`"catalog_revision":17`), []byte(`"catalog_revision":18`), 1)
			},
			now:  "2026-08-14 12:00:00.000000",
			want: "digest",
		},
		{
			name: "expired lease", mutateRaw: func(raw []byte) []byte { return raw },
			now: "2026-08-14 12:03:00.000000", want: "expired",
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			cfg := validRuntimeConfig(t).WithDefaults()
			cfg.WorkspaceScopeMode = WorkspaceScopeRevisionFence
			cfg.WorkspaceAllowlist = nil
			raw, err := EncodeRevisionFenceFile([]RevisionFence{testRevisionFence(17, "building")})
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(cfg.RevisionFenceFile, test.mutateRaw(raw), 0o600); err != nil {
				t.Fatal(err)
			}
			provider, err := NewFileRevisionProvider(cfg.RevisionFenceFile)
			if err != nil {
				t.Fatal(err)
			}
			provider.now = func() time.Time {
				value, _ := time.Parse(dateTime64Layout, test.now)
				return value
			}
			runtime, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
			if err != nil {
				t.Fatal(err)
			}
			row := scopedHotRow(
				testWorkspace, testProject,
				hotRow(testProject, testSeen, map[string]string{"rejected": test.name}, map[string]float64{}),
			)
			if submission, err := runtime.admitSubmission([]ScopedSpan{row}); err == nil ||
				!strings.Contains(err.Error(), test.want) || len(submission.streams) != 0 ||
				len(runtime.pendingAdmissions) != 0 {
				t.Fatalf("invalid fence admission=%+v pending=%v err=%v", submission, runtime.pendingAdmissions, err)
			}
		})
	}
}

func TestHotValuesRemainRevisionLocalAcrossObservationGap(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	jan := "2026-01-10 12:00:00.000000"
	mar := "2026-03-10 12:00:00.000000"
	build := func(when string, revision uint64) AttributeValueRow {
		groups, errs := collectHotGroups(cfg, []ScopedSpan{scopedHotRow(
			testWorkspace, testProject,
			hotRow(testProject, when, map[string]string{"city": "Straße"}, map[string]float64{}),
		)})
		if len(errs) != 0 || len(groups) != 1 {
			t.Fatalf("groups=%d errs=%v", len(groups), errs)
		}
		fence := testRevisionFence(revision, "building")
		observed, _ := time.Parse(dateTime64Layout, when)
		fence.SpanSinceUS = uint64(observed.UnixMicro())
		fence.SpanUntilUS = uint64(observed.Add(time.Hour).UnixMicro())
		envelope, err := buildHotEnvelope(cfg, fence, groups[0], 1, ZeroSHA256)
		if err != nil {
			t.Fatal(err)
		}
		var value AttributeValueRow
		chunk := envelope.Snapshot().Payload.Chunks[0]
		if err := decodeCanonicalRow(bytes.TrimSuffix(chunk.JSONEachRow, []byte{'\n'}), &value); err != nil {
			t.Fatal(err)
		}
		return value
	}
	left := build(jan, 17)
	right := build(mar, 18)
	if left.CatalogRevision != 17 || right.CatalogRevision != 18 ||
		left.FirstSeen != jan || left.LastSeen != jan || right.FirstSeen != mar || right.LastSeen != mar {
		t.Fatalf("cross-revision observation windows merged: left=%+v right=%+v", left, right)
	}
	if left.ValueSearchTextFolded != "strasse" || right.ValueSearchTextFolded != "strasse" {
		t.Fatalf("Unicode folded search drift: left=%q right=%q", left.ValueSearchTextFolded, right.ValueSearchTextFolded)
	}
}

func TestHotValueCasefoldExpansionBecomesDurableGapInsteadOfPoison(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	groups, errs := collectHotGroups(cfg, []ScopedSpan{scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, testSeen, map[string]string{"expands": strings.Repeat("ß", 9_000)}, map[string]float64{}),
	)})
	if len(errs) != 0 || len(groups) != 1 {
		t.Fatalf("groups=%d errs=%v", len(groups), errs)
	}
	envelope, err := buildHotEnvelope(cfg, testRevisionFence(17, "building"), groups[0], 1, ZeroSHA256)
	if err != nil {
		t.Fatal(err)
	}
	snapshot := envelope.Snapshot()
	if snapshot.Payload.Outcome != OutcomeGap || snapshot.Payload.ValueRows != 0 ||
		len(snapshot.Payload.GapReasons) != 1 || snapshot.Payload.GapReasons[0] != "max_encoded_bytes" {
		t.Fatalf("casefold expansion result=%+v", snapshot.Payload)
	}
}

func TestHotValueOverJSONLimitKeepsLaterBoundedValue(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	// Exercise the property wire cap independently of the configurable
	// per-span builder budget.
	cfg.MaxEncodedBytesPerSpan = 4 * MaxValueJSONBytes
	oversized := strings.Repeat("x", MaxValueJSONBytes+1)
	groups, errs := collectHotGroups(cfg, []ScopedSpan{scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, testSeen, map[string]string{
			"a_oversized": oversized,
			"z_retained":  "ok",
		}, map[string]float64{}),
	)})
	if len(errs) != 0 || len(groups) != 1 {
		t.Fatalf("groups=%d errs=%v", len(groups), errs)
	}
	if len(groups[0].values) != 1 {
		t.Fatalf("retained values=%+v", groups[0].values)
	}
	for _, value := range groups[0].values {
		if value.AttributeKey != "z_retained" || value.ValueJSON != `"ok"` {
			t.Fatalf("unexpected retained value=%+v", value)
		}
	}
	if _, present := groups[0].gaps[attributecatalog.GapMaxEncodedBytes]; !present {
		t.Fatalf("oversized JSON gap missing: %+v", groups[0].gaps)
	}
}

func TestHotValueOverSearchLimitKeepsLaterBoundedValue(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	oversizedSearch := strings.Repeat("s", MaxValueSearchTextBytes+1)
	if len(oversizedSearch)+2 > MaxValueJSONBytes {
		t.Fatal("search-limit fixture unexpectedly exceeds the JSON limit")
	}
	groups, errs := collectHotGroups(cfg, []ScopedSpan{scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, testSeen, map[string]string{
			"a_oversized": oversizedSearch,
			"z_retained":  "ok",
		}, map[string]float64{}),
	)})
	if len(errs) != 0 || len(groups) != 1 {
		t.Fatalf("groups=%d errs=%v", len(groups), errs)
	}
	if len(groups[0].values) != 1 {
		t.Fatalf("retained values=%+v", groups[0].values)
	}
	for _, value := range groups[0].values {
		if value.AttributeKey != "z_retained" || value.ValueSearchTextFolded != "ok" {
			t.Fatalf("unexpected retained value=%+v", value)
		}
	}
	if _, present := groups[0].gaps[attributecatalog.GapMaxEncodedBytes]; !present {
		t.Fatalf("oversized search gap missing: %+v", groups[0].gaps)
	}
}

func TestHotRuntimeChainsPerWorkspaceAndResetsAtDynamicRevision(t *testing.T) {
	cfg := validRuntimeConfig(t, testWorkspace, testWorkspaceTwo).WithDefaults()
	provider := testMutableProvider(17, testWorkspace, testWorkspaceTwo)
	downstream := &recordingEnvelopePublisher{}
	runtime, err := NewHotRuntime(cfg, provider, downstream)
	if err != nil {
		t.Fatal(err)
	}
	runtime.stage([]ScopedSpan{
		scopedHotRow(testWorkspace, testProject, hotRow(testProject, testSeen, map[string]string{"a": "one"}, map[string]float64{})),
		scopedHotRow(testWorkspace, testProjectTwo, hotRow(testProjectTwo, testSeen, map[string]string{"b": "two"}, map[string]float64{})),
		scopedHotRow(testWorkspaceTwo, testProject, hotRow(testProject, testSeen, map[string]string{"c": "three"}, map[string]float64{})),
	})
	pending, err := runtime.spool.PendingEnvelopes()
	if err != nil || len(pending) != 3 {
		t.Fatalf("pending=%d err=%v", len(pending), err)
	}
	sequences := map[string][]uint64{}
	for _, envelope := range pending {
		snapshot := envelope.Snapshot()
		sequences[snapshot.WorkspaceID] = append(sequences[snapshot.WorkspaceID], snapshot.Sequence)
	}
	if got := sequences[testWorkspace]; len(got) != 2 || got[0] != 1 || got[1] != 2 {
		t.Fatalf("workspace one sequences=%v", got)
	}
	if got := sequences[testWorkspaceTwo]; len(got) != 1 || got[0] != 1 {
		t.Fatalf("workspace two sequences=%v", got)
	}

	rotated := testRevisionFence(18, "building")
	provider.set(rotated)
	runtime.stage([]ScopedSpan{scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, testLastSeen, map[string]string{}, map[string]float64{"a": 1}),
	)})
	pending, _ = runtime.spool.PendingEnvelopes()
	last := pending[len(pending)-1].Snapshot()
	if last.CatalogRevision != 18 || last.Sequence != 1 || last.PreviousPayloadSHA256 != ZeroSHA256 ||
		last.Payload.DefinitionRows != 0 {
		t.Fatalf("rotated envelope=%+v", last)
	}
}

func TestReplayRechecksFenceBeforeKafkaAndPersistsAckBeforeRemoval(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	provider := testMutableProvider(17, testWorkspace)
	downstream := &recordingEnvelopePublisher{}
	runtime, err := NewHotRuntime(cfg, provider, downstream)
	if err != nil {
		t.Fatal(err)
	}
	runtime.stage([]ScopedSpan{scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, testSeen, map[string]string{"a": "one"}, map[string]float64{}),
	)})
	provider.set(testRevisionFence(18, "building"))
	if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err == nil {
		t.Fatal("stale revision was published")
	}
	if len(downstream.envelopes) != 0 {
		t.Fatal("Kafka publisher ran after fence rotation")
	}
	provider.set(testRevisionFence(17, "building"))
	result, err := runtime.spool.Replay(context.Background(), runtime.publisher)
	if err != nil || result.Delivered != 1 || len(downstream.envelopes) != 1 {
		t.Fatalf("result=%+v published=%d err=%v", result, len(downstream.envelopes), err)
	}
	if pending, _ := runtime.spool.PendingEnvelopes(); len(pending) != 0 {
		t.Fatalf("acknowledged spool remained: %d", len(pending))
	}
	if len(runtime.publisher.state.snapshot()) != 1 {
		t.Fatal("durable producer acknowledgement missing")
	}
}

func TestZeroTrafficDrainEmitsAndAcknowledgesSequenceOneTerminal(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	provider := testMutableProvider(17, testWorkspace)
	downstream := &recordingEnvelopePublisher{}
	runtime, err := NewHotRuntime(cfg, provider, downstream)
	if err != nil {
		t.Fatal(err)
	}
	proof := prepareHotDrain(t, runtime, provider, 17)
	if proof.LastDataSequence != 0 || proof.TerminalSequence != 1 || proof.DeliveryCount != 0 {
		t.Fatalf("zero-traffic prepared proof=%+v", proof)
	}
	bindHotDrain(t, runtime, provider, 17, proof)
	pending, err := runtime.spool.PendingEnvelopes()
	if err != nil || len(pending) != 1 {
		t.Fatalf("pending=%d err=%v", len(pending), err)
	}
	terminal := pending[0].Snapshot()
	if !terminal.Terminal || terminal.Sequence != 1 || terminal.PreviousPayloadSHA256 != ZeroSHA256 ||
		terminal.Payload.SourceRows != 0 || terminal.Payload.ValueRows != 0 || len(terminal.Payload.Chunks) != 0 {
		t.Fatalf("quiet terminal=%+v", terminal)
	}
	if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err != nil {
		t.Fatal(err)
	}
	proofs, err := runtime.DrainProofs(context.Background())
	if err != nil || len(proofs) != 1 || !proofs[0].Ready || proofs[0].Poisoned {
		t.Fatalf("proofs=%+v err=%v", proofs, err)
	}
}

func TestTerminalOrdersAfterLastDataAndClosesStream(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	provider := testMutableProvider(17, testWorkspace)
	downstream := &recordingEnvelopePublisher{}
	runtime, err := NewHotRuntime(cfg, provider, downstream)
	if err != nil {
		t.Fatal(err)
	}
	runtime.stage([]ScopedSpan{scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, testSeen, map[string]string{"plan": "PRO"}, map[string]float64{}),
	)})
	proof := prepareHotDrain(t, runtime, provider, 17)
	if proof.LastDataSequence != 1 || proof.TerminalSequence != 2 || proof.ValueCount != 1 {
		t.Fatalf("data prepared proof=%+v", proof)
	}
	bindHotDrain(t, runtime, provider, 17, proof)
	pending, _ := runtime.spool.PendingEnvelopes()
	if len(pending) != 1 || !pending[0].Snapshot().Terminal || pending[0].Snapshot().Sequence != 2 ||
		pending[0].Snapshot().PreviousPayloadSHA256 != downstream.envelopes[0].PayloadSHA256 {
		t.Fatalf("ordered pending=%+v", pending)
	}
	if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err != nil {
		t.Fatal(err)
	}
	if len(downstream.envelopes) != 2 || downstream.envelopes[0].Terminal ||
		!downstream.envelopes[1].Terminal {
		t.Fatalf("published=%+v", downstream.envelopes)
	}
	validator, _ := NewSequenceValidator(nil)
	// The data envelope was already drained before Python bound the terminal.
	dataGroups, _ := collectHotGroups(cfg, []ScopedSpan{scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, testSeen, map[string]string{"plan": "PRO"}, map[string]float64{}),
	)})
	dataEnvelope, _ := buildHotEnvelope(cfg, testRevisionFence(17, "building"), dataGroups[0], 1, ZeroSHA256)
	for _, envelope := range []WireEnvelope{dataEnvelope, pending[0]} {
		checked, err := validator.Check(envelope)
		if err != nil {
			t.Fatal(err)
		}
		if err := checked.Acknowledge(); err != nil {
			t.Fatal(err)
		}
	}
	groups, errs := collectHotGroups(cfg, []ScopedSpan{scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, testLastSeen, map[string]string{"late": "value"}, map[string]float64{}),
	)})
	if len(errs) != 0 || len(groups) != 1 {
		t.Fatalf("late groups=%d errs=%v", len(groups), errs)
	}
	afterTerminal, err := buildHotEnvelope(
		cfg, testRevisionFence(17, "building"), groups[0], 3, pending[0].PayloadSHA256(),
	)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := validator.Check(afterTerminal); !errors.Is(err, ErrSequenceConflict) {
		t.Fatalf("post-terminal advance=%v", err)
	}
}

func TestCrashAfterKafkaAckReplaysExactTokenDuringDrain(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	provider := testMutableProvider(17, testWorkspace)
	downstream := &recordingEnvelopePublisher{}
	runtime, err := NewHotRuntime(cfg, provider, downstream)
	if err != nil {
		t.Fatal(err)
	}
	runtime.stage([]ScopedSpan{scopedHotRow(
		testWorkspace, testProject,
		hotRow(testProject, testSeen, map[string]string{"a": "one"}, map[string]float64{}),
	)})
	runtime.publisher.state.persistHook = func(map[streamKey]StreamCheckpoint) error {
		return errors.New("simulated crash after Kafka ACK")
	}
	if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err == nil {
		t.Fatal("state-ACK failure was hidden")
	}
	if len(downstream.envelopes) != 1 {
		t.Fatalf("Kafka ACK count=%d", len(downstream.envelopes))
	}
	runtime.publisher.state.persistHook = nil
	provider.set(drainingTestFence(17, 0))
	if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err != nil {
		t.Fatal(err)
	}
	if err := runtime.observeDraining(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := runtime.persistDrainProofs(context.Background()); err != nil {
		t.Fatal(err)
	}
	prepared, err := runtime.DrainProofs(context.Background())
	if err != nil || len(prepared) != 1 || prepared[0].Phase != "prepared" {
		t.Fatalf("crash recovery did not prepare: %+v err=%v", prepared, err)
	}
	bindHotDrain(t, runtime, provider, 17, prepared[0])
	if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err != nil {
		t.Fatal(err)
	}
	if len(downstream.envelopes) != 3 || downstream.envelopes[0].EnvelopeID != downstream.envelopes[1].EnvelopeID ||
		!downstream.envelopes[2].Terminal {
		t.Fatalf("replayed envelopes=%+v", downstream.envelopes)
	}
	proofs, _ := runtime.DrainProofs(context.Background())
	if len(proofs) != 1 || !proofs[0].Ready {
		t.Fatalf("drain proof=%+v", proofs)
	}
}

func TestAcceptedAdmissionIsDurableAndCrashRecoveryPoisonsItsBuild(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	provider := testMutableProvider(17, testWorkspace)
	runtime, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatal(err)
	}
	row := scopedHotRow(testWorkspace, testProject, hotRow(
		testProject, testSeen, map[string]string{"accepted": "pending"}, map[string]float64{},
	))
	if err := runtime.EnqueueCanonicalSpans([]ScopedSpan{row}); err != nil {
		t.Fatal(err)
	}
	proofs, err := runtime.DrainProofs(context.Background())
	if err != nil || len(proofs) != 1 || proofs[0].PendingAdmissions != 1 || proofs[0].Poisoned {
		t.Fatalf("accepted proof=%+v err=%v", proofs, err)
	}
	raw, err := os.ReadFile(runtime.DrainProofPath())
	if err != nil || !bytes.Contains(raw, []byte(`"pending_admissions":1`)) {
		t.Fatalf("durable admission proof=%q err=%v", raw, err)
	}

	// Simulate process death: the first runtime's in-memory queue disappears,
	// while the fsynced proof remains in the dedicated spool directory.
	provider.set(drainingTestFence(17, 0))
	restarted, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatal(err)
	}
	proofs, err = restarted.DrainProofs(context.Background())
	if err != nil || len(proofs) != 1 || proofs[0].PendingAdmissions != 0 || !proofs[0].Poisoned || proofs[0].Ready {
		t.Fatalf("restart did not poison lost admission: proofs=%+v err=%v", proofs, err)
	}
	if err := restarted.observeDraining(context.Background()); err != nil {
		t.Fatal(err)
	}
	proofs, err = restarted.DrainProofs(context.Background())
	if err != nil || len(proofs) != 1 || proofs[0].TerminalIssued || !proofs[0].Poisoned || proofs[0].Ready {
		t.Fatalf("poisoned intent advanced=%+v err=%v", proofs, err)
	}
}

func TestAdmissionDrainRaceCannotTerminalizeBeforeDurablePoison(t *testing.T) {
	cfg := validRuntimeConfig(t).WithDefaults()
	base := testMutableProvider(17, testWorkspace)
	provider := &blockingAdmissionRevisionProvider{
		base: base, entered: make(chan struct{}), release: make(chan struct{}),
	}
	runtime, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
	if err != nil {
		t.Fatal(err)
	}
	row := scopedHotRow(testWorkspace, testProject, hotRow(
		testProject, testSeen, map[string]string{"racy": "accepted"}, map[string]float64{},
	))
	enqueueDone := make(chan error, 1)
	go func() { enqueueDone <- runtime.EnqueueCanonicalSpans([]ScopedSpan{row}) }()
	select {
	case <-provider.entered:
	case <-time.After(2 * time.Second):
		t.Fatal("admission did not reach the controlled building-fence read")
	}
	base.set(drainingTestFence(17, 0))
	drainDone := make(chan error, 1)
	go func() { drainDone <- runtime.observeDraining(context.Background()) }()
	close(provider.release)
	if err := <-enqueueDone; err != nil {
		t.Fatal(err)
	}
	if err := <-drainDone; err != nil {
		t.Fatal(err)
	}
	if pending, err := runtime.spool.PendingEnvelopes(); err != nil || len(pending) != 0 {
		t.Fatalf("terminal passed pending admission: pending=%d err=%v", len(pending), err)
	}
	proofs, err := runtime.DrainProofs(context.Background())
	if err != nil || len(proofs) != 1 || proofs[0].PendingAdmissions != 1 || proofs[0].TerminalIssued {
		t.Fatalf("racy pending proof=%+v err=%v", proofs, err)
	}

	submission := <-runtime.queue
	runtime.processSubmission(submission)
	if err := runtime.persistDrainProofs(context.Background()); err != nil {
		t.Fatal(err)
	}
	proofs, err = runtime.DrainProofs(context.Background())
	if err != nil || len(proofs) != 1 || proofs[0].PendingAdmissions != 0 || !proofs[0].Poisoned {
		t.Fatalf("draining rejection was not durably poisoned: proofs=%+v err=%v", proofs, err)
	}
	if err := runtime.observeDraining(context.Background()); err != nil {
		t.Fatal(err)
	}
	pending, err := runtime.spool.PendingEnvelopes()
	if err != nil || len(pending) != 0 {
		t.Fatalf("poisoned drain issued a terminal: pending=%+v err=%v", pending, err)
	}
}

func TestQueueFullAndInvalidRowsDurablyPoisonActivation(t *testing.T) {
	for _, test := range []struct {
		name  string
		stage func(*HotRuntime) error
	}{
		{
			name: "queue full",
			stage: func(runtime *HotRuntime) error {
				row := scopedHotRow(testWorkspace, testProject, hotRow(
					testProject, testSeen, map[string]string{"a": "one"}, map[string]float64{},
				))
				if err := runtime.EnqueueCanonicalSpans([]ScopedSpan{row}); err != nil {
					return err
				}
				return runtime.EnqueueCanonicalSpans([]ScopedSpan{row})
			},
		},
		{
			name: "invalid scoped row",
			stage: func(runtime *HotRuntime) error {
				row := scopedHotRow(testWorkspace, testProject, hotRow(
					testProject, testSeen, map[string]string{"a": "one"}, map[string]float64{},
				))
				row.Row["project_id"] = 42
				runtime.stage([]ScopedSpan{row})
				return nil
			},
		},
		{
			name: "project workspace mismatch",
			stage: func(runtime *HotRuntime) error {
				row := scopedHotRow(testWorkspace, testProject, hotRow(
					testProject, testSeen, map[string]string{"a": "one"}, map[string]float64{},
				))
				row.ScopeError = "project_workspace_mismatch"
				runtime.stage([]ScopedSpan{row})
				return nil
			},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			cfg := validRuntimeConfig(t).WithDefaults()
			cfg.QueueDepth = 1
			provider := testMutableProvider(17, testWorkspace)
			runtime, err := NewHotRuntime(cfg, provider, &recordingEnvelopePublisher{})
			if err != nil {
				t.Fatal(err)
			}
			_ = test.stage(runtime)
			pending, err := runtime.spool.PendingEnvelopes()
			if err != nil || len(pending) != 1 || pending[0].Snapshot().Payload.Outcome != OutcomeGap {
				t.Fatalf("durable gaps=%d err=%v", len(pending), err)
			}
			provider.set(drainingTestFence(17, 0))
			if err := runtime.observeDraining(context.Background()); err != nil {
				t.Fatal(err)
			}
			if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err != nil {
				t.Fatal(err)
			}
			proofs, _ := runtime.DrainProofs(context.Background())
			if len(proofs) != 1 || !proofs[0].Poisoned || proofs[0].Ready || proofs[0].TerminalIssued ||
				!proofs[0].GapIssued || !proofs[0].GapAcknowledged {
				t.Fatalf("activation was not poisoned: %+v", proofs)
			}
		})
	}
}
