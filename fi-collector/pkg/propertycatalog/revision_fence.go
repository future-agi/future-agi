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
	"time"
)

const (
	revisionFenceFormat   = "futureagi.property-catalog-revision-fence"
	revisionFenceVersion  = uint16(2)
	maxRevisionFenceBytes = 64 << 20
	maxRevisionProjects   = 256
	// Keep this hard safety bound aligned with
	// PROPERTY_CATALOG_MAX_REVISION_LEASE_SECONDS. Extended initial backfills
	// may use up to the setting's supported 60-minute maximum while preserving
	// lease headroom for the drain handshake.
	maxRevisionLease = 60 * time.Minute
)

// RevisionFence is an activation-control-plane-owned build assignment. The
// hot producer consumes it; it never invents or increments catalog revisions.
type RevisionFence struct {
	OrganizationID    string   `json:"organization_id"`
	WorkspaceID       string   `json:"workspace_id"`
	CatalogEpoch      uint16   `json:"catalog_epoch"`
	CatalogRevision   uint64   `json:"catalog_revision"`
	ProjectionVersion uint16   `json:"projection_version"`
	BuildLeaseSHA256  string   `json:"build_lease_sha256"`
	BuildToken        string   `json:"build_token"`
	ProjectIDs        []string `json:"project_ids"`
	SpanSinceUS       uint64   `json:"span_since_us"`
	SpanUntilUS       uint64   `json:"span_until_us"`
	IssuedAt          string   `json:"issued_at"`
	ExpiresAt         string   `json:"expires_at"`
	DrainDeadline     string   `json:"drain_deadline"`
	FencedSequence    uint64   `json:"fenced_sequence"`
	Status            string   `json:"status"`
	FenceSHA256       string   `json:"fence_sha256"`
}

type revisionFenceDocument struct {
	Format  string          `json:"format"`
	Version uint16          `json:"version"`
	Fences  []RevisionFence `json:"fences"`
}

type RevisionProvider interface {
	CurrentRevision(context.Context, string, string) (RevisionFence, error)
}

// RevisionLister is implemented by providers that can expose every bounded
// assignment. The runtime uses it to observe draining leases and emit a
// terminal envelope even when a workspace had no traffic in the revision.
type RevisionLister interface {
	CurrentRevisions(context.Context) ([]RevisionFence, error)
}

type FileRevisionProvider struct {
	path string
	now  func() time.Time
}

// ErrRevisionNotAssigned is the typed, non-corruption result for a valid
// revision-fence inventory that simply has no current entry for a tenant. It
// must remain distinguishable from an unreadable, malformed, or expired fence
// file, all of which are operational failures and must fail closed.
var ErrRevisionNotAssigned = errors.New("propertycatalog: no revision assignment for tenant scope")

func NewFileRevisionProvider(path string) (*FileRevisionProvider, error) {
	if path == "" || !filepath.IsAbs(path) {
		return nil, errors.New("propertycatalog: revision fence path must be absolute")
	}
	return &FileRevisionProvider{path: filepath.Clean(path), now: time.Now}, nil
}

func RevisionFenceSHA256(fence RevisionFence) string {
	components := []any{
		fence.OrganizationID, fence.WorkspaceID, uint64(fence.CatalogEpoch),
		fence.CatalogRevision, uint64(fence.ProjectionVersion), fence.BuildLeaseSHA256,
		fence.BuildToken, uint64(len(fence.ProjectIDs)),
	}
	for _, projectID := range fence.ProjectIDs {
		components = append(components, projectID)
	}
	components = append(components,
		fence.SpanSinceUS, fence.SpanUntilUS,
		fence.IssuedAt, fence.ExpiresAt, fence.DrainDeadline,
		fence.FencedSequence, fence.Status,
	)
	return framedSHA256("futureagi.property-catalog.revision-fence.v2", components...)
}

func (p *FileRevisionProvider) CurrentRevision(
	ctx context.Context, organizationID, workspaceID string,
) (RevisionFence, error) {
	if p == nil || p.now == nil || ctx == nil {
		return RevisionFence{}, errors.New("propertycatalog: revision provider requires context")
	}
	if err := ctx.Err(); err != nil {
		return RevisionFence{}, err
	}
	if err := validateCanonicalUUID("revision organization", organizationID); err != nil {
		return RevisionFence{}, err
	}
	if err := validateCanonicalUUID("revision workspace", workspaceID); err != nil {
		return RevisionFence{}, err
	}
	fences, err := p.CurrentRevisions(ctx)
	if err != nil {
		return RevisionFence{}, err
	}
	for _, fence := range fences {
		if fence.OrganizationID == organizationID && fence.WorkspaceID == workspaceID {
			return fence, nil
		}
	}
	return RevisionFence{}, fmt.Errorf(
		"%w: organization=%s workspace=%s",
		ErrRevisionNotAssigned, organizationID, workspaceID,
	)
}

func (p *FileRevisionProvider) CurrentRevisions(ctx context.Context) ([]RevisionFence, error) {
	if p == nil || p.now == nil || ctx == nil {
		return nil, errors.New("propertycatalog: revision provider requires context")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	info, err := os.Lstat(p.path)
	if err != nil {
		return nil, fmt.Errorf("propertycatalog: inspect revision fence: %w", err)
	}
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() < 2 || info.Size() > maxRevisionFenceBytes {
		return nil, errors.New("propertycatalog: revision fence is not a bounded regular file")
	}
	if info.Mode().Perm()&0o022 != 0 {
		return nil, errors.New("propertycatalog: revision fence must not be group/world writable")
	}
	raw, err := os.ReadFile(p.path)
	if err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if raw[len(raw)-1] != '\n' || bytes.Contains(raw[:len(raw)-1], []byte{'\n'}) {
		return nil, errors.New("propertycatalog: revision fence must be one canonical JSON line")
	}
	body := raw[:len(raw)-1]
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	var document revisionFenceDocument
	if err := decoder.Decode(&document); err != nil {
		return nil, fmt.Errorf("propertycatalog: decode revision fence: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return nil, err
	}
	canonical, err := json.Marshal(document)
	if err != nil || !bytes.Equal(canonical, body) {
		return nil, errors.New("propertycatalog: revision fence is not canonical JSON")
	}
	if document.Format != revisionFenceFormat || document.Version != revisionFenceVersion ||
		len(document.Fences) == 0 {
		return nil, errors.New("propertycatalog: revision fence format/version/count is invalid")
	}
	if !sort.SliceIsSorted(document.Fences, func(i, j int) bool {
		if document.Fences[i].OrganizationID != document.Fences[j].OrganizationID {
			return document.Fences[i].OrganizationID < document.Fences[j].OrganizationID
		}
		return document.Fences[i].WorkspaceID < document.Fences[j].WorkspaceID
	}) {
		return nil, errors.New("propertycatalog: revision fences must be tenant-sorted")
	}
	for index, fence := range document.Fences {
		if index > 0 && fence.OrganizationID == document.Fences[index-1].OrganizationID &&
			fence.WorkspaceID == document.Fences[index-1].WorkspaceID {
			return nil, errors.New("propertycatalog: revision fence contains duplicate tenant scope")
		}
		if err := validateRevisionFence(fence, p.now().UTC()); err != nil {
			return nil, fmt.Errorf("propertycatalog: revision fence %d: %w", index, err)
		}
	}
	result := make([]RevisionFence, len(document.Fences))
	for index, fence := range document.Fences {
		result[index] = cloneRevisionFence(fence)
	}
	return result, nil
}

func validateRevisionFence(fence RevisionFence, now time.Time) error {
	if err := validateCanonicalUUID("fence organization", fence.OrganizationID); err != nil {
		return err
	}
	if err := validateCanonicalUUID("fence workspace", fence.WorkspaceID); err != nil {
		return err
	}
	if fence.CatalogEpoch == 0 || fence.CatalogRevision == 0 || fence.ProjectionVersion == 0 ||
		(fence.Status != "building" && fence.Status != "draining" && fence.Status != "fenced") {
		return errors.New("fence must assign a positive epoch/revision/projection and known status")
	}
	if !isLowerSHA256(fence.BuildLeaseSHA256) {
		return errors.New("fence build lease digest is invalid")
	}
	if err := validateCanonicalUUID("fence build token", fence.BuildToken); err != nil {
		return err
	}
	if err := validateRevisionSourceScope(
		fence.ProjectIDs, fence.SpanSinceUS, fence.SpanUntilUS,
	); err != nil {
		return fmt.Errorf("fence source scope: %w", err)
	}
	issuedAt, issuedErr := time.Parse(dateTime64Layout, fence.IssuedAt)
	expiresAt, expiresErr := time.Parse(dateTime64Layout, fence.ExpiresAt)
	if issuedErr != nil || expiresErr != nil || issuedAt.Format(dateTime64Layout) != fence.IssuedAt ||
		expiresAt.Format(dateTime64Layout) != fence.ExpiresAt || !expiresAt.After(issuedAt) {
		return errors.New("fence lease timestamps are non-canonical or unordered")
	}
	switch fence.Status {
	case "building":
		if !expiresAt.After(now) {
			return errors.New("building fence lease is expired")
		}
		if fence.DrainDeadline != "" || fence.FencedSequence != 0 {
			return errors.New("building fence cannot assign a drain boundary")
		}
	case "draining":
		drainDeadline, err := time.Parse(dateTime64Layout, fence.DrainDeadline)
		if err != nil || drainDeadline.Format(dateTime64Layout) != fence.DrainDeadline ||
			!drainDeadline.After(now) || !drainDeadline.After(issuedAt) ||
			drainDeadline.Sub(issuedAt) > maxRevisionLease {
			return errors.New("draining fence deadline is invalid, expired, or too wide")
		}
	case "fenced":
		if fence.DrainDeadline != "" {
			drainDeadline, err := time.Parse(dateTime64Layout, fence.DrainDeadline)
			if err != nil || drainDeadline.Format(dateTime64Layout) != fence.DrainDeadline ||
				!drainDeadline.After(issuedAt) {
				return errors.New("fenced drain deadline is non-canonical or unordered")
			}
		} else if fence.FencedSequence != 0 {
			return errors.New("fenced sequence requires its drain deadline")
		}
	}
	if !isLowerSHA256(fence.FenceSHA256) || fence.FenceSHA256 != RevisionFenceSHA256(fence) {
		return errors.New("fence digest does not match its assignment")
	}
	return nil
}

// EncodeRevisionFenceFile is intentionally pure and used by the control plane
// handoff/tests to produce the exact atomic-file payload expected above.
func EncodeRevisionFenceFile(fences []RevisionFence) ([]byte, error) {
	if len(fences) == 0 {
		return nil, errors.New("propertycatalog: revision fence count is invalid")
	}
	cloned := make([]RevisionFence, len(fences))
	for index, fence := range fences {
		cloned[index] = cloneRevisionFence(fence)
	}
	sort.Slice(cloned, func(i, j int) bool {
		if cloned[i].OrganizationID != cloned[j].OrganizationID {
			return cloned[i].OrganizationID < cloned[j].OrganizationID
		}
		return cloned[i].WorkspaceID < cloned[j].WorkspaceID
	})
	for index := range cloned {
		cloned[index].FenceSHA256 = RevisionFenceSHA256(cloned[index])
	}
	raw, err := json.Marshal(revisionFenceDocument{Format: revisionFenceFormat, Version: revisionFenceVersion, Fences: cloned})
	if err != nil {
		return nil, err
	}
	raw = append(raw, '\n')
	if len(raw) > maxRevisionFenceBytes {
		return nil, errors.New("propertycatalog: revision fence exceeds byte limit")
	}
	return raw, nil
}

func validateRevisionSourceScope(projectIDs []string, spanSinceUS, spanUntilUS uint64) error {
	if len(projectIDs) == 0 || len(projectIDs) > maxRevisionProjects {
		return errors.New("project inventory must contain 1..256 projects")
	}
	if !sort.StringsAreSorted(projectIDs) {
		return errors.New("project inventory must be canonical-sorted")
	}
	for index, projectID := range projectIDs {
		if err := validateCanonicalUUID(fmt.Sprintf("source-scope project %d", index), projectID); err != nil {
			return err
		}
		if index > 0 && projectID == projectIDs[index-1] {
			return errors.New("project inventory contains a duplicate")
		}
	}
	if spanSinceUS == 0 || spanUntilUS == 0 || spanSinceUS >= spanUntilUS {
		return errors.New("span window must be a positive increasing half-open interval")
	}
	return nil
}

func validateRevisionSourceObservation(
	projectIDs []string, spanSinceUS, spanUntilUS uint64,
	projectID string, firstSeen, lastSeen time.Time,
) error {
	if err := validateRevisionSourceScope(projectIDs, spanSinceUS, spanUntilUS); err != nil {
		return fmt.Errorf("invalid revision source scope: %w", err)
	}
	projectIndex := sort.SearchStrings(projectIDs, projectID)
	if projectIndex == len(projectIDs) || projectIDs[projectIndex] != projectID {
		return errors.New("hot row project is outside the revision source scope")
	}
	if firstSeen.IsZero() || lastSeen.IsZero() || lastSeen.Before(firstSeen) ||
		firstSeen.UnixMicro() < 0 || lastSeen.UnixMicro() < 0 {
		return errors.New("hot row time range is invalid")
	}
	firstUS, lastUS := uint64(firstSeen.UnixMicro()), uint64(lastSeen.UnixMicro())
	if firstUS < spanSinceUS || lastUS >= spanUntilUS {
		return errors.New("hot row time is outside the half-open revision span window")
	}
	return nil
}

func cloneRevisionFence(fence RevisionFence) RevisionFence {
	fence.ProjectIDs = append([]string(nil), fence.ProjectIDs...)
	return fence
}
