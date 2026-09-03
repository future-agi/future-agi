package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

const (
	producerStateFormat         = "futureagi.property-catalog-producer-state"
	producerStateVersion        = uint16(3)
	producerStateFileName       = "producer-ack-state-v3.json"
	legacyProducerStateVersion  = uint16(2)
	legacyProducerStateFileName = "producer-ack-state-v2.json"
	maxProducerStateBytes       = 4 << 20
	emptySHA256                 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)

type producerStateDocument struct {
	Format      string             `json:"format"`
	Version     uint16             `json:"version"`
	Checkpoints []StreamCheckpoint `json:"checkpoints"`
}

type producerStateStore struct {
	mu          sync.Mutex
	path        string
	directory   string
	checkpoints map[streamKey]StreamCheckpoint
	persistHook func(map[streamKey]StreamCheckpoint) error
}

func loadProducerState(directory string) (*producerStateStore, error) {
	store := &producerStateStore{
		path: filepath.Join(directory, producerStateFileName), directory: directory,
		checkpoints: make(map[streamKey]StreamCheckpoint),
	}
	raw, err := os.ReadFile(store.path)
	if errors.Is(err, os.ErrNotExist) {
		raw, err = os.ReadFile(filepath.Join(directory, legacyProducerStateFileName))
		if errors.Is(err, os.ErrNotExist) {
			return store, nil
		}
	}
	if err != nil {
		return nil, fmt.Errorf("propertycatalog: read producer state: %w", err)
	}
	if len(raw) < 2 || len(raw) > maxProducerStateBytes || raw[len(raw)-1] != '\n' ||
		bytes.Contains(raw[:len(raw)-1], []byte{'\n'}) {
		return nil, errors.New("propertycatalog: producer state is not one bounded canonical JSON line")
	}
	body := raw[:len(raw)-1]
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	var document producerStateDocument
	if err := decoder.Decode(&document); err != nil {
		return nil, fmt.Errorf("propertycatalog: decode producer state: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return nil, err
	}
	canonical, err := json.Marshal(document)
	if err != nil || !bytes.Equal(canonical, body) {
		return nil, errors.New("propertycatalog: producer state is not canonical JSON")
	}
	if document.Format != producerStateFormat ||
		(document.Version != producerStateVersion && document.Version != legacyProducerStateVersion) {
		return nil, errors.New("propertycatalog: producer state format/version is unsupported")
	}
	for index, checkpoint := range document.Checkpoints {
		if err := validateCheckpoint(checkpoint); err != nil {
			return nil, fmt.Errorf("propertycatalog: producer checkpoint %d: %w", index, err)
		}
		key := checkpointKey(checkpoint)
		if _, exists := store.checkpoints[key]; exists {
			return nil, errors.New("propertycatalog: producer state contains a duplicate stream")
		}
		store.checkpoints[key] = checkpoint
	}
	return store, nil
}

func (s *producerStateStore) acknowledge(snapshot EnvelopeSnapshot) error {
	if s == nil {
		return errors.New("propertycatalog: nil producer state")
	}
	checkpoint := StreamCheckpoint{
		OrganizationID: snapshot.OrganizationID, WorkspaceID: snapshot.WorkspaceID,
		CatalogEpoch: snapshot.CatalogEpoch, CatalogRevision: snapshot.CatalogRevision,
		BuildToken:        snapshot.BuildToken,
		ProjectionVersion: snapshot.ProjectionVersion, SourceAdapter: snapshot.SourceAdapter,
		ProducerStreamID: snapshot.ProducerStreamID, Sequence: snapshot.Sequence,
		Terminal:      snapshot.Terminal,
		GapSeen:       snapshot.Payload.Outcome == OutcomeGap,
		PayloadSHA256: snapshot.PayloadSHA256, EnvelopeID: snapshot.EnvelopeID,
		SourceRows: snapshot.Payload.SourceRows, DefinitionRows: snapshot.Payload.DefinitionRows,
		ValueRows: snapshot.Payload.ValueRows, TombstoneRows: snapshot.Payload.TombstoneRows,
		DeliveryCount: 1, SourceDigest: emptySHA256,
		LastSourceBatchDigest: snapshot.Payload.SourceBatchDigest,
		EmittedDigest: framedSHA256(
			"futureagi.property-catalog.emitted-stream.v1", emptySHA256, snapshot.PayloadSHA256,
		),
	}
	if !snapshot.Terminal {
		checkpoint.SourceDigest = framedSHA256(
			"futureagi.property-catalog.hot-source-stream.v1", emptySHA256,
			snapshot.Payload.SourceBatchDigest,
		)
	}
	if err := validateCheckpoint(checkpoint); err != nil {
		return err
	}
	key := checkpointKey(checkpoint)
	s.mu.Lock()
	defer s.mu.Unlock()
	current, exists := s.checkpoints[key]
	if exists {
		checkpoint.GapSeen = checkpoint.GapSeen || current.GapSeen
		if checkpoint.Sequence < current.Sequence {
			return errors.New("propertycatalog: producer acknowledgement regresses sequence")
		}
		if checkpoint.Sequence == current.Sequence {
			if checkpoint.PayloadSHA256 != current.PayloadSHA256 || checkpoint.EnvelopeID != current.EnvelopeID {
				return errors.New("propertycatalog: producer acknowledgement conflicts at sequence")
			}
			return nil
		}
		if checkpoint.Sequence != current.Sequence+1 || snapshot.PreviousPayloadSHA256 != current.PayloadSHA256 {
			return errors.New("propertycatalog: producer acknowledgement skips or breaks the payload chain")
		}
		if current.DeliveryCount != current.Sequence || !isLowerSHA256(current.SourceDigest) ||
			!isLowerSHA256(current.EmittedDigest) {
			return errors.New("propertycatalog: producer acknowledgement lacks durable aggregate evidence")
		}
		var addErr error
		checkpoint.SourceRows, addErr = checkedAddUint64(current.SourceRows, checkpoint.SourceRows)
		if addErr == nil {
			checkpoint.DefinitionRows, addErr = checkedAddUint64(current.DefinitionRows, checkpoint.DefinitionRows)
		}
		if addErr == nil {
			checkpoint.ValueRows, addErr = checkedAddUint64(current.ValueRows, checkpoint.ValueRows)
		}
		if addErr == nil {
			checkpoint.TombstoneRows, addErr = checkedAddUint64(current.TombstoneRows, checkpoint.TombstoneRows)
		}
		if addErr == nil {
			checkpoint.DeliveryCount, addErr = checkedAddUint64(current.DeliveryCount, checkpoint.DeliveryCount)
		}
		if addErr != nil {
			return addErr
		}
		checkpoint.SourceDigest = current.SourceDigest
		if !snapshot.Terminal {
			checkpoint.SourceDigest = framedSHA256(
				"futureagi.property-catalog.hot-source-stream.v1", current.SourceDigest,
				snapshot.Payload.SourceBatchDigest,
			)
		}
		checkpoint.EmittedDigest = framedSHA256(
			"futureagi.property-catalog.emitted-stream.v1", current.EmittedDigest,
			snapshot.PayloadSHA256,
		)
	} else if checkpoint.Sequence != 1 || snapshot.PreviousPayloadSHA256 != ZeroSHA256 {
		return errors.New("propertycatalog: producer acknowledgement requires a sequence-one root")
	}
	updated := make(map[streamKey]StreamCheckpoint, len(s.checkpoints)+1)
	for existingKey, value := range s.checkpoints {
		updated[existingKey] = value
	}
	updated[key] = checkpoint
	persist := s.persist
	if s.persistHook != nil {
		persist = s.persistHook
	}
	if err := persist(updated); err != nil {
		return err
	}
	s.checkpoints = updated
	return nil
}

func checkedAddUint64(left, right uint64) (uint64, error) {
	if ^uint64(0)-left < right {
		return 0, errors.New("propertycatalog: producer aggregate overflows UInt64")
	}
	return left + right, nil
}

func (s *producerStateStore) persist(values map[streamKey]StreamCheckpoint) error {
	checkpoints := make([]StreamCheckpoint, 0, len(values))
	for _, checkpoint := range values {
		checkpoints = append(checkpoints, checkpoint)
	}
	sort.Slice(checkpoints, func(i, j int) bool {
		left, right := checkpoints[i], checkpoints[j]
		if left.OrganizationID != right.OrganizationID {
			return left.OrganizationID < right.OrganizationID
		}
		if left.WorkspaceID != right.WorkspaceID {
			return left.WorkspaceID < right.WorkspaceID
		}
		if left.CatalogEpoch != right.CatalogEpoch {
			return left.CatalogEpoch < right.CatalogEpoch
		}
		if left.CatalogRevision != right.CatalogRevision {
			return left.CatalogRevision < right.CatalogRevision
		}
		if left.BuildToken != right.BuildToken {
			return left.BuildToken < right.BuildToken
		}
		if left.SourceAdapter != right.SourceAdapter {
			return left.SourceAdapter < right.SourceAdapter
		}
		return left.ProducerStreamID < right.ProducerStreamID
	})
	raw, err := json.Marshal(producerStateDocument{
		Format: producerStateFormat, Version: producerStateVersion, Checkpoints: checkpoints,
	})
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	if len(raw) > maxProducerStateBytes {
		return errors.New("propertycatalog: producer state exceeds byte limit")
	}
	temporary, err := os.CreateTemp(s.directory, ".producer-state-tmp-*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	keep := true
	defer func() {
		_ = temporary.Close()
		if keep {
			_ = os.Remove(temporaryPath)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return err
	}
	if _, err := temporary.Write(raw); err != nil {
		return err
	}
	if err := temporary.Sync(); err != nil {
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := os.Rename(temporaryPath, s.path); err != nil {
		return err
	}
	keep = false
	return syncDirectory(s.directory)
}

func (s *producerStateStore) remove(keys map[streamKey]struct{}) error {
	if s == nil {
		return errors.New("propertycatalog: nil producer state")
	}
	if len(keys) == 0 {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	updated := make(map[streamKey]StreamCheckpoint, len(s.checkpoints))
	for key, checkpoint := range s.checkpoints {
		if _, remove := keys[key]; !remove {
			updated[key] = checkpoint
		}
	}
	if len(updated) == len(s.checkpoints) {
		return nil
	}
	persist := s.persist
	if s.persistHook != nil {
		persist = s.persistHook
	}
	if err := persist(updated); err != nil {
		return err
	}
	s.checkpoints = updated
	return nil
}

func (s *producerStateStore) snapshot() map[streamKey]StreamCheckpoint {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make(map[streamKey]StreamCheckpoint, len(s.checkpoints))
	for key, checkpoint := range s.checkpoints {
		out[key] = checkpoint
	}
	return out
}

type acknowledgingPublisher struct {
	downstream EnvelopePublisher
	state      *producerStateStore
	revisions  RevisionProvider
	cfg        RuntimeConfig
}

func (p *acknowledgingPublisher) Publish(ctx context.Context, envelope WireEnvelope) error {
	if p == nil || p.downstream == nil || p.state == nil || p.revisions == nil {
		return errors.New("propertycatalog: nil acknowledging publisher")
	}
	snapshot := envelope.Snapshot()
	key := streamKey{
		organizationID: snapshot.OrganizationID, workspaceID: snapshot.WorkspaceID,
		epoch: snapshot.CatalogEpoch, revision: snapshot.CatalogRevision,
		buildToken: snapshot.BuildToken,
		adapter:    snapshot.SourceAdapter, streamID: snapshot.ProducerStreamID,
	}
	if current, exists := p.state.snapshot()[key]; exists && current.Sequence == snapshot.Sequence {
		if current.EnvelopeID != snapshot.EnvelopeID || current.PayloadSHA256 != snapshot.PayloadSHA256 {
			return errors.New("propertycatalog: acknowledged spool envelope conflicts with producer state")
		}
		return nil
	}
	fence, err := p.revisions.CurrentRevision(ctx, snapshot.OrganizationID, snapshot.WorkspaceID)
	if err != nil {
		return fmt.Errorf("propertycatalog: recheck build revision before publish: %w", err)
	}
	if !p.cfg.fenceAllowsTenant(fence, snapshot.OrganizationID, snapshot.WorkspaceID) ||
		fence.CatalogEpoch != snapshot.CatalogEpoch || fence.CatalogRevision != snapshot.CatalogRevision ||
		fence.BuildToken != snapshot.BuildToken ||
		fence.ProjectionVersion != snapshot.ProjectionVersion || fence.CatalogEpoch != p.cfg.CatalogEpoch ||
		fence.ProjectionVersion != p.cfg.ProjectionVersion {
		return errors.New("propertycatalog: envelope no longer matches the active build revision fence")
	}
	switch fence.Status {
	case "building":
	case "draining":
		if fence.FencedSequence == 0 {
			if snapshot.Terminal {
				return errors.New("propertycatalog: drain intent rejects terminal before exact boundary binding")
			}
		} else if snapshot.Sequence > fence.FencedSequence ||
			(snapshot.Sequence == fence.FencedSequence) != snapshot.Terminal {
			return errors.New("propertycatalog: envelope does not match the exact draining fence boundary")
		}
	default:
		return errors.New("propertycatalog: fenced revision rejects publication")
	}
	if err := p.downstream.Publish(ctx, envelope); err != nil {
		return err
	}
	return p.state.acknowledge(snapshot)
}

type producerTail struct {
	sequence    uint64
	payload     string
	envelope    string
	projection  uint16
	terminal    bool
	gapSeen     bool
	sourceBatch string
}

type preparedDrain struct {
	intentFenceSHA256 string
	drainDeadline     string
	terminalSequence  uint64
	prepared          bool
}

type hotTenantScope struct {
	organizationID string
	workspaceID    string
}

// hotSubmission binds an accepted in-memory batch to the exact build leases
// that admitted it. A later revision can therefore never silently adopt work
// accepted for an older build.
type hotSubmission struct {
	rows        []ScopedSpan
	assignments map[hotTenantScope]RevisionFence
	streams     []streamKey
}

// HotRuntime is the isolated, explicitly environment-gated collector-side producer. The
// server transfers a bounded copy of canonical spans to it only after the span
// insert succeeds. A separate fsync spool and synchronous Kafka ACK chain mean
// catalog failure cannot enter the canonical span dead-letter path.
//
// This stream is an acceleration, never the source-completeness authority: a
// process crash can occur after the canonical span commit but before this
// optional enqueue. Activation therefore still requires the revision-pinned
// ClickHouse reconciler/backfill to prove the canonical span high-water and
// value inventory independently. Any omission observed in-process is emitted
// as a durable GAP envelope and poisons the drain proof for this revision.
type HotRuntime struct {
	cfg          RuntimeConfig
	spool        *Spool
	publisher    *acknowledgingPublisher
	revisions    RevisionProvider
	revisionList RevisionLister

	mu    sync.Mutex
	tails map[streamKey]producerTail
	// pendingAdmissions is included in the durable drain proof before an
	// accepted enqueue returns. admissionPoison survives a draining rotation
	// that prevents the accepted batch from reaching the durable spool.
	pendingAdmissions map[streamKey]uint64
	admissionPoison   map[streamKey]bool
	drains            map[streamKey]preparedDrain
	proofMu           sync.Mutex
	retirementMu      sync.Mutex

	queue chan hotSubmission
	gaps  chan error
	stop  chan struct{}
	once  sync.Once
	close sync.Once
	wg    sync.WaitGroup
}

func NewHotRuntime(
	cfg RuntimeConfig, revisions RevisionProvider, downstream EnvelopePublisher,
) (*HotRuntime, error) {
	mode, err := cfg.SelectedMode()
	if err != nil {
		return nil, err
	}
	lister, listsRevisions := revisions.(RevisionLister)
	if (mode != RuntimeSequencer && mode != RuntimeDirectKafkaDevelopment) ||
		revisions == nil || downstream == nil || !listsRevisions {
		return nil, errors.New("propertycatalog: hot runtime requires singleton or explicit development-direct mode, revision provider, and publisher")
	}
	cfg = cfg.WithDefaults()
	spool, err := NewSpool(SpoolConfig{
		Directory: cfg.SpoolDirectory, MaxFiles: cfg.MaxSpoolFiles, MaxBytes: cfg.MaxSpoolBytes,
	})
	if err != nil {
		return nil, err
	}
	state, err := loadProducerState(cfg.SpoolDirectory)
	if err != nil {
		return nil, err
	}
	runtime := &HotRuntime{
		cfg: cfg, spool: spool, revisions: revisions, revisionList: lister,
		publisher:         &acknowledgingPublisher{downstream: downstream, state: state, revisions: revisions, cfg: cfg},
		tails:             make(map[streamKey]producerTail),
		pendingAdmissions: make(map[streamKey]uint64),
		admissionPoison:   make(map[streamKey]bool),
		drains:            make(map[streamKey]preparedDrain),
		queue:             make(chan hotSubmission, cfg.QueueDepth), gaps: make(chan error, cfg.QueueDepth*2),
		stop: make(chan struct{}),
	}
	if err := runtime.reconstructTails(state); err != nil {
		return nil, err
	}
	if err := runtime.loadDrainSafety(); err != nil {
		return nil, err
	}
	retirements, err := loadProducerRetirements(cfg.SpoolDirectory)
	if err != nil {
		return nil, err
	}
	if len(retirements) != 0 {
		fences, err := lister.CurrentRevisions(context.Background())
		if err != nil {
			return nil, err
		}
		// Restore every crash artifact first, then retire the activation-proven
		// checkpoint and its matching in-memory drain/tail state together. Running
		// compaction before loadDrainSafety would let an old ready proof resurrect a
		// retired drain marker after restart. A brand-new empty volume intentionally
		// does not require a fence yet; Python publishes the first one at bootstrap.
		if err := runtime.compactProducerState(context.Background(), fences); err != nil {
			return nil, err
		}
	}
	return runtime, nil
}

func (r *HotRuntime) reconstructTails(state *producerStateStore) error {
	for key, checkpoint := range state.snapshot() {
		if checkpoint.CatalogEpoch != r.cfg.CatalogEpoch || checkpoint.ProjectionVersion != r.cfg.ProjectionVersion ||
			checkpoint.SourceAdapter != AdapterSpanAttribute ||
			checkpoint.ProducerStreamID != r.cfg.ProducerStreamID ||
			!r.cfg.workspaceWithinConfiguredScope(checkpoint.WorkspaceID) {
			return errors.New("propertycatalog: durable producer state does not match the enabled build scope")
		}
		r.tails[key] = producerTail{
			checkpoint.Sequence, checkpoint.PayloadSHA256, checkpoint.EnvelopeID,
			checkpoint.ProjectionVersion, checkpoint.Terminal, checkpoint.GapSeen,
			checkpoint.LastSourceBatchDigest,
		}
	}
	pending, err := r.spool.PendingEnvelopes()
	if err != nil {
		return err
	}
	for _, envelope := range pending {
		snapshot := envelope.Snapshot()
		if snapshot.CatalogEpoch != r.cfg.CatalogEpoch || snapshot.ProjectionVersion != r.cfg.ProjectionVersion ||
			snapshot.SourceAdapter != AdapterSpanAttribute ||
			snapshot.ProducerStreamID != r.cfg.ProducerStreamID ||
			!r.cfg.workspaceWithinConfiguredScope(snapshot.WorkspaceID) {
			return errors.New("propertycatalog: durable spool envelope does not match the enabled build scope")
		}
		key := streamKey{
			organizationID: snapshot.OrganizationID, workspaceID: snapshot.WorkspaceID,
			epoch: snapshot.CatalogEpoch, revision: snapshot.CatalogRevision,
			buildToken: snapshot.BuildToken,
			adapter:    snapshot.SourceAdapter, streamID: snapshot.ProducerStreamID,
		}
		tail, exists := r.tails[key]
		if exists && snapshot.Sequence == tail.sequence && snapshot.EnvelopeID == tail.envelope &&
			snapshot.PayloadSHA256 == tail.payload {
			continue // acknowledged before a crash that left the spool file behind
		}
		wantSequence, wantPrevious := uint64(1), ZeroSHA256
		if exists {
			wantSequence, wantPrevious = tail.sequence+1, tail.payload
		}
		if snapshot.Sequence != wantSequence || snapshot.PreviousPayloadSHA256 != wantPrevious {
			return errors.New("propertycatalog: durable spool contains a sequence gap or broken chain")
		}
		r.tails[key] = producerTail{
			snapshot.Sequence, snapshot.PayloadSHA256, snapshot.EnvelopeID,
			snapshot.ProjectionVersion, snapshot.Terminal,
			tail.gapSeen || snapshot.Payload.Outcome == OutcomeGap,
			snapshot.Payload.SourceBatchDigest,
		}
	}
	return nil
}

func (r *HotRuntime) Start(ctx context.Context) error {
	if r == nil || ctx == nil {
		return errors.New("propertycatalog: hot runtime requires a context")
	}
	if err := r.observeDraining(ctx); err != nil {
		if !r.allowsMissingPrebootstrapFence(err) {
			return err
		}
	} else if err := r.persistDrainProofs(ctx); err != nil {
		return err
	}
	started := false
	r.once.Do(func() {
		started = true
		r.wg.Add(2)
		go r.runSubmission()
		go r.runReplay(ctx)
	})
	if !started {
		return errors.New("propertycatalog: hot runtime already started")
	}
	return nil
}

func (r *HotRuntime) EnqueueCanonicalSpans(rows []ScopedSpan) error {
	if r == nil {
		return errors.New("propertycatalog: nil hot runtime")
	}
	submission, err := r.admitSubmission(cloneHotRows(rows))
	if err != nil {
		return err
	}
	select {
	case <-r.stop:
		r.completeSubmission(submission, submissionStreamSet(submission))
		_ = r.persistDrainProofs(context.Background())
		return errors.New("propertycatalog: hot runtime is stopped")
	default:
	}
	select {
	case <-r.stop:
		r.completeSubmission(submission, submissionStreamSet(submission))
		_ = r.persistDrainProofs(context.Background())
		return errors.New("propertycatalog: hot runtime is stopped")
	case r.queue <- submission:
		// This fsync is the acceptance boundary. If the process crashes after a
		// nil return but before submission, restart converts the durable pending
		// admission into an activation poison rather than losing it silently.
		if err := r.persistDrainProofs(context.Background()); err != nil {
			return fmt.Errorf("propertycatalog: persist accepted admission proof: %w", err)
		}
		return nil
	default:
		gapErr := r.durablyStageGapAssigned(submission.rows, "queue_full", submission.assignments)
		failed := map[streamKey]struct{}{}
		if gapErr != nil {
			failed = submissionStreamSet(submission)
		}
		if completionErr := r.completeSubmission(submission, failed); completionErr != nil && gapErr == nil {
			gapErr = completionErr
		}
		proofErr := r.persistDrainProofs(context.Background())
		if gapErr != nil {
			return fmt.Errorf("propertycatalog: bounded hot runtime queue is full and durable gap staging failed: %w", gapErr)
		}
		if proofErr != nil {
			return fmt.Errorf("propertycatalog: queue-full gap was staged but proof persistence failed: %w", proofErr)
		}
		return errors.New("propertycatalog: bounded hot runtime queue is full; durable revision gap staged")
	}
}

// AcceptCandidate is the singleton sequencing boundary. It returns only after
// the candidate has become an immutable fsynced ordered-envelope spool entry,
// or after proving that the exact candidate is already the durable stream
// tail. Autoscaled collectors never call this method.
func (r *HotRuntime) AcceptCandidate(candidate WireCandidate) (bool, error) {
	if r == nil {
		return false, errors.New("propertycatalog: nil hot runtime")
	}
	if r.cfg.normalizedMode() != RuntimeSequencer {
		return false, errors.New("propertycatalog: candidate admission requires singleton sequencer mode")
	}
	select {
	case <-r.stop:
		return false, errors.New("propertycatalog: hot runtime is stopped")
	default:
	}
	snapshot := candidate.Snapshot()
	if snapshot.CatalogEpoch != r.cfg.CatalogEpoch ||
		snapshot.ProjectionVersion != r.cfg.ProjectionVersion {
		return false, errors.New("propertycatalog: candidate epoch/projection does not match sequencer")
	}
	group, err := candidate.hotGroup()
	if err != nil {
		return false, err
	}
	fence, err := r.revisions.CurrentRevision(
		context.Background(), snapshot.OrganizationID, snapshot.WorkspaceID,
	)
	if err != nil {
		if errors.Is(err, ErrRevisionNotAssigned) {
			return false, candidateNotAdmitted(snapshot, CandidateNoCurrentBuildFence)
		}
		return false, err
	}
	if !r.cfg.fenceAllowsTenant(fence, snapshot.OrganizationID, snapshot.WorkspaceID) {
		return false, candidateNotAdmitted(snapshot, CandidateWorkspaceNotInRollout)
	}
	if fence.Status != "building" {
		return false, candidateNotAdmitted(snapshot, CandidateNoCurrentBuildFence)
	}
	if fence.CatalogEpoch != r.cfg.CatalogEpoch ||
		fence.ProjectionVersion != r.cfg.ProjectionVersion {
		return false, errors.New("propertycatalog: candidate conflicts with build fence epoch/projection")
	}
	if err := validateHotFenceObservation(fence, group.key, group.firstSeen, group.lastSeen); err != nil {
		return false, candidateNotAdmitted(snapshot, CandidateOutsideBuildSourceScope)
	}
	key := streamKey{
		organizationID: snapshot.OrganizationID, workspaceID: snapshot.WorkspaceID,
		epoch: fence.CatalogEpoch, revision: fence.CatalogRevision, buildToken: fence.BuildToken,
		adapter: AdapterSpanAttribute, streamID: r.cfg.ProducerStreamID,
	}

	r.mu.Lock()
	tail, exists := r.tails[key]
	if exists && tail.sourceBatch == snapshot.CandidateID {
		r.mu.Unlock()
		if err := r.persistDrainProofs(context.Background()); err != nil {
			return false, fmt.Errorf("propertycatalog: persist duplicate candidate proof: %w", err)
		}
		return true, nil
	}
	if tail.terminal {
		r.mu.Unlock()
		return false, errors.New("propertycatalog: terminal stream rejects candidate admission")
	}
	sequence, previous := uint64(1), ZeroSHA256
	if exists {
		if tail.sequence == ^uint64(0) {
			r.mu.Unlock()
			return false, errors.New("propertycatalog: candidate stream sequence is exhausted")
		}
		sequence, previous = tail.sequence+1, tail.payload
	}
	envelope, issueErr := buildHotEnvelopeWithSourceDigest(
		r.cfg, fence, group, snapshot.CandidateID, sequence, previous,
	)
	if issueErr == nil {
		var current RevisionFence
		current, issueErr = r.revisions.CurrentRevision(
			context.Background(), snapshot.OrganizationID, snapshot.WorkspaceID,
		)
		if errors.Is(issueErr, ErrRevisionNotAssigned) {
			issueErr = candidateNotAdmitted(snapshot, CandidateNoCurrentBuildFence)
		} else if issueErr == nil && current.Status != "building" {
			issueErr = candidateNotAdmitted(snapshot, CandidateNoCurrentBuildFence)
		} else if issueErr == nil && (current.FenceSHA256 != fence.FenceSHA256 ||
			current.BuildToken != fence.BuildToken || current.CatalogRevision != fence.CatalogRevision) {
			// A different building fence is a race, not a skip: retrying can bind
			// this still-durable candidate to the new current build. Keeping its
			// receipt prevents a silent gap if fence rotation was malformed.
			issueErr = errors.New("propertycatalog: build revision rotated during candidate staging")
		}
	}
	if issueErr == nil {
		issueErr = r.spool.Enqueue(envelope)
	}
	if issueErr == nil {
		issued := envelope.Snapshot()
		r.tails[key] = producerTail{
			sequence: issued.Sequence, payload: issued.PayloadSHA256,
			envelope: issued.EnvelopeID, projection: issued.ProjectionVersion,
			terminal:    issued.Terminal,
			gapSeen:     tail.gapSeen || issued.Payload.Outcome == OutcomeGap,
			sourceBatch: snapshot.CandidateID,
		}
	}
	r.mu.Unlock()
	if issueErr != nil {
		return false, issueErr
	}
	if err := r.persistDrainProofs(context.Background()); err != nil {
		return false, fmt.Errorf("propertycatalog: persist candidate admission proof: %w", err)
	}
	return false, nil
}

func (r *HotRuntime) admitSubmission(rows []ScopedSpan) (hotSubmission, error) {
	if len(rows) == 0 {
		return hotSubmission{}, errors.New("propertycatalog: hot admission requires source rows")
	}
	scopeSet := make(map[hotTenantScope]struct{})
	for _, row := range rows {
		if validateCanonicalUUID("admission organization", row.OrganizationID) != nil ||
			validateCanonicalUUID("admission workspace", row.WorkspaceID) != nil ||
			!r.cfg.workspaceWithinConfiguredScope(row.WorkspaceID) {
			continue
		}
		scopeSet[hotTenantScope{row.OrganizationID, row.WorkspaceID}] = struct{}{}
	}
	if len(scopeSet) == 0 {
		return hotSubmission{}, errors.New("propertycatalog: hot admission has no valid allowlisted tenant scope")
	}
	scopes := make([]hotTenantScope, 0, len(scopeSet))
	for scope := range scopeSet {
		scopes = append(scopes, scope)
	}
	sort.Slice(scopes, func(i, j int) bool {
		if scopes[i].organizationID != scopes[j].organizationID {
			return scopes[i].organizationID < scopes[j].organizationID
		}
		return scopes[i].workspaceID < scopes[j].workspaceID
	})

	submission := hotSubmission{
		rows: rows, assignments: make(map[hotTenantScope]RevisionFence, len(scopes)),
		streams: make([]streamKey, 0, len(scopes)),
	}
	// The same lock guards terminal issuance. If admission read a building
	// lease first, draining must observe its pending count; if draining read
	// first, this admission sees draining and fails before entering the queue.
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, scope := range scopes {
		fence, err := r.revisions.CurrentRevision(
			context.Background(), scope.organizationID, scope.workspaceID,
		)
		if err != nil {
			return hotSubmission{}, err
		}
		if !r.cfg.fenceAllowsTenant(fence, scope.organizationID, scope.workspaceID) ||
			fence.Status != "building" || fence.CatalogEpoch != r.cfg.CatalogEpoch ||
			fence.ProjectionVersion != r.cfg.ProjectionVersion {
			return hotSubmission{}, errors.New("propertycatalog: build revision is not accepting hot admission")
		}
		key := streamKey{
			organizationID: scope.organizationID, workspaceID: scope.workspaceID,
			epoch: fence.CatalogEpoch, revision: fence.CatalogRevision, buildToken: fence.BuildToken,
			adapter: AdapterSpanAttribute, streamID: r.cfg.ProducerStreamID,
		}
		if r.tails[key].terminal {
			return hotSubmission{}, errors.New("propertycatalog: terminal stream rejects hot admission")
		}
		submission.assignments[scope] = fence
		submission.streams = append(submission.streams, key)
	}
	// Source scope is part of the build lease, not a mutable collector setting.
	// Reject the whole submission before its pending-admission acceptance point
	// if any otherwise-valid row would cross the signed project/time boundary.
	for index, scoped := range rows {
		key, seenAt, allowed, rowErr := hotRowScopeAssigned(r.cfg, scoped, submission.assignments)
		if rowErr != nil || !allowed {
			continue // existing invalid-row handling emits a durable activation poison
		}
		fence, assigned := submission.assignments[hotTenantScope{
			organizationID: key.organizationID,
			workspaceID:    key.workspaceID,
		}]
		if !assigned {
			return hotSubmission{}, errors.New("propertycatalog: scoped hot row has no revision assignment")
		}
		if err := validateHotFenceObservation(fence, key, seenAt, seenAt); err != nil {
			return hotSubmission{}, fmt.Errorf(
				"propertycatalog: hot admission rejects row %d: %w", index, err,
			)
		}
	}
	for _, key := range submission.streams {
		r.pendingAdmissions[key]++
	}
	return submission, nil
}

func submissionStreamSet(submission hotSubmission) map[streamKey]struct{} {
	set := make(map[streamKey]struct{}, len(submission.streams))
	for _, key := range submission.streams {
		set[key] = struct{}{}
	}
	return set
}

func (r *HotRuntime) completeSubmission(
	submission hotSubmission, failed map[streamKey]struct{},
) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, key := range submission.streams {
		if r.pendingAdmissions[key] == 0 {
			return errors.New("propertycatalog: hot admission completion underflow")
		}
	}
	for _, key := range submission.streams {
		pending := r.pendingAdmissions[key]
		if _, poisoned := failed[key]; poisoned {
			r.admissionPoison[key] = true
		}
		if pending == 1 {
			delete(r.pendingAdmissions, key)
		} else {
			r.pendingAdmissions[key] = pending - 1
		}
	}
	return nil
}

func (r *HotRuntime) processSubmission(submission hotSubmission) {
	failed := r.stageAssigned(submission.rows, submission.assignments)
	if err := r.completeSubmission(submission, failed); err != nil {
		r.reportGap(err)
	}
}

func (r *HotRuntime) Gaps() <-chan error { return r.gaps }

func (r *HotRuntime) Shutdown(ctx context.Context) error {
	if r == nil || ctx == nil {
		return errors.New("propertycatalog: shutdown requires runtime and context")
	}
	r.close.Do(func() { close(r.stop) })
	done := make(chan struct{})
	go func() { r.wg.Wait(); close(done) }()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-done:
	}
	_, err := r.spool.Replay(ctx, r.publisher)
	if err != nil {
		return err
	}
	err = r.persistDrainProofs(ctx)
	if err != nil && r.allowsMissingPrebootstrapFence(err) {
		return nil
	}
	return err
}

func (r *HotRuntime) runSubmission() {
	defer r.wg.Done()
	for {
		select {
		case submission := <-r.queue:
			r.processSubmission(submission)
			if err := r.persistDrainProofs(context.Background()); err != nil {
				r.reportGap(err)
			}
		case <-r.stop:
			for {
				select {
				case submission := <-r.queue:
					r.processSubmission(submission)
					if err := r.persistDrainProofs(context.Background()); err != nil {
						r.reportGap(err)
					}
				default:
					return
				}
			}
		}
	}
}

func (r *HotRuntime) runReplay(ctx context.Context) {
	defer r.wg.Done()
	ticker := time.NewTicker(r.cfg.ReplayInterval)
	defer ticker.Stop()
	for {
		observed := true
		if err := r.observeDraining(ctx); err != nil && ctx.Err() == nil {
			observed = false
			if !r.allowsMissingPrebootstrapFence(err) {
				r.reportGap(err)
			}
		}
		if observed {
			if _, err := r.spool.Replay(ctx, r.publisher); err != nil && ctx.Err() == nil {
				r.reportGap(err)
			}
			if err := r.persistDrainProofs(ctx); err != nil && ctx.Err() == nil {
				r.reportGap(err)
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		case <-r.stop:
			return
		}
	}
}

// allowsMissingPrebootstrapFence recognizes only the brand-new, evidence-free
// shared volume used before Python publishes the first assignment. Once any
// producer state, spool, drain proof, retirement proof, admission, or poison
// exists, loss of the fence is a hard error rather than a disabled state.
func (r *HotRuntime) allowsMissingPrebootstrapFence(observed error) bool {
	if r == nil || !errors.Is(observed, os.ErrNotExist) || r.publisher == nil ||
		r.publisher.state == nil || r.spool == nil || len(r.publisher.state.snapshot()) != 0 {
		return false
	}
	pending, err := r.spool.PendingEnvelopes()
	if err != nil || len(pending) != 0 {
		return false
	}
	for _, path := range []string{
		r.DrainProofPath(), filepath.Join(r.cfg.SpoolDirectory, producerRetirementFileName),
	} {
		if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
			return false
		}
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.tails) == 0 && len(r.pendingAdmissions) == 0 &&
		len(r.admissionPoison) == 0 && len(r.drains) == 0
}

func (r *HotRuntime) observeDraining(ctx context.Context) error {
	if r == nil || r.revisionList == nil || ctx == nil {
		return errors.New("propertycatalog: draining observation requires a runtime context")
	}
	fences, err := r.revisionList.CurrentRevisions(ctx)
	if err != nil {
		return err
	}
	if err := r.compactProducerState(ctx, fences); err != nil {
		return err
	}
	for _, fence := range fences {
		if fence.Status != "draining" ||
			!r.cfg.fenceAllowsTenant(fence, fence.OrganizationID, fence.WorkspaceID) ||
			fence.CatalogEpoch != r.cfg.CatalogEpoch || fence.ProjectionVersion != r.cfg.ProjectionVersion {
			continue
		}
		key := streamKey{
			organizationID: fence.OrganizationID, workspaceID: fence.WorkspaceID,
			epoch: fence.CatalogEpoch, revision: fence.CatalogRevision, buildToken: fence.BuildToken,
			adapter: AdapterSpanAttribute, streamID: r.cfg.ProducerStreamID,
		}
		r.mu.Lock()
		tail, exists := r.tails[key]
		drain, drainExists := r.drains[key]
		if exists && tail.terminal {
			if fence.FencedSequence == 0 || tail.sequence != fence.FencedSequence ||
				!drainExists || drain.terminalSequence != tail.sequence {
				r.mu.Unlock()
				return errors.New("propertycatalog: issued terminal does not match the draining sequence boundary")
			}
			r.mu.Unlock()
			continue
		}
		if r.pendingAdmissions[key] > 0 {
			// The coordinator may have rotated immediately after observing a
			// building proof. Wait until every already-accepted submission has
			// either reached the fsync spool or become a durable local poison.
			r.mu.Unlock()
			continue
		}
		sequence, previous := uint64(1), ZeroSHA256
		if exists {
			if tail.sequence == ^uint64(0) {
				r.mu.Unlock()
				return errors.New("propertycatalog: hot drain terminal sequence is exhausted")
			}
			sequence, previous = tail.sequence+1, tail.payload
		}
		// fenced_sequence=0 is a durable drain intent, not a guessed high-water.
		// Admissions are already closed because admitSubmission accepts only a
		// building assignment.  Persist the producer-owned terminal boundary and
		// wait for Python to prove the physical non-terminal ledger and bind that
		// exact boundary before issuing the terminal.
		if fence.FencedSequence == 0 {
			if drainExists && (drain.intentFenceSHA256 != fence.FenceSHA256 ||
				drain.drainDeadline != fence.DrainDeadline || drain.terminalSequence != sequence) {
				r.mu.Unlock()
				return errors.New("propertycatalog: drain intent changed after its producer boundary was prepared")
			}
			if !drainExists {
				r.drains[key] = preparedDrain{
					intentFenceSHA256: fence.FenceSHA256,
					drainDeadline:     fence.DrainDeadline,
					terminalSequence:  sequence,
				}
			}
			r.mu.Unlock()
			continue
		}
		if !drainExists || !drain.prepared || drain.drainDeadline != fence.DrainDeadline ||
			drain.terminalSequence != fence.FencedSequence || fence.FencedSequence != sequence {
			r.mu.Unlock()
			return errors.New("propertycatalog: exact drain boundary was not prepared and physically bound")
		}
		terminal, buildErr := buildHotTerminalEnvelope(r.cfg, fence, sequence, previous)
		if buildErr == nil {
			var current RevisionFence
			current, buildErr = r.revisions.CurrentRevision(ctx, fence.OrganizationID, fence.WorkspaceID)
			if buildErr == nil && (current.Status != "draining" || current.FenceSHA256 != fence.FenceSHA256 ||
				current.BuildToken != fence.BuildToken || current.FencedSequence != sequence) {
				buildErr = errors.New("propertycatalog: draining fence rotated during terminal issuance")
			}
		}
		if buildErr == nil {
			buildErr = r.spool.Enqueue(terminal)
		}
		if buildErr == nil {
			snapshot := terminal.Snapshot()
			r.tails[key] = producerTail{
				snapshot.Sequence, snapshot.PayloadSHA256, snapshot.EnvelopeID,
				snapshot.ProjectionVersion, true, tail.gapSeen,
				snapshot.Payload.SourceBatchDigest,
			}
		}
		r.mu.Unlock()
		if buildErr != nil {
			return buildErr
		}
	}
	return nil
}

func (r *HotRuntime) stage(rows []ScopedSpan) {
	_ = r.stageAssigned(rows, nil)
}

func (r *HotRuntime) stageAssigned(
	rows []ScopedSpan, assignments map[hotTenantScope]RevisionFence,
) map[streamKey]struct{} {
	failed := make(map[streamKey]struct{})
	markAllAssignedFailed := func() {
		for scope, fence := range assignments {
			failed[streamKey{
				organizationID: scope.organizationID, workspaceID: scope.workspaceID,
				epoch: fence.CatalogEpoch, revision: fence.CatalogRevision, buildToken: fence.BuildToken,
				adapter: AdapterSpanAttribute, streamID: r.cfg.ProducerStreamID,
			}] = struct{}{}
		}
	}
	groups, collectionErrors := collectHotGroupsAssigned(r.cfg, rows, assignments)
	for _, err := range collectionErrors {
		r.reportGap(err)
	}
	if len(collectionErrors) > 0 {
		if err := r.durablyStageGapAssigned(rows, "invalid_scoped_row", assignments); err != nil {
			r.reportGap(fmt.Errorf("propertycatalog: durable invalid-row gap failed: %w", err))
			markAllAssignedFailed()
		}
	}
	for _, group := range groups {
		scope := hotTenantScope{group.key.organizationID, group.key.workspaceID}
		fence, assigned := assignments[scope]
		var err error
		if assignments == nil {
			fence, err = r.revisions.CurrentRevision(
				context.Background(), group.key.organizationID, group.key.workspaceID,
			)
		} else if !assigned {
			err = errors.New("propertycatalog: accepted submission lacks its tenant lease")
		}
		if err != nil {
			r.reportGap(err)
			markAllAssignedFailed()
			continue
		}
		key := streamKey{
			organizationID: group.key.organizationID, workspaceID: group.key.workspaceID,
			epoch: fence.CatalogEpoch, revision: fence.CatalogRevision,
			buildToken: fence.BuildToken,
			adapter:    AdapterSpanAttribute, streamID: r.cfg.ProducerStreamID,
		}
		if fence.Status != "building" {
			r.reportGap(errors.New("propertycatalog: build revision is not accepting new staging"))
			failed[key] = struct{}{}
			continue
		}
		if fence.CatalogEpoch != r.cfg.CatalogEpoch || fence.ProjectionVersion != r.cfg.ProjectionVersion {
			r.reportGap(errors.New("propertycatalog: build revision fence does not match configured epoch/projection"))
			failed[key] = struct{}{}
			continue
		}
		r.mu.Lock()
		tail, exists := r.tails[key]
		sequence, previous := uint64(1), ZeroSHA256
		if exists {
			sequence, previous = tail.sequence+1, tail.payload
		}
		envelope, err := buildHotEnvelope(r.cfg, fence, group, sequence, previous)
		if err == nil {
			// Recheck immediately before the durable issuance point. This closes
			// the normal control-file rotation window; a draining proof exposes
			// the resulting last-issued boundary for the coordinator handshake.
			var current RevisionFence
			current, err = r.revisions.CurrentRevision(
				context.Background(), group.key.organizationID, group.key.workspaceID,
			)
			if err == nil && (current.Status != "building" || current.FenceSHA256 != fence.FenceSHA256 ||
				current.BuildToken != fence.BuildToken || current.CatalogRevision != fence.CatalogRevision) {
				err = errors.New("propertycatalog: build revision rotated during staging")
			}
		}
		if err == nil {
			err = r.spool.Enqueue(envelope)
		}
		if err == nil {
			snapshot := envelope.Snapshot()
			r.tails[key] = producerTail{
				snapshot.Sequence, snapshot.PayloadSHA256, snapshot.EnvelopeID,
				snapshot.ProjectionVersion, snapshot.Terminal,
				tail.gapSeen || snapshot.Payload.Outcome == OutcomeGap,
				snapshot.Payload.SourceBatchDigest,
			}
		}
		r.mu.Unlock()
		if err != nil {
			r.reportGap(err)
			failed[key] = struct{}{}
		}
	}
	return failed
}

func (r *HotRuntime) durablyStageGap(rows []ScopedSpan, reason string) error {
	return r.durablyStageGapAssigned(rows, reason, nil)
}

func (r *HotRuntime) durablyStageGapAssigned(
	rows []ScopedSpan, reason string, assignments map[hotTenantScope]RevisionFence,
) error {
	if r == nil || len(rows) == 0 {
		return errors.New("propertycatalog: durable gap requires scoped source rows")
	}
	type tenantKey struct{ organizationID, workspaceID string }
	counts := make(map[tenantKey]uint64)
	for _, scoped := range rows {
		if validateCanonicalUUID("gap organization", scoped.OrganizationID) != nil ||
			validateCanonicalUUID("gap workspace", scoped.WorkspaceID) != nil ||
			!r.cfg.tenantAllowedByConfigurationOrAssignment(
				scoped.OrganizationID, scoped.WorkspaceID, assignments,
			) {
			continue
		}
		counts[tenantKey{scoped.OrganizationID, scoped.WorkspaceID}]++
	}
	if len(counts) == 0 {
		return errors.New("propertycatalog: no valid tenant scope exists for durable gap")
	}
	tenants := make([]tenantKey, 0, len(counts))
	for tenant := range counts {
		tenants = append(tenants, tenant)
	}
	sort.Slice(tenants, func(i, j int) bool {
		if tenants[i].organizationID != tenants[j].organizationID {
			return tenants[i].organizationID < tenants[j].organizationID
		}
		return tenants[i].workspaceID < tenants[j].workspaceID
	})
	for _, tenant := range tenants {
		scope := hotTenantScope{tenant.organizationID, tenant.workspaceID}
		fence, assigned := assignments[scope]
		var err error
		if assignments == nil {
			fence, err = r.revisions.CurrentRevision(
				context.Background(), tenant.organizationID, tenant.workspaceID,
			)
		} else if !assigned {
			err = errors.New("propertycatalog: accepted gap lacks its tenant lease")
		}
		if err != nil {
			return err
		}
		if fence.Status != "building" || fence.CatalogEpoch != r.cfg.CatalogEpoch ||
			fence.ProjectionVersion != r.cfg.ProjectionVersion {
			return errors.New("propertycatalog: revision is not accepting a durable gap")
		}
		key := streamKey{
			organizationID: tenant.organizationID, workspaceID: tenant.workspaceID,
			epoch: fence.CatalogEpoch, revision: fence.CatalogRevision, buildToken: fence.BuildToken,
			adapter: AdapterSpanAttribute, streamID: r.cfg.ProducerStreamID,
		}
		r.mu.Lock()
		tail, exists := r.tails[key]
		if tail.terminal {
			r.mu.Unlock()
			return errors.New("propertycatalog: terminal stream cannot accept a durable gap")
		}
		sequence, previous := uint64(1), ZeroSHA256
		if exists {
			sequence, previous = tail.sequence+1, tail.payload
		}
		envelope, issueErr := buildHotGapEnvelope(
			r.cfg, fence, sequence, previous, counts[tenant], reason,
		)
		if issueErr == nil {
			var current RevisionFence
			current, issueErr = r.revisions.CurrentRevision(
				context.Background(), tenant.organizationID, tenant.workspaceID,
			)
			if issueErr == nil && (current.Status != "building" || current.FenceSHA256 != fence.FenceSHA256 ||
				current.BuildToken != fence.BuildToken) {
				issueErr = errors.New("propertycatalog: build revision rotated during durable gap staging")
			}
		}
		if issueErr == nil {
			issueErr = r.spool.Enqueue(envelope)
		}
		if issueErr == nil {
			snapshot := envelope.Snapshot()
			r.tails[key] = producerTail{
				snapshot.Sequence, snapshot.PayloadSHA256, snapshot.EnvelopeID,
				snapshot.ProjectionVersion, false, true,
				snapshot.Payload.SourceBatchDigest,
			}
		}
		r.mu.Unlock()
		if issueErr != nil {
			return issueErr
		}
	}
	return nil
}

func (r *HotRuntime) reportGap(err error) {
	if err == nil {
		return
	}
	select {
	case r.gaps <- err:
	default:
	}
}

func cloneHotRows(rows []ScopedSpan) []ScopedSpan {
	cloned := make([]ScopedSpan, len(rows))
	for index, scoped := range rows {
		row := scoped.Row
		copyRow := make(map[string]any, 8)
		for _, field := range []string{"org_id", "project_id", "start_time", "model"} {
			copyRow[field] = row[field]
		}
		copyRow["attrs_string"] = cloneMap(row["attrs_string"])
		copyRow["attrs_number"] = cloneMap(row["attrs_number"])
		copyRow["attrs_bool"] = cloneMap(row["attrs_bool"])
		copyRow["attributes_extra"] = cloneMap(row["attributes_extra"])
		cloned[index] = ScopedSpan{
			OrganizationID: scoped.OrganizationID, WorkspaceID: scoped.WorkspaceID,
			ScopeError: scoped.ScopeError, Row: copyRow,
		}
	}
	return cloned
}

func cloneMap(value any) any {
	switch source := value.(type) {
	case map[string]string:
		out := make(map[string]string, len(source))
		for key, value := range source {
			out[key] = value
		}
		return out
	case map[string]float64:
		out := make(map[string]float64, len(source))
		for key, value := range source {
			out[key] = value
		}
		return out
	case map[string]uint8:
		out := make(map[string]uint8, len(source))
		for key, value := range source {
			out[key] = value
		}
		return out
	case map[string]any:
		out := make(map[string]any, len(source))
		for key, value := range source {
			out[key] = value
		}
		return out
	default:
		return value
	}
}
