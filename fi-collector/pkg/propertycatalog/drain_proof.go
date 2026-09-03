package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

const (
	drainProofFormat   = "futureagi.property-catalog-drain-proof"
	drainProofVersion  = uint16(2)
	drainProofFileName = "producer-drain-proof-v2.json"
	maxDrainProofBytes = 64 << 20
)

// DrainProof is the producer-owned durable side of the hot-stream handoff.
// A draining assignment with fenced_sequence=0 is only an intent. Prepared
// binds the last data sequence and proposed terminal sequence after admissions
// are closed and every issued data envelope is Kafka-acknowledged. Python
// independently proves those data deliveries in ClickHouse before publishing
// the exact non-zero boundary. Ready is emitted only after that exact terminal
// is issued and Kafka-acknowledged; Python then re-proves the physical ledger.
type DrainProof struct {
	OrganizationID           string        `json:"organization_id"`
	WorkspaceID              string        `json:"workspace_id"`
	CatalogEpoch             uint16        `json:"catalog_epoch"`
	CatalogRevision          uint64        `json:"catalog_revision"`
	BuildToken               string        `json:"build_token"`
	ProjectionVersion        uint16        `json:"projection_version"`
	SourceAdapter            SourceAdapter `json:"source_adapter"`
	ProducerStreamID         string        `json:"producer_stream_id"`
	BuildLeaseSHA256         string        `json:"build_lease_sha256"`
	DrainIntentFenceSHA256   string        `json:"drain_intent_fence_sha256"`
	ObservedFenceSHA256      string        `json:"observed_fence_sha256"`
	DrainDeadline            string        `json:"drain_deadline"`
	Phase                    string        `json:"phase"`
	LastDataSequence         uint64        `json:"last_data_sequence"`
	TerminalSequence         uint64        `json:"terminal_sequence"`
	LastIssuedSequence       uint64        `json:"last_issued_sequence"`
	LastAcknowledgedSequence uint64        `json:"last_acknowledged_sequence"`
	TerminalIssued           bool          `json:"terminal_issued"`
	TerminalAcknowledged     bool          `json:"terminal_acknowledged"`
	SourceCount              uint64        `json:"source_count"`
	DefinitionCount          uint64        `json:"definition_count"`
	ValueCount               uint64        `json:"value_count"`
	TombstoneCount           uint64        `json:"tombstone_count"`
	DeliveryCount            uint64        `json:"delivery_count"`
	SourceDigest             string        `json:"source_digest"`
	EmittedDigest            string        `json:"emitted_digest"`
	TerminalPayloadSHA256    string        `json:"terminal_payload_sha256"`
	GapIssued                bool          `json:"gap_issued"`
	GapAcknowledged          bool          `json:"gap_acknowledged"`
	PendingEnvelopes         uint64        `json:"pending_envelopes"`
	PendingAdmissions        uint64        `json:"pending_admissions"`
	Poisoned                 bool          `json:"poisoned"`
	Ready                    bool          `json:"ready"`
}

type drainProofDocument struct {
	Format  string       `json:"format"`
	Version uint16       `json:"version"`
	Proofs  []DrainProof `json:"proofs"`
}

func (r *HotRuntime) DrainProofPath() string {
	if r == nil {
		return ""
	}
	return filepath.Join(r.cfg.SpoolDirectory, drainProofFileName)
}

func (r *HotRuntime) DrainProofs(ctx context.Context) ([]DrainProof, error) {
	if r == nil || r.revisionList == nil || r.publisher == nil || r.publisher.state == nil || ctx == nil {
		return nil, errors.New("propertycatalog: drain proof requires a live runtime context")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	fences, err := r.revisionList.CurrentRevisions(ctx)
	if err != nil {
		return nil, err
	}
	r.mu.Lock()
	tails := make(map[streamKey]producerTail, len(r.tails))
	for key, tail := range r.tails {
		tails[key] = tail
	}
	admissions := make(map[streamKey]uint64, len(r.pendingAdmissions))
	for key, count := range r.pendingAdmissions {
		admissions[key] = count
	}
	poisons := make(map[streamKey]bool, len(r.admissionPoison))
	for key, poisoned := range r.admissionPoison {
		poisons[key] = poisoned
	}
	drains := make(map[streamKey]preparedDrain, len(r.drains))
	for key, drain := range r.drains {
		drains[key] = drain
	}
	r.mu.Unlock()

	acknowledged := r.publisher.state.snapshot()
	pending, err := r.spool.PendingEnvelopes()
	if err != nil {
		return nil, err
	}
	pendingCounts := make(map[streamKey]uint64)
	for _, envelope := range pending {
		snapshot := envelope.Snapshot()
		key := streamKey{
			organizationID: snapshot.OrganizationID, workspaceID: snapshot.WorkspaceID,
			epoch: snapshot.CatalogEpoch, revision: snapshot.CatalogRevision,
			buildToken: snapshot.BuildToken, adapter: snapshot.SourceAdapter,
			streamID: snapshot.ProducerStreamID,
		}
		pendingCounts[key]++
	}

	proofs := make([]DrainProof, 0, len(fences))
	for _, fence := range fences {
		if !r.cfg.fenceAllowsTenant(fence, fence.OrganizationID, fence.WorkspaceID) ||
			fence.CatalogEpoch != r.cfg.CatalogEpoch ||
			fence.ProjectionVersion != r.cfg.ProjectionVersion {
			continue
		}
		key := streamKey{
			organizationID: fence.OrganizationID, workspaceID: fence.WorkspaceID,
			epoch: fence.CatalogEpoch, revision: fence.CatalogRevision, buildToken: fence.BuildToken,
			adapter: AdapterSpanAttribute, streamID: r.cfg.ProducerStreamID,
		}
		tail, checkpoint, drain := tails[key], acknowledged[key], drains[key]
		proof := DrainProof{
			OrganizationID: fence.OrganizationID, WorkspaceID: fence.WorkspaceID,
			CatalogEpoch: fence.CatalogEpoch, CatalogRevision: fence.CatalogRevision,
			BuildToken: fence.BuildToken, ProjectionVersion: fence.ProjectionVersion,
			SourceAdapter: AdapterSpanAttribute, ProducerStreamID: r.cfg.ProducerStreamID,
			BuildLeaseSHA256: fence.BuildLeaseSHA256, ObservedFenceSHA256: fence.FenceSHA256,
			DrainIntentFenceSHA256: drain.intentFenceSHA256, DrainDeadline: fence.DrainDeadline,
			LastDataSequence: drainDataSequence(tail, drain), TerminalSequence: drain.terminalSequence,
			LastIssuedSequence: tail.sequence, LastAcknowledgedSequence: checkpoint.Sequence,
			TerminalIssued: tail.terminal, TerminalAcknowledged: checkpoint.Terminal,
			SourceCount: checkpoint.SourceRows, DefinitionCount: checkpoint.DefinitionRows,
			ValueCount: checkpoint.ValueRows, TombstoneCount: checkpoint.TombstoneRows,
			DeliveryCount: checkpoint.DeliveryCount, SourceDigest: checkpoint.SourceDigest,
			EmittedDigest: checkpoint.EmittedDigest, TerminalPayloadSHA256: ZeroSHA256,
			GapIssued: tail.gapSeen, GapAcknowledged: checkpoint.GapSeen,
			PendingEnvelopes: pendingCounts[key], PendingAdmissions: admissions[key],
		}
		if checkpoint.Sequence == 0 {
			proof.SourceDigest, proof.EmittedDigest = emptySHA256, emptySHA256
		}
		if tail.terminal {
			proof.TerminalPayloadSHA256 = tail.payload
		}
		if proof.LastAcknowledgedSequence > proof.LastIssuedSequence {
			return nil, errors.New("propertycatalog: drain proof acknowledgement exceeds issued sequence")
		}
		aggregateValid := proof.LastAcknowledgedSequence == proof.LastIssuedSequence &&
			proof.DeliveryCount == proof.LastAcknowledgedSequence &&
			isLowerSHA256(proof.SourceDigest) && isLowerSHA256(proof.EmittedDigest)
		proof.Poisoned = proof.GapIssued || proof.GapAcknowledged || poisons[key]
		switch {
		case proof.Poisoned:
			proof.Phase = "poisoned"
		case fence.Status == "building":
			proof.Phase = "building"
		case fence.Status != "draining" || drain.terminalSequence == 0:
			proof.Phase = "preparing"
		case fence.FencedSequence == 0:
			proof.Phase = "preparing"
			if aggregateValid && !proof.TerminalIssued && proof.PendingEnvelopes == 0 &&
				proof.PendingAdmissions == 0 && proof.LastIssuedSequence == proof.LastDataSequence {
				proof.Phase = "prepared"
			}
		case !drain.prepared || fence.FencedSequence != drain.terminalSequence:
			proof.Phase = "preparing"
		default:
			proof.Phase = "bound"
			proof.Ready = aggregateValid && proof.TerminalIssued && proof.TerminalAcknowledged &&
				proof.LastIssuedSequence == proof.TerminalSequence &&
				proof.LastAcknowledgedSequence == proof.TerminalSequence &&
				proof.PendingEnvelopes == 0 && proof.PendingAdmissions == 0 &&
				proof.TerminalPayloadSHA256 == checkpoint.PayloadSHA256
			if proof.Ready {
				proof.Phase = "ready"
			}
		}
		proofs = append(proofs, proof)
	}
	return proofs, nil
}

func drainDataSequence(tail producerTail, drain preparedDrain) uint64 {
	if drain.terminalSequence > 0 {
		return drain.terminalSequence - 1
	}
	if tail.terminal && tail.sequence > 0 {
		return tail.sequence - 1
	}
	return tail.sequence
}

func (r *HotRuntime) persistDrainProofs(ctx context.Context) error {
	r.proofMu.Lock()
	defer r.proofMu.Unlock()
	proofs, err := r.DrainProofs(ctx)
	if err != nil {
		return err
	}
	raw, err := json.Marshal(drainProofDocument{Format: drainProofFormat, Version: drainProofVersion, Proofs: proofs})
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	if len(raw) > maxDrainProofBytes || bytes.Contains(raw[:len(raw)-1], []byte{'\n'}) {
		return errors.New("propertycatalog: drain proof exceeds canonical file bounds")
	}
	temporary, err := os.CreateTemp(r.cfg.SpoolDirectory, ".drain-proof-tmp-*")
	if err != nil {
		return fmt.Errorf("propertycatalog: create drain proof temporary: %w", err)
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
	if err := os.Rename(temporaryPath, r.DrainProofPath()); err != nil {
		return err
	}
	keep = false
	if err := syncDirectory(r.cfg.SpoolDirectory); err != nil {
		return err
	}
	// Mark prepared only after the canonical proof is durable. A crash between
	// rename and this update reloads the same flag from the proof document.
	r.mu.Lock()
	for _, proof := range proofs {
		if proof.Phase != "prepared" {
			continue
		}
		key := proofStreamKey(proof)
		drain := r.drains[key]
		if drain.intentFenceSHA256 == proof.DrainIntentFenceSHA256 && drain.terminalSequence == proof.TerminalSequence {
			drain.prepared = true
			r.drains[key] = drain
		}
	}
	r.mu.Unlock()
	return nil
}

func proofStreamKey(proof DrainProof) streamKey {
	return streamKey{
		organizationID: proof.OrganizationID, workspaceID: proof.WorkspaceID,
		epoch: proof.CatalogEpoch, revision: proof.CatalogRevision,
		buildToken: proof.BuildToken, adapter: proof.SourceAdapter, streamID: proof.ProducerStreamID,
	}
}

// loadDrainSafety restores prepared boundaries and permanently poisons any
// exact build that lost an admission after returning success to the caller.
func (r *HotRuntime) loadDrainSafety() error {
	path := r.DrainProofPath()
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("propertycatalog: inspect drain proof: %w", err)
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() < 2 || info.Size() > maxDrainProofBytes {
		return errors.New("propertycatalog: drain proof is not a bounded regular file")
	}
	if info.Mode().Perm()&0o022 != 0 {
		return errors.New("propertycatalog: drain proof must not be group/world writable")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if raw[len(raw)-1] != '\n' || bytes.Contains(raw[:len(raw)-1], []byte{'\n'}) {
		return errors.New("propertycatalog: drain proof is not one canonical JSON line")
	}
	body := raw[:len(raw)-1]
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	var document drainProofDocument
	if err := decoder.Decode(&document); err != nil {
		return fmt.Errorf("propertycatalog: decode drain proof: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return err
	}
	canonical, err := json.Marshal(document)
	if err != nil || !bytes.Equal(canonical, body) {
		return errors.New("propertycatalog: drain proof is not canonical JSON")
	}
	if document.Format != drainProofFormat || document.Version != drainProofVersion {
		return errors.New("propertycatalog: drain proof format/version is invalid")
	}
	seen := make(map[streamKey]struct{}, len(document.Proofs))
	for index, proof := range document.Proofs {
		if err := validateDrainProofScope(r, proof); err != nil {
			return fmt.Errorf("propertycatalog: drain proof %d: %w", index, err)
		}
		key := proofStreamKey(proof)
		if _, duplicate := seen[key]; duplicate {
			return errors.New("propertycatalog: drain proof contains a duplicate stream")
		}
		seen[key] = struct{}{}
		if proof.PendingAdmissions > 0 || proof.Poisoned {
			r.admissionPoison[key] = true
		}
		if proof.TerminalSequence > 0 && proof.DrainIntentFenceSHA256 != "" {
			r.drains[key] = preparedDrain{
				intentFenceSHA256: proof.DrainIntentFenceSHA256,
				drainDeadline:     proof.DrainDeadline, terminalSequence: proof.TerminalSequence,
				prepared: proof.Phase == "prepared" || proof.Phase == "bound" || proof.Phase == "ready",
			}
		}
	}
	return nil
}

func validateDrainProofScope(r *HotRuntime, proof DrainProof) error {
	for label, value := range map[string]string{
		"organization": proof.OrganizationID, "workspace": proof.WorkspaceID,
		"build token": proof.BuildToken, "producer stream": proof.ProducerStreamID,
	} {
		if err := validateCanonicalUUID("drain proof "+label, value); err != nil {
			return err
		}
	}
	if proof.CatalogEpoch != r.cfg.CatalogEpoch || proof.CatalogRevision == 0 ||
		proof.ProjectionVersion != r.cfg.ProjectionVersion ||
		!r.cfg.workspaceWithinConfiguredScope(proof.WorkspaceID) ||
		proof.SourceAdapter != AdapterSpanAttribute || proof.ProducerStreamID != r.cfg.ProducerStreamID ||
		!isLowerSHA256(proof.BuildLeaseSHA256) || !isLowerSHA256(proof.ObservedFenceSHA256) ||
		!isLowerSHA256(proof.SourceDigest) || !isLowerSHA256(proof.EmittedDigest) ||
		!isLowerSHA256(proof.TerminalPayloadSHA256) || proof.LastAcknowledgedSequence > proof.LastIssuedSequence ||
		proof.DeliveryCount != proof.LastAcknowledgedSequence ||
		(proof.Ready && (proof.Phase != "ready" || proof.Poisoned || proof.PendingAdmissions != 0 || proof.PendingEnvelopes != 0)) {
		return errors.New("drain proof is outside the runtime safety contract")
	}
	switch proof.Phase {
	case "building", "preparing", "prepared", "bound", "ready", "poisoned":
	default:
		return errors.New("drain proof phase is invalid")
	}
	if proof.DrainIntentFenceSHA256 != "" && !isLowerSHA256(proof.DrainIntentFenceSHA256) {
		return errors.New("drain intent digest is invalid")
	}
	return nil
}
