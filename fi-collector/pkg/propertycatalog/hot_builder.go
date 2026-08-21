package propertycatalog

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
)

// ScopedSpan keeps authenticated tenant scope out-of-band. Organization and
// workspace are never injected into resource_attrs or any existing span-table
// column merely to feed the optional catalog path.
type ScopedSpan struct {
	OrganizationID string
	WorkspaceID    string
	ScopeError     string
	Row            map[string]any
}

type hotGroupKey struct {
	organizationID string
	workspaceID    string
	projectID      string
}

type hotGroup struct {
	key       hotGroupKey
	spans     uint64
	firstSeen time.Time
	lastSeen  time.Time
	values    map[string]AttributeValueRow
	gaps      map[string]struct{}
}

func collectHotGroups(cfg RuntimeConfig, rows []ScopedSpan) ([]hotGroup, []error) {
	if len(rows) > cfg.MaxSpansPerBatch {
		return nil, []error{fmt.Errorf("propertycatalog: canonical batch has %d spans, limit %d", len(rows), cfg.MaxSpansPerBatch)}
	}
	groups := make(map[hotGroupKey]*hotGroup)
	errs := make([]error, 0)
	limits := attributecatalog.BuildLimits{
		MaxKeys: cfg.MaxKeysPerSpan, MaxArrayMembers: cfg.MaxArrayMembersPerSpan,
		MaxEncodedBytes: cfg.MaxEncodedBytesPerSpan,
	}
	for index, scoped := range rows {
		row := scoped.Row
		key, seenAt, allowed, err := hotRowScope(cfg, scoped)
		if err != nil {
			errs = append(errs, fmt.Errorf("propertycatalog: canonical span %d: %w", index, err))
			continue
		}
		if !allowed {
			continue
		}
		group := groups[key]
		if group == nil {
			group = &hotGroup{
				key: key, values: make(map[string]AttributeValueRow), gaps: make(map[string]struct{}),
			}
			groups[key] = group
		}
		if group.spans == 0 || seenAt.Before(group.firstSeen) {
			group.firstSeen = seenAt
		}
		if group.spans == 0 || seenAt.After(group.lastSeen) {
			group.lastSeen = seenAt
		}
		group.spans++
		attrs, ok := hotAttributeMaps(row)
		if !ok {
			group.gaps["invalid_canonical_attributes"] = struct{}{}
			continue
		}
		built, err := attributecatalog.BuildRows(
			attributecatalog.Scope{ProjectID: key.projectID, SeenAt: seenAt, CatalogEpoch: cfg.CatalogEpoch},
			attrs, limits,
		)
		if err != nil {
			group.gaps["attribute_builder_error"] = struct{}{}
			continue
		}
		mergeBuiltRows(group, built, key, KindCustomAttribute)
		for _, reason := range built.Metadata.GapReasons {
			group.gaps[reason] = struct{}{}
		}

		// Code-owned hot columns are value observations only. Their definitions
		// come from the checked-in system manifest, so the collector cannot
		// create a second, drifting definition for the same property_id.
		if model, ok := row["model"].(string); ok && model != "" {
			system, systemErr := attributecatalog.BuildRowsForSource(
				attributecatalog.Scope{ProjectID: key.projectID, SeenAt: seenAt, CatalogEpoch: cfg.CatalogEpoch},
				attributecatalog.SpanAttributeMaps{Strings: map[string]string{"model": model}},
				limits, attributecatalog.SourceKindSystemAttribute,
			)
			if systemErr != nil {
				group.gaps["system_attribute_builder_error"] = struct{}{}
			} else {
				mergeBuiltRows(group, system, key, KindSystemAttribute)
				// Remove the synthetic key definition: system_manifest is its sole
				// definition owner. mergeBuiltRows never added one for this kind.
				for _, reason := range system.Metadata.GapReasons {
					group.gaps[reason] = struct{}{}
				}
			}
		}
	}
	result := make([]hotGroup, 0, len(groups))
	for _, group := range groups {
		result = append(result, *group)
	}
	sort.Slice(result, func(i, j int) bool {
		left, right := result[i].key, result[j].key
		if left.organizationID != right.organizationID {
			return left.organizationID < right.organizationID
		}
		if left.workspaceID != right.workspaceID {
			return left.workspaceID < right.workspaceID
		}
		return left.projectID < right.projectID
	})
	return result, errs
}

func hotRowScope(cfg RuntimeConfig, scoped ScopedSpan) (hotGroupKey, time.Time, bool, error) {
	if scoped.ScopeError != "" {
		return hotGroupKey{}, time.Time{}, false, fmt.Errorf("authenticated project/workspace proof failed: %s", scoped.ScopeError)
	}
	row := scoped.Row
	organizationID := scoped.OrganizationID
	workspaceID := scoped.WorkspaceID
	rowOrganizationID, ok := row["org_id"].(string)
	if !ok || rowOrganizationID != organizationID {
		return hotGroupKey{}, time.Time{}, false, errors.New("canonical org_id does not match authenticated scope")
	}
	projectID, ok := row["project_id"].(string)
	if !ok {
		return hotGroupKey{}, time.Time{}, false, errors.New("project_id is absent or not a string")
	}
	if err := validateCanonicalUUID("organization", organizationID); err != nil {
		return hotGroupKey{}, time.Time{}, false, err
	}
	if err := validateCanonicalUUID("workspace", workspaceID); err != nil {
		return hotGroupKey{}, time.Time{}, false, err
	}
	if err := validateCanonicalUUID("project", projectID); err != nil {
		return hotGroupKey{}, time.Time{}, false, err
	}
	if !cfg.WorkspaceAllowed(workspaceID) {
		return hotGroupKey{}, time.Time{}, false, nil
	}
	seenText, ok := row["start_time"].(string)
	if !ok {
		return hotGroupKey{}, time.Time{}, false, errors.New("start_time is absent or not a string")
	}
	seenAt, err := time.Parse(dateTime64Layout, seenText)
	if err != nil || seenAt.Format(dateTime64Layout) != seenText {
		return hotGroupKey{}, time.Time{}, false, errors.New("start_time is not canonical DateTime64(6)")
	}
	return hotGroupKey{organizationID, workspaceID, projectID}, seenAt, true, nil
}

func hotAttributeMaps(row map[string]any) (attributecatalog.SpanAttributeMaps, bool) {
	stringsMap, ok := row["attrs_string"].(map[string]string)
	if !ok {
		return attributecatalog.SpanAttributeMaps{}, false
	}
	numbersMap, ok := row["attrs_number"].(map[string]float64)
	if !ok {
		return attributecatalog.SpanAttributeMaps{}, false
	}
	booleansMap, ok := row["attrs_bool"].(map[string]uint8)
	if !ok {
		return attributecatalog.SpanAttributeMaps{}, false
	}
	extraMap, ok := row["attributes_extra"].(map[string]any)
	if !ok {
		return attributecatalog.SpanAttributeMaps{}, false
	}
	return attributecatalog.SpanAttributeMaps{
		Strings: stringsMap, Numbers: numbersMap, Booleans: booleansMap, Extra: extraMap,
	}, true
}

func mergeBuiltRows(group *hotGroup, built attributecatalog.BuildResult, key hotGroupKey, kind PropertyKind) {
	for _, row := range built.ValueRows {
		foldedSearchText := foldPropertyText(row.ValueSearchText)
		if len(row.ValueJSON) > MaxValueJSONBytes || len(foldedSearchText) > MaxValueSearchTextBytes {
			group.gaps[attributecatalog.GapMaxEncodedBytes] = struct{}{}
			continue
		}
		first := row.FirstSeen.UTC().Format(dateTime64Layout)
		last := row.LastSeen.UTC().Format(dateTime64Layout)
		identity := strings.Join([]string{string(kind), row.AttributeKey, row.AttributeType, row.ValueFingerprint}, "\x00")
		current, exists := group.values[identity]
		if !exists {
			group.values[identity] = AttributeValueRow{
				OrganizationID: key.organizationID, WorkspaceID: key.workspaceID, ProjectID: key.projectID,
				CatalogEpoch: row.CatalogEpoch, SourceKind: kind, AttributeKey: row.AttributeKey,
				AttributeType: row.AttributeType, ValueFingerprint: row.ValueFingerprint,
				ValueJSON: row.ValueJSON, ValueSearchTextFolded: foldedSearchText,
				FirstSeen: first, LastSeen: last,
			}
			continue
		}
		if first < current.FirstSeen {
			current.FirstSeen = first
		}
		if last > current.LastSeen {
			current.LastSeen = last
		}
		group.values[identity] = current
	}
}

func hotGroupDigest(group hotGroup) string {
	hash := sha256.New()
	writeFramed := func(value string) {
		var size [8]byte
		binary.BigEndian.PutUint64(size[:], uint64(len(value)))
		_, _ = hash.Write(size[:])
		_, _ = hash.Write([]byte(value))
	}
	writeFramed("futureagi.property-catalog.hot-source-batch.v1")
	writeFramed(group.key.organizationID)
	writeFramed(group.key.workspaceID)
	writeFramed(group.key.projectID)
	valueKeys := make([]string, 0, len(group.values))
	for key := range group.values {
		valueKeys = append(valueKeys, key)
	}
	sort.Strings(valueKeys)
	for _, key := range valueKeys {
		row := group.values[key]
		writeFramed(key)
		writeFramed(row.ValueJSON)
		writeFramed(row.FirstSeen)
		writeFramed(row.LastSeen)
	}
	gaps := sortedGapReasons(group.gaps)
	for _, gap := range gaps {
		writeFramed(gap)
	}
	return hex.EncodeToString(hash.Sum(nil))
}

func buildHotEnvelope(
	cfg RuntimeConfig,
	fence RevisionFence,
	group hotGroup,
	sequence uint64,
	previousPayloadSHA256 string,
) (WireEnvelope, error) {
	if err := validateHotFenceObservation(
		fence, group.key, group.firstSeen, group.lastSeen,
	); err != nil {
		return WireEnvelope{}, err
	}
	fingerprint := hotGroupDigest(group)
	values := make([]AttributeValueRow, 0, len(group.values))
	valueKeys := make([]string, 0, len(group.values))
	for key := range group.values {
		valueKeys = append(valueKeys, key)
	}
	sort.Strings(valueKeys)
	for _, key := range valueKeys {
		row := group.values[key]
		row.CatalogRevision = fence.CatalogRevision
		row.BuildToken = fence.BuildToken
		values = append(values, row)
	}
	payload, err := BuildPayload(
		nil, values, cfg.MaxChunkRows, cfg.MaxChunkBytes, group.spans, fingerprint,
	)
	if err != nil {
		return WireEnvelope{}, err
	}
	if gaps := sortedGapReasons(group.gaps); len(gaps) != 0 {
		payload.Outcome = OutcomeGap
		payload.GapReasons = gaps
	}
	return NewWireEnvelope(EnvelopeInput{
		OrganizationID: group.key.organizationID, WorkspaceID: group.key.workspaceID,
		CatalogEpoch: fence.CatalogEpoch, CatalogRevision: fence.CatalogRevision,
		BuildToken:        fence.BuildToken,
		ProjectionVersion: fence.ProjectionVersion, SourceAdapter: AdapterSpanAttribute,
		SourceVersion: sequence, SourceFingerprint: fingerprint,
		ProducerStreamID: cfg.ProducerStreamID, Sequence: sequence,
		PreviousPayloadSHA256: previousPayloadSHA256, Payload: payload,
	})
}

func validateHotFenceObservation(
	fence RevisionFence, key hotGroupKey, firstSeen, lastSeen time.Time,
) error {
	if fence.OrganizationID != key.organizationID || fence.WorkspaceID != key.workspaceID {
		return errors.New("propertycatalog: hot row tenant is outside the revision source scope")
	}
	if err := validateRevisionSourceObservation(
		fence.ProjectIDs, fence.SpanSinceUS, fence.SpanUntilUS,
		key.projectID, firstSeen, lastSeen,
	); err != nil {
		return fmt.Errorf("propertycatalog: %w", err)
	}
	return nil
}

func buildHotTerminalEnvelope(
	cfg RuntimeConfig,
	fence RevisionFence,
	sequence uint64,
	previousPayloadSHA256 string,
) (WireEnvelope, error) {
	digest := framedSHA256(
		"futureagi.property-catalog.span-attribute-terminal.v1",
		fence.OrganizationID, fence.WorkspaceID, uint64(fence.CatalogEpoch),
		fence.CatalogRevision, fence.BuildToken, uint64(fence.ProjectionVersion),
		cfg.ProducerStreamID, sequence, previousPayloadSHA256,
	)
	payload, err := BuildPayload(nil, nil, cfg.MaxChunkRows, cfg.MaxChunkBytes, 0, digest)
	if err != nil {
		return WireEnvelope{}, err
	}
	return NewWireEnvelope(EnvelopeInput{
		OrganizationID: fence.OrganizationID, WorkspaceID: fence.WorkspaceID,
		CatalogEpoch: fence.CatalogEpoch, CatalogRevision: fence.CatalogRevision,
		BuildToken: fence.BuildToken, ProjectionVersion: fence.ProjectionVersion,
		SourceAdapter: AdapterSpanAttribute, SourceVersion: sequence,
		SourceFingerprint: digest, ProducerStreamID: cfg.ProducerStreamID,
		Sequence: sequence, Terminal: true, PreviousPayloadSHA256: previousPayloadSHA256,
		Payload: payload,
	})
}

func buildHotGapEnvelope(
	cfg RuntimeConfig,
	fence RevisionFence,
	sequence uint64,
	previousPayloadSHA256 string,
	sourceRows uint64,
	reason string,
) (WireEnvelope, error) {
	digest := framedSHA256(
		"futureagi.property-catalog.span-attribute-gap.v1",
		fence.OrganizationID, fence.WorkspaceID, uint64(fence.CatalogEpoch),
		fence.CatalogRevision, fence.BuildToken, uint64(fence.ProjectionVersion),
		cfg.ProducerStreamID, sequence, previousPayloadSHA256, sourceRows, reason,
	)
	payload, err := BuildPayload(nil, nil, cfg.MaxChunkRows, cfg.MaxChunkBytes, sourceRows, digest)
	if err != nil {
		return WireEnvelope{}, err
	}
	payload.Outcome = OutcomeGap
	payload.GapReasons = []string{reason}
	return NewWireEnvelope(EnvelopeInput{
		OrganizationID: fence.OrganizationID, WorkspaceID: fence.WorkspaceID,
		CatalogEpoch: fence.CatalogEpoch, CatalogRevision: fence.CatalogRevision,
		BuildToken: fence.BuildToken, ProjectionVersion: fence.ProjectionVersion,
		SourceAdapter: AdapterSpanAttribute, SourceVersion: sequence,
		SourceFingerprint: digest, ProducerStreamID: cfg.ProducerStreamID,
		Sequence: sequence, PreviousPayloadSHA256: previousPayloadSHA256, Payload: payload,
	})
}

func sortedGapReasons(values map[string]struct{}) []string {
	result := make([]string, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}
