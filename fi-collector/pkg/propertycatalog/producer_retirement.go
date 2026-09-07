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
	"strings"
	"time"
)

const (
	producerRetirementFormat    = "futureagi.property-catalog-producer-state-retirements"
	producerRetirementVersion   = uint16(1)
	producerRetirementFileName  = "producer-state-retirements-v1.json"
	producerRetirementSHADomain = "futureagi.property-catalog.producer-state-retirement.v1"
	maxProducerRetirementBytes  = 64 << 20
)

type producerRetirementDocument struct {
	Format      string                    `json:"format"`
	Version     uint16                    `json:"version"`
	Retirements []ProducerStateRetirement `json:"retirements"`
}

// ProducerStateRetirement is the Python control plane's activation-proven
// high-water. It is intentionally not derived from the mutable fence file.
// The canonical active build plan and activation lineage are reread from
// ClickHouse before Python atomically publishes this record.
type ProducerStateRetirement struct {
	OrganizationID                  string `json:"organization_id"`
	WorkspaceID                     string `json:"workspace_id"`
	CatalogEpoch                    uint16 `json:"catalog_epoch"`
	CatalogRevision                 uint64 `json:"catalog_revision"`
	BuildToken                      string `json:"build_token"`
	ProjectionVersion               uint16 `json:"projection_version"`
	LifecycleMode                   string `json:"lifecycle_mode"`
	BuildPlanJSON                   string `json:"build_plan_json"`
	BuildLeaseSHA256                string `json:"build_lease_sha256"`
	SourceManifestSHA256            string `json:"source_manifest_sha256"`
	ActivationSequence              uint64 `json:"activation_sequence"`
	ActivationSHA256                string `json:"activation_sha256"`
	LineageAnchorRevision           uint64 `json:"lineage_anchor_revision"`
	LineageAnchorBuildToken         string `json:"lineage_anchor_build_token"`
	LineageAnchorActivationSequence uint64 `json:"lineage_anchor_activation_sequence"`
	LineageAnchorActivationSHA256   string `json:"lineage_anchor_activation_sha256"`
	ActiveRevisionsSinceAnchor      uint64 `json:"active_revisions_since_anchor"`
	HotProducerStreamID             string `json:"hot_producer_stream_id"`
	EmittedAt                       string `json:"emitted_at"`
	RetirementSHA256                string `json:"retirement_sha256"`
}

type producerRetirementTenant struct {
	organizationID string
	workspaceID    string
}

func producerRetirementSHA256(value ProducerStateRetirement) string {
	return framedSHA256(
		producerRetirementSHADomain,
		value.OrganizationID,
		value.WorkspaceID,
		uint64(value.CatalogEpoch),
		value.CatalogRevision,
		value.BuildToken,
		uint64(value.ProjectionVersion),
		value.LifecycleMode,
		value.BuildPlanJSON,
		value.BuildLeaseSHA256,
		value.SourceManifestSHA256,
		value.ActivationSequence,
		value.ActivationSHA256,
		value.LineageAnchorRevision,
		value.LineageAnchorBuildToken,
		value.LineageAnchorActivationSequence,
		value.LineageAnchorActivationSHA256,
		value.ActiveRevisionsSinceAnchor,
		value.HotProducerStreamID,
		value.EmittedAt,
	)
}

func loadProducerRetirements(directory string) (map[producerRetirementTenant]ProducerStateRetirement, error) {
	path := filepath.Join(directory, producerRetirementFileName)
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return map[producerRetirementTenant]ProducerStateRetirement{}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("propertycatalog: inspect producer retirement proof: %w", err)
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm() != 0o600 ||
		info.Size() < 2 || info.Size() > maxProducerRetirementBytes {
		return nil, errors.New("propertycatalog: producer retirement proof type, mode, or size is unsafe")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("propertycatalog: read producer retirement proof: %w", err)
	}
	if raw[len(raw)-1] != '\n' || bytes.Contains(raw[:len(raw)-1], []byte{'\n'}) {
		return nil, errors.New("propertycatalog: producer retirement proof is not one canonical JSON line")
	}
	body := raw[:len(raw)-1]
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	var document producerRetirementDocument
	if err := decoder.Decode(&document); err != nil {
		return nil, fmt.Errorf("propertycatalog: decode producer retirement proof: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return nil, err
	}
	canonical, err := json.Marshal(document)
	if err != nil || !bytes.Equal(canonical, body) {
		return nil, errors.New("propertycatalog: producer retirement proof is not canonical JSON")
	}
	if document.Format != producerRetirementFormat || document.Version != producerRetirementVersion ||
		len(document.Retirements) == 0 {
		return nil, errors.New("propertycatalog: producer retirement proof format/version/count is invalid")
	}
	if !sort.SliceIsSorted(document.Retirements, func(i, j int) bool {
		left, right := document.Retirements[i], document.Retirements[j]
		if left.OrganizationID != right.OrganizationID {
			return left.OrganizationID < right.OrganizationID
		}
		return left.WorkspaceID < right.WorkspaceID
	}) {
		return nil, errors.New("propertycatalog: producer retirements are not tenant-sorted")
	}
	result := make(map[producerRetirementTenant]ProducerStateRetirement, len(document.Retirements))
	for index, retirement := range document.Retirements {
		if err := validateProducerRetirement(retirement); err != nil {
			return nil, fmt.Errorf("propertycatalog: producer retirement %d: %w", index, err)
		}
		key := producerRetirementTenant{retirement.OrganizationID, retirement.WorkspaceID}
		if _, duplicate := result[key]; duplicate {
			return nil, errors.New("propertycatalog: producer retirement proof contains a duplicate tenant")
		}
		result[key] = retirement
	}
	return result, nil
}

func validateProducerRetirement(value ProducerStateRetirement) error {
	for label, candidate := range map[string]string{
		"retirement organization":        value.OrganizationID,
		"retirement workspace":           value.WorkspaceID,
		"retirement build token":         value.BuildToken,
		"retirement anchor build token":  value.LineageAnchorBuildToken,
		"retirement hot producer stream": value.HotProducerStreamID,
	} {
		if err := validateCanonicalUUID(label, candidate); err != nil {
			return err
		}
	}
	if value.CatalogEpoch == 0 || value.CatalogRevision == 0 || value.ProjectionVersion == 0 ||
		value.ActivationSequence == 0 || value.LineageAnchorRevision == 0 ||
		value.LineageAnchorActivationSequence == 0 || value.ActiveRevisionsSinceAnchor > 2048 {
		return errors.New("retirement revision, activation, or lineage bound is invalid")
	}
	for label, digest := range map[string]string{
		"build lease":       value.BuildLeaseSHA256,
		"source manifest":   value.SourceManifestSHA256,
		"activation":        value.ActivationSHA256,
		"anchor activation": value.LineageAnchorActivationSHA256,
		"retirement":        value.RetirementSHA256,
	} {
		if !isLowerSHA256(digest) {
			return fmt.Errorf("%s digest is invalid", label)
		}
	}
	emitted, err := time.Parse(dateTime64Layout, value.EmittedAt)
	if err != nil || emitted.UTC().Format(dateTime64Layout) != value.EmittedAt {
		return errors.New("retirement emission time is not canonical UTC")
	}
	if value.ActivationSequence < value.LineageAnchorActivationSequence ||
		value.ActivationSequence-value.LineageAnchorActivationSequence != value.ActiveRevisionsSinceAnchor {
		return errors.New("retirement activation depth differs from its lineage anchor")
	}
	snapshot := value.LifecycleMode == "initial_backfill" || value.LifecycleMode == "full_repair"
	switch {
	case snapshot:
		if value.LineageAnchorRevision != value.CatalogRevision ||
			value.LineageAnchorBuildToken != value.BuildToken ||
			value.LineageAnchorActivationSequence != value.ActivationSequence ||
			value.LineageAnchorActivationSHA256 != value.ActivationSHA256 ||
			value.ActiveRevisionsSinceAnchor != 0 {
			return errors.New("snapshot retirement is not its own lineage anchor")
		}
	case value.LifecycleMode == "incremental":
		if value.LineageAnchorRevision >= value.CatalogRevision ||
			value.LineageAnchorActivationSequence >= value.ActivationSequence {
			return errors.New("incremental retirement has no earlier lineage anchor")
		}
	default:
		return errors.New("retirement lifecycle mode is unsupported")
	}
	request := DeliveryLeaseRequest{
		OrganizationID: value.OrganizationID, WorkspaceID: value.WorkspaceID,
		CatalogEpoch: value.CatalogEpoch, CatalogRevision: value.CatalogRevision,
		BuildToken: value.BuildToken, ProjectionVersion: value.ProjectionVersion,
		SourceAdapter: AdapterSpanAttribute, ProducerStreamID: value.HotProducerStreamID,
		EnvelopeVersion: EnvelopeVersion, Sequence: 1,
	}
	evidence, err := validateBuildPlan(value.BuildPlanJSON, value.BuildLeaseSHA256, request)
	if err != nil {
		return fmt.Errorf("retirement build plan: %w", err)
	}
	if evidence.StreamRole != "hot_values" {
		return errors.New("retirement stream is not the active hot-values stream")
	}
	var plan buildPlanDocumentJSON
	if err := json.Unmarshal([]byte(value.BuildPlanJSON), &plan); err != nil {
		return err
	}
	prefix := value.LifecycleMode + "_"
	for _, stream := range plan.Streams {
		if !strings.HasPrefix(stream.SourceCutoff.Label, prefix) {
			return errors.New("retirement lifecycle mode differs from its build plan")
		}
	}
	if value.RetirementSHA256 != producerRetirementSHA256(value) {
		return errors.New("retirement digest does not match its fields")
	}
	return nil
}

func (r *HotRuntime) compactProducerState(ctx context.Context, fences []RevisionFence) error {
	if r == nil || r.publisher == nil || r.publisher.state == nil || ctx == nil {
		return errors.New("propertycatalog: producer retirement requires a runtime context")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	r.retirementMu.Lock()
	defer r.retirementMu.Unlock()
	retirements, err := loadProducerRetirements(r.cfg.SpoolDirectory)
	if err != nil || len(retirements) == 0 {
		return err
	}
	current := make(map[producerRetirementTenant]RevisionFence, len(fences))
	for _, fence := range fences {
		key := producerRetirementTenant{fence.OrganizationID, fence.WorkspaceID}
		if _, duplicate := current[key]; duplicate {
			return errors.New("propertycatalog: current fence contains duplicate retirement tenant")
		}
		current[key] = fence
	}
	pendingEnvelopes, err := r.spool.PendingEnvelopes()
	if err != nil {
		return err
	}
	pending := make(map[streamKey]struct{}, len(pendingEnvelopes))
	for _, envelope := range pendingEnvelopes {
		snapshot := envelope.Snapshot()
		pending[streamKey{
			organizationID: snapshot.OrganizationID, workspaceID: snapshot.WorkspaceID,
			epoch: snapshot.CatalogEpoch, revision: snapshot.CatalogRevision,
			buildToken: snapshot.BuildToken, adapter: snapshot.SourceAdapter,
			streamID: snapshot.ProducerStreamID,
		}] = struct{}{}
	}
	covered := func(key streamKey, projection uint16) (bool, error) {
		retirement, proven := retirements[producerRetirementTenant{
			key.organizationID, key.workspaceID,
		}]
		fence, assigned := current[producerRetirementTenant{
			key.organizationID, key.workspaceID,
		}]
		if !proven || !assigned || key.epoch != retirement.CatalogEpoch ||
			key.revision > retirement.CatalogRevision || projection != retirement.ProjectionVersion ||
			key.adapter != AdapterSpanAttribute || key.streamID != retirement.HotProducerStreamID ||
			fence.CatalogEpoch != key.epoch || fence.CatalogRevision <= key.revision ||
			fence.ProjectionVersion != projection {
			return false, nil
		}
		if key.revision == retirement.CatalogRevision && key.buildToken != retirement.BuildToken {
			return false, errors.New("propertycatalog: active retirement conflicts with producer checkpoint build")
		}
		return true, nil
	}
	remove := make(map[streamKey]struct{})
	checkpoints := r.publisher.state.snapshot()
	for key, checkpoint := range checkpoints {
		isCovered, coverErr := covered(key, checkpoint.ProjectionVersion)
		if coverErr != nil {
			return coverErr
		}
		if !isCovered || !checkpoint.Terminal {
			continue
		}
		if _, exists := pending[key]; exists {
			continue
		}
		remove[key] = struct{}{}
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	for key := range remove {
		if r.pendingAdmissions[key] != 0 {
			return errors.New("propertycatalog: producer retirement crossed a pending admission")
		}
	}
	cleanupDrains := make(map[streamKey]struct{})
	for key := range r.drains {
		if _, hasCheckpoint := checkpoints[key]; hasCheckpoint {
			if _, willRemove := remove[key]; !willRemove {
				continue
			}
		}
		if _, hasPending := pending[key]; hasPending || r.pendingAdmissions[key] != 0 {
			continue
		}
		isCovered, coverErr := covered(key, r.cfg.ProjectionVersion)
		if coverErr != nil {
			return coverErr
		}
		if isCovered {
			cleanupDrains[key] = struct{}{}
		}
	}
	if len(remove) != 0 {
		if err := r.publisher.state.remove(remove); err != nil {
			return err
		}
	}
	for key := range remove {
		delete(r.tails, key)
		delete(r.pendingAdmissions, key)
		delete(r.admissionPoison, key)
		delete(r.drains, key)
	}
	// A prior successful compaction may have persisted the empty checkpoint
	// document immediately before a crash. loadDrainSafety then restores the old
	// ready marker from the separately durable proof. Clear only such checkpoint-
	// free, spool-free markers covered by the same activation high-water; no
	// producer-state write is needed because the checkpoint is already absent.
	for key := range cleanupDrains {
		delete(r.drains, key)
		delete(r.admissionPoison, key)
	}
	return nil
}
