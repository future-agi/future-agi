// Package catalogwriter stages and drains bounded span-attribute catalog jobs.
//
// The package is deliberately independent from the canonical span writer. A
// catalog failure is persisted in its own spool and returned to its caller; it
// can neither write the span dead-letter nor change span-writer health stats.
// No ingestion path is wired to this package until an explicit feature gate is
// enabled by a later change. Config's zero value is therefore disabled.
package catalogwriter

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
	"github.com/google/uuid"
)

// Table is intentionally closed over the two catalog tables. Keeping this
// type out of chwriter prevents catalog rows from accidentally entering the
// pinned spans insert/dead-letter path.
type Table string

const (
	KeyTable   Table = "span_attribute_key_catalog"
	ValueTable Table = "span_attribute_value_catalog"
)

const dateTime64Layout = "2006-01-02 15:04:05.000000"

// Inserter is the only transport contract used by this package. Production
// wiring can adapt a catalog-specific ClickHouse client later; tests need no
// network and no span-writer stats/dead-letter implementation.
type Inserter interface {
	InsertCatalog(context.Context, Table, []map[string]any) error
}

// ProgressRecord is delivered only after both catalog tables acknowledge all
// chunks. Sink implementations must be idempotent by EnvelopeID: a crash after
// sink acknowledgement but before spool deletion replays the same record.
type ProgressRecord struct {
	EnvelopeID string
	CreatedAt  time.Time
	Metadata   JobMetadata
}

// ProgressSink durably acknowledges project-scoped progress and gaps before a
// spool envelope may be deleted. The package never derives coverage itself.
type ProgressSink interface {
	AcknowledgeCatalogProgress(context.Context, ProgressRecord) error
}

// Config contains both per-span builder bounds and whole-drain bounds.
// Enabled defaults to false. All positive limits must be supplied before the
// writer can be enabled, making an accidental partial activation fail closed.
type Config struct {
	Enabled              bool
	CatalogEpoch         uint16
	BuildLimits          attributecatalog.BuildLimits
	MaxJobRows           int
	MaxJobEncodedBytes   int
	MaxJobInputSpans     int
	MaxJobProjects       int
	MaxJobMetadataBytes  int
	MaxChunkRows         int
	MaxChunkEncodedBytes int
	MaxSpoolBytes        int64
	MaxSpoolFiles        int
	SpoolDir             string
	ProgressSink         ProgressSink
}

// DefaultConfig returns conservative bounds but remains disabled. A future
// integration must still opt in explicitly and choose its spool location.
func DefaultConfig() Config {
	return Config{
		Enabled:      false,
		CatalogEpoch: 1,
		BuildLimits: attributecatalog.BuildLimits{
			MaxKeys:         128,
			MaxArrayMembers: 256,
			MaxEncodedBytes: 64 << 10,
		},
		MaxJobRows:           20_000,
		MaxJobEncodedBytes:   8 << 20,
		MaxJobInputSpans:     20_000,
		MaxJobProjects:       1_024,
		MaxJobMetadataBytes:  1 << 20,
		MaxChunkRows:         2_000,
		MaxChunkEncodedBytes: 1 << 20,
		MaxSpoolBytes:        512 << 20,
		MaxSpoolFiles:        10_000,
	}
}

// Writer owns a catalog-only spool. admission serializes only short local
// filesystem/accounting operations; replayMu serializes workers independently,
// so a blocked catalog insert can never hold admission or delay Submit.
type Writer struct {
	cfg        Config
	inserter   Inserter
	spool      spool
	admission  chan struct{}
	replayMu   sync.Mutex
	spoolBytes int64 // guarded by admission
	spoolFiles int   // guarded by admission
	progress   ProgressSink
}

// New constructs a disabled no-op writer for zero Config. Enabling validates
// every bound and creates only the dedicated catalog spool directory.
func New(cfg Config, inserter Inserter) (*Writer, error) {
	return newWriter(cfg, inserter, cfg.ProgressSink, true)
}

// NewTransportWriter constructs a writer whose durable spool is drained only
// through ReplayTo. It intentionally has no Inserter or ProgressSink, so Kafka
// and other transports do not need dummy direct-delivery dependencies.
func NewTransportWriter(cfg Config) (*Writer, error) {
	cfg.ProgressSink = nil
	return newWriter(cfg, nil, nil, false)
}

func newWriter(cfg Config, inserter Inserter, progress ProgressSink, requireDirect bool) (*Writer, error) {
	w := &Writer{
		cfg: cfg, inserter: inserter, progress: progress,
		spool:     spool{dir: cfg.SpoolDir},
		admission: make(chan struct{}, 1),
	}
	w.admission <- struct{}{}
	if !cfg.Enabled {
		return w, nil
	}
	if requireDirect && inserter == nil {
		return nil, errors.New("catalogwriter: enabled writer requires an Inserter")
	}
	if requireDirect && progress == nil {
		return nil, errors.New("catalogwriter: enabled writer requires a progress sink")
	}
	if cfg.CatalogEpoch == 0 {
		return nil, errors.New("catalogwriter: enabled writer requires a non-zero catalog epoch")
	}
	if cfg.BuildLimits.MaxKeys < 0 || cfg.BuildLimits.MaxArrayMembers < 0 || cfg.BuildLimits.MaxEncodedBytes < 0 {
		return nil, errors.New("catalogwriter: build limits must be non-negative")
	}
	if cfg.MaxJobRows <= 0 || cfg.MaxJobEncodedBytes <= 0 || cfg.MaxJobInputSpans <= 0 ||
		cfg.MaxJobProjects <= 0 || cfg.MaxJobMetadataBytes <= 0 || cfg.MaxChunkRows <= 0 ||
		cfg.MaxChunkEncodedBytes <= 0 || cfg.MaxSpoolBytes <= 0 || cfg.MaxSpoolFiles <= 0 {
		return nil, errors.New("catalogwriter: enabled writer requires positive job, chunk, and spool limits")
	}
	if cfg.SpoolDir == "" {
		return nil, errors.New("catalogwriter: enabled writer requires a dedicated spool directory")
	}
	if err := w.spool.prepare(); err != nil {
		return nil, err
	}
	bytesUsed, filesUsed, err := w.spool.usage(cfg.MaxSpoolFiles)
	if err != nil {
		return nil, err
	}
	if bytesUsed > cfg.MaxSpoolBytes {
		return nil, fmt.Errorf("catalogwriter: existing spool uses %d bytes, limit %d", bytesUsed, cfg.MaxSpoolBytes)
	}
	w.spoolBytes = bytesUsed
	w.spoolFiles = filesUsed
	return w, nil
}

// Enabled reports whether catalog staging and spooling are explicitly active.
func (w *Writer) Enabled() bool { return w != nil && w.cfg.Enabled }

// StageReport describes only local staging outcomes. It is not coverage state:
// in particular, this package never emits a positive coverage watermark.
type StageReport struct {
	InputSpans            int
	AcceptedSpans         int
	RejectedSpans         int
	UnscopedRejectedSpans int
	IncompleteSpans       int
	KeyRows               int
	ValueRows             int
	DuplicateRows         int
	RowsOmitted           int
	EncodedBytes          int
	GlobalTruncated       bool
	BuildGapReasons       []string
	UnscopedGapReasons    []string
	Projects              []ProjectJobMetadata
}

// ProjectJobMetadata makes mixed-project drains safe to reason about without
// inferring scope from emitted rows. A scoped rejection or empty-attribute span
// still appears here. Untrusted project/time rows are counted separately on
// JobMetadata and must block advancement for the whole source drain.
type ProjectJobMetadata struct {
	ProjectID       string   `json:"project_id"`
	InputSpans      int      `json:"input_spans"`
	AcceptedSpans   int      `json:"accepted_spans"`
	RejectedSpans   int      `json:"rejected_spans"`
	IncompleteSpans int      `json:"incomplete_spans"`
	KeyRows         int      `json:"key_rows"`
	ValueRows       int      `json:"value_rows"`
	DuplicateRows   int      `json:"duplicate_rows"`
	RowsOmitted     int      `json:"rows_omitted"`
	GapReasons      []string `json:"gap_reasons"`
	MinSpanStart    string   `json:"min_span_start"`
	MaxSpanStart    string   `json:"max_span_start"`
}

// JobMetadata is the durable, immutable progress/gap record carried with each
// staged job. It is deliberately descriptive rather than a positive coverage
// watermark: a later checkpoint writer must treat any rejected/incomplete/
// omitted count or gap reason as an unresolved coverage gap.
type JobMetadata struct {
	CatalogEpoch          uint16               `json:"catalog_epoch"`
	InputSpans            int                  `json:"input_spans"`
	OverflowSpans         int                  `json:"overflow_spans"`
	AcceptedSpans         int                  `json:"accepted_spans"`
	RejectedSpans         int                  `json:"rejected_spans"`
	UnscopedRejectedSpans int                  `json:"unscoped_rejected_spans"`
	IncompleteSpans       int                  `json:"incomplete_spans"`
	KeyRows               int                  `json:"key_rows"`
	ValueRows             int                  `json:"value_rows"`
	DuplicateRows         int                  `json:"duplicate_rows"`
	RowsOmitted           int                  `json:"rows_omitted"`
	EncodedBytes          int                  `json:"encoded_bytes"`
	GlobalTruncated       bool                 `json:"global_truncated"`
	GapReasons            []string             `json:"gap_reasons"`
	UnscopedGapReasons    []string             `json:"unscoped_gap_reasons"`
	MinSpanStart          string               `json:"min_span_start"`
	MaxSpanStart          string               `json:"max_span_start"`
	Projects              []ProjectJobMetadata `json:"projects"`
}

// Job is an opaque, compact drain unit. Its slices and row types are private so
// callers cannot append canonical span fields or invalidate size accounting.
// The supported lifecycle is StageCanonicalSpans -> Submit -> worker Replay.
type Job struct {
	keyRows      []keyRow
	valueRows    []valueRow
	encodedBytes int
	metadata     JobMetadata
}

// Empty reports whether staging retained no catalog rows.
func (j Job) Empty() bool { return len(j.keyRows) == 0 && len(j.valueRows) == 0 }

// RowCount returns the total compact rows in the job.
func (j Job) RowCount() int { return len(j.keyRows) + len(j.valueRows) }

// EncodedBytes is the exact JSONEachRow byte count, including one newline per
// row, used to enforce the global job limit.
func (j Job) EncodedBytes() int { return j.encodedBytes }

// Metadata returns a defensive copy of the durable staging record.
func (j Job) Metadata() JobMetadata { return cloneJobMetadata(j.metadata) }

// SubmissionGapError means a staged batch could not be proven durable. The
// attempted metadata remains available to monitoring/coverage coordination
// even when admission, capacity, or fsync fails.
type SubmissionGapError struct {
	Operation string
	Metadata  JobMetadata
	Err       error
}

func (e *SubmissionGapError) Error() string {
	return fmt.Sprintf("catalogwriter: %s left an unresolved durable gap: %v", e.Operation, e.Err)
}

func (e *SubmissionGapError) Unwrap() error { return e.Err }

type keyRow struct {
	ProjectID string `json:"project_id"`
	// SourceKind is omitted only when decoding/replaying a pre-projection-v2
	// durable spool entry. New staging always writes it explicitly. Keeping the
	// legacy JSON shape byte-identical lets spool v2 checksum and encoded-byte
	// validation remain valid across a rolling upgrade.
	SourceKind    string `json:"source_kind,omitempty"`
	AttributeKey  string `json:"attribute_key"`
	KeyFolded     string `json:"key_folded"`
	AttributeType string `json:"attribute_type"`
	FirstSeen     string `json:"first_seen"`
	LastSeen      string `json:"last_seen"`
	CatalogEpoch  uint16 `json:"catalog_epoch"`
}

type valueRow struct {
	ProjectID        string `json:"project_id"`
	SourceKind       string `json:"source_kind,omitempty"`
	AttributeKey     string `json:"attribute_key"`
	AttributeType    string `json:"attribute_type"`
	ValueFingerprint string `json:"value_fingerprint"`
	ValueJSON        string `json:"value_json"`
	ValueSearchText  string `json:"value_search_text"`
	FirstSeen        string `json:"first_seen"`
	LastSeen         string `json:"last_seen"`
	CatalogEpoch     uint16 `json:"catalog_epoch"`
}

type keyIdentity struct {
	projectID, sourceKind, attributeKey, attributeType string
	epoch                                              uint16
}

type valueIdentity struct {
	projectID, sourceKind, attributeKey, attributeType, fingerprint string
	epoch                                                           uint16
}

// StageCanonicalSpans synchronously extracts only the four typed attribute
// maps plus project/time scope from canonical span rows. All retained strings
// are cloned, so the returned Job cannot keep a large source row/backing buffer
// alive. Invalid spans are skipped and counted; one bad row never poisons the
// rest of a canonical drain.
func (w *Writer) StageCanonicalSpans(rows []map[string]any) (Job, StageReport) {
	report := StageReport{InputSpans: len(rows)}
	if w == nil || !w.cfg.Enabled || len(rows) == 0 {
		return Job{}, report
	}
	if len(rows) > w.cfg.MaxJobInputSpans {
		return w.unscopedGapJob(len(rows), "input_span_limit")
	}

	job := Job{
		keyRows:   make([]keyRow, 0, min(w.cfg.MaxJobRows, len(rows)*4)),
		valueRows: make([]valueRow, 0, min(w.cfg.MaxJobRows, len(rows)*4)),
	}
	keys := make(map[keyIdentity]int, min(w.cfg.MaxJobRows, len(rows)*4))
	values := make(map[valueIdentity]int, min(w.cfg.MaxJobRows, len(rows)*4))
	gaps := make(map[string]struct{})
	unscopedGaps := make(map[string]struct{})
	projects := make(map[string]*projectStageProgress, min(len(rows), 64))

	for _, canonical := range rows {
		scope, ok := extractCanonicalScope(canonical, w.cfg.CatalogEpoch)
		if !ok {
			report.RejectedSpans++
			report.UnscopedRejectedSpans++
			gaps["unscoped_rejection"] = struct{}{}
			unscopedGaps["unscoped_rejection"] = struct{}{}
			continue
		}
		if _, exists := projects[scope.ProjectID]; !exists && len(projects) >= w.cfg.MaxJobProjects {
			report.RejectedSpans++
			report.UnscopedRejectedSpans++
			gaps["project_metadata_limit"] = struct{}{}
			unscopedGaps["project_metadata_limit"] = struct{}{}
			continue
		}
		progress := projectProgress(projects, scope)
		progress.metadata.InputSpans++
		mergeProgressTime(&progress.metadata, scope.SeenAt)
		attrs, ok := extractCanonicalAttributes(canonical)
		if !ok {
			report.RejectedSpans++
			progress.metadata.RejectedSpans++
			progress.gaps["invalid_canonical_attributes"] = struct{}{}
			gaps["invalid_canonical_attributes"] = struct{}{}
			continue
		}
		systemAttrs, systemProjectionComplete, ok := extractCanonicalSystemAttributes(canonical)
		if !ok {
			report.RejectedSpans++
			progress.metadata.RejectedSpans++
			progress.gaps["invalid_canonical_system_attributes"] = struct{}{}
			gaps["invalid_canonical_system_attributes"] = struct{}{}
			continue
		}
		report.AcceptedSpans++
		progress.metadata.AcceptedSpans++
		built, err := attributecatalog.BuildRows(scope, attrs, w.cfg.BuildLimits)
		if err != nil {
			// Config validation makes this unreachable for ordinary inputs, but
			// retaining fail-closed behavior is safer than accepting partial data.
			report.RejectedSpans++
			report.AcceptedSpans--
			progress.metadata.RejectedSpans++
			progress.metadata.AcceptedSpans--
			progress.gaps["builder_error"] = struct{}{}
			gaps["builder_error"] = struct{}{}
			continue
		}
		if len(systemAttrs.Strings) != 0 {
			systemBuilt, systemErr := attributecatalog.BuildRowsForSource(
				scope, systemAttrs, w.cfg.BuildLimits,
				attributecatalog.SourceKindSystemAttribute,
			)
			if systemErr != nil {
				report.RejectedSpans++
				report.AcceptedSpans--
				progress.metadata.RejectedSpans++
				progress.metadata.AcceptedSpans--
				progress.gaps["system_builder_error"] = struct{}{}
				gaps["system_builder_error"] = struct{}{}
				continue
			}
			built.KeyRows = append(built.KeyRows, systemBuilt.KeyRows...)
			built.ValueRows = append(built.ValueRows, systemBuilt.ValueRows...)
			if !systemBuilt.Metadata.Complete {
				built.Metadata.Complete = false
				built.Metadata.GapReasons = sortedUnion(
					built.Metadata.GapReasons, systemBuilt.Metadata.GapReasons,
				)
			}
		}
		if !systemProjectionComplete {
			built.Metadata.Complete = false
			built.Metadata.GapReasons = sortedUnion(
				built.Metadata.GapReasons, []string{"system_value_projection"},
			)
		}
		if !built.Metadata.Complete {
			report.IncompleteSpans++
			progress.metadata.IncompleteSpans++
			for _, reason := range built.Metadata.GapReasons {
				gaps[reason] = struct{}{}
				progress.gaps[reason] = struct{}{}
			}
		}

		for _, source := range built.KeyRows {
			compact := compactKeyRow(source)
			identity := keyIdentity{
				projectID: compact.ProjectID, sourceKind: compact.SourceKind, attributeKey: compact.AttributeKey,
				attributeType: compact.AttributeType, epoch: compact.CatalogEpoch,
			}
			if index, duplicate := keys[identity]; duplicate {
				mergeSeen(&job.keyRows[index].FirstSeen, &job.keyRows[index].LastSeen, compact.FirstSeen)
				report.DuplicateRows++
				progress.metadata.DuplicateRows++
				continue
			}
			rowBytes, err := wireSize(compact)
			if err != nil || !w.rowFits(job, rowBytes) {
				report.RowsOmitted++
				progress.metadata.RowsOmitted++
				report.GlobalTruncated = true
				progress.gaps["writer_global_truncation"] = struct{}{}
				gaps["writer_global_truncation"] = struct{}{}
				continue
			}
			keys[identity] = len(job.keyRows)
			job.keyRows = append(job.keyRows, compact)
			job.encodedBytes += rowBytes
			progress.metadata.KeyRows++
		}

		for _, source := range built.ValueRows {
			compact := compactValueRow(source)
			key := keyIdentity{
				projectID: compact.ProjectID, sourceKind: compact.SourceKind, attributeKey: compact.AttributeKey,
				attributeType: compact.AttributeType, epoch: compact.CatalogEpoch,
			}
			if _, keyRetained := keys[key]; !keyRetained {
				// Never publish an orphan value when the corresponding key row was
				// rejected by a tighter writer-wide row/byte ceiling. A prior span's
				// retained duplicate key still satisfies this invariant.
				report.RowsOmitted++
				progress.metadata.RowsOmitted++
				report.GlobalTruncated = true
				progress.gaps["writer_global_truncation"] = struct{}{}
				gaps["writer_global_truncation"] = struct{}{}
				continue
			}
			identity := valueIdentity{
				projectID: compact.ProjectID, sourceKind: compact.SourceKind, attributeKey: compact.AttributeKey,
				attributeType: compact.AttributeType,
				fingerprint:   compact.ValueFingerprint, epoch: compact.CatalogEpoch,
			}
			if index, duplicate := values[identity]; duplicate {
				mergeSeen(&job.valueRows[index].FirstSeen, &job.valueRows[index].LastSeen, compact.FirstSeen)
				report.DuplicateRows++
				progress.metadata.DuplicateRows++
				continue
			}
			rowBytes, err := wireSize(compact)
			if err != nil || !w.rowFits(job, rowBytes) {
				report.RowsOmitted++
				progress.metadata.RowsOmitted++
				report.GlobalTruncated = true
				progress.gaps["writer_global_truncation"] = struct{}{}
				gaps["writer_global_truncation"] = struct{}{}
				continue
			}
			values[identity] = len(job.valueRows)
			job.valueRows = append(job.valueRows, compact)
			job.encodedBytes += rowBytes
			progress.metadata.ValueRows++
		}
	}

	sort.Slice(job.keyRows, func(i, j int) bool { return lessKeyRow(job.keyRows[i], job.keyRows[j]) })
	sort.Slice(job.valueRows, func(i, j int) bool { return lessValueRow(job.valueRows[i], job.valueRows[j]) })
	report.KeyRows = len(job.keyRows)
	report.ValueRows = len(job.valueRows)
	report.EncodedBytes = job.encodedBytes
	report.BuildGapReasons = sortedSet(gaps)
	report.UnscopedGapReasons = sortedSet(unscopedGaps)
	report.Projects = freezeProjectProgress(projects)
	job.metadata = metadataFromReport(report, w.cfg.CatalogEpoch)
	if encoded, err := json.Marshal(job.metadata); err != nil || len(encoded) > w.cfg.MaxJobMetadataBytes {
		return w.unscopedGapJob(len(rows), "metadata_byte_limit")
	}
	return job, report
}

func (w *Writer) unscopedGapJob(totalInputSpans int, reason string) (Job, StageReport) {
	report := StageReport{
		InputSpans: totalInputSpans, RejectedSpans: totalInputSpans,
		UnscopedRejectedSpans: totalInputSpans,
		BuildGapReasons:       []string{reason}, UnscopedGapReasons: []string{reason},
	}
	represented := min(totalInputSpans, w.cfg.MaxJobInputSpans)
	durableReport := report
	durableReport.InputSpans = represented
	durableReport.RejectedSpans = represented
	durableReport.UnscopedRejectedSpans = represented
	metadata := metadataFromReport(durableReport, w.cfg.CatalogEpoch)
	metadata.OverflowSpans = totalInputSpans - represented
	job := Job{metadata: metadata}
	return job, report
}

type projectStageProgress struct {
	metadata ProjectJobMetadata
	gaps     map[string]struct{}
}

func projectProgress(
	projects map[string]*projectStageProgress, scope attributecatalog.Scope,
) *projectStageProgress {
	progress := projects[scope.ProjectID]
	if progress == nil {
		progress = &projectStageProgress{
			metadata: ProjectJobMetadata{ProjectID: strings.Clone(scope.ProjectID)},
			gaps:     make(map[string]struct{}),
		}
		projects[scope.ProjectID] = progress
	}
	return progress
}

func mergeProgressTime(metadata *ProjectJobMetadata, seenAt time.Time) {
	seen := seenAt.UTC().Format(dateTime64Layout)
	if metadata.MinSpanStart == "" || seen < metadata.MinSpanStart {
		metadata.MinSpanStart = seen
	}
	if metadata.MaxSpanStart == "" || seen > metadata.MaxSpanStart {
		metadata.MaxSpanStart = seen
	}
}

func freezeProjectProgress(projects map[string]*projectStageProgress) []ProjectJobMetadata {
	frozen := make([]ProjectJobMetadata, 0, len(projects))
	for _, progress := range projects {
		progress.metadata.GapReasons = sortedSet(progress.gaps)
		frozen = append(frozen, cloneProjectMetadata(progress.metadata))
	}
	sort.Slice(frozen, func(i, j int) bool { return frozen[i].ProjectID < frozen[j].ProjectID })
	return frozen
}

func metadataFromReport(report StageReport, catalogEpoch uint16) JobMetadata {
	minSpanStart, maxSpanStart := projectTimeBounds(report.Projects)
	return JobMetadata{
		CatalogEpoch: catalogEpoch,
		InputSpans:   report.InputSpans, AcceptedSpans: report.AcceptedSpans,
		RejectedSpans:         report.RejectedSpans,
		UnscopedRejectedSpans: report.UnscopedRejectedSpans,
		IncompleteSpans:       report.IncompleteSpans,
		KeyRows:               report.KeyRows, ValueRows: report.ValueRows,
		DuplicateRows: report.DuplicateRows, RowsOmitted: report.RowsOmitted,
		EncodedBytes: report.EncodedBytes, GlobalTruncated: report.GlobalTruncated,
		GapReasons:         cloneStrings(report.BuildGapReasons),
		UnscopedGapReasons: cloneStrings(report.UnscopedGapReasons),
		MinSpanStart:       minSpanStart, MaxSpanStart: maxSpanStart,
		Projects: cloneProjectMetadataList(report.Projects),
	}
}

func projectTimeBounds(projects []ProjectJobMetadata) (string, string) {
	var minimum, maximum string
	for _, project := range projects {
		if minimum == "" || project.MinSpanStart < minimum {
			minimum = project.MinSpanStart
		}
		if maximum == "" || project.MaxSpanStart > maximum {
			maximum = project.MaxSpanStart
		}
	}
	return minimum, maximum
}

func cloneJobMetadata(source JobMetadata) JobMetadata {
	cloned := source
	cloned.GapReasons = cloneStrings(source.GapReasons)
	cloned.UnscopedGapReasons = cloneStrings(source.UnscopedGapReasons)
	cloned.Projects = cloneProjectMetadataList(source.Projects)
	return cloned
}

func cloneProjectMetadata(source ProjectJobMetadata) ProjectJobMetadata {
	cloned := source
	cloned.GapReasons = cloneStrings(source.GapReasons)
	return cloned
}

func cloneProjectMetadataList(source []ProjectJobMetadata) []ProjectJobMetadata {
	if len(source) == 0 {
		return nil
	}
	cloned := make([]ProjectJobMetadata, len(source))
	for index := range source {
		cloned[index] = cloneProjectMetadata(source[index])
	}
	return cloned
}

func cloneStrings(source []string) []string {
	if len(source) == 0 {
		return nil
	}
	return append([]string(nil), source...)
}

func (w *Writer) rowFits(job Job, rowBytes int) bool {
	if rowBytes <= 0 || rowBytes > w.cfg.MaxChunkEncodedBytes {
		return false
	}
	if job.RowCount() >= w.cfg.MaxJobRows {
		return false
	}
	return rowBytes <= w.cfg.MaxJobEncodedBytes-job.encodedBytes
}

func extractCanonicalScope(row map[string]any, epoch uint16) (attributecatalog.Scope, bool) {
	projectID, ok := row["project_id"].(string)
	if !ok {
		return attributecatalog.Scope{}, false
	}
	parsedProject, err := uuid.Parse(projectID)
	if err != nil || parsedProject == uuid.Nil || parsedProject.String() != projectID {
		return attributecatalog.Scope{}, false
	}
	seenAt, ok := parseCanonicalTime(row["start_time"])
	if !ok {
		return attributecatalog.Scope{}, false
	}
	return attributecatalog.Scope{
		ProjectID: strings.Clone(parsedProject.String()), SeenAt: seenAt, CatalogEpoch: epoch,
	}, true
}

func extractCanonicalAttributes(row map[string]any) (attributecatalog.SpanAttributeMaps, bool) {
	stringsMap, ok := typedMap[string](row, "attrs_string")
	if !ok {
		return attributecatalog.SpanAttributeMaps{}, false
	}
	numbersMap, ok := typedMap[float64](row, "attrs_number")
	if !ok {
		return attributecatalog.SpanAttributeMaps{}, false
	}
	booleansMap, ok := typedMap[uint8](row, "attrs_bool")
	if !ok {
		return attributecatalog.SpanAttributeMaps{}, false
	}
	extraMap, ok := typedMap[any](row, "attributes_extra")
	if !ok {
		return attributecatalog.SpanAttributeMaps{}, false
	}
	return attributecatalog.SpanAttributeMaps{
		Strings: stringsMap, Numbers: numbersMap, Booleans: booleansMap, Extra: extraMap,
	}, true
}

// extractCanonicalSystemAttributes projects only hot columns whose public
// filter semantics are byte-identical to the canonical span row. Model is the
// first projection-v2 field. Derived voice and dictionary-backed fields are
// deliberately excluded until their final public values can be injected.
const catalogSystemStringSuggestionMaxUTF8Bytes = 16 << 10

const catalogSystemNilUUIDValue = "00000000-0000-0000-0000-000000000000"

func extractCanonicalSystemAttributes(
	row map[string]any,
) (attributecatalog.SpanAttributeMaps, bool, bool) {
	raw, present := row["model"]
	if !present || raw == nil {
		return attributecatalog.SpanAttributeMaps{}, true, true
	}
	model, ok := raw.(string)
	if !ok {
		return attributecatalog.SpanAttributeMaps{}, false, false
	}
	if model == "" || model == catalogSystemNilUUIDValue {
		return attributecatalog.SpanAttributeMaps{}, true, true
	}
	// Go's len(string) and ClickHouse length(String) both count UTF-8 bytes.
	// Apply the same public-suggestion cap in live staging and backfill so an
	// oversized Model cannot be present only on one ingestion path.
	if len(model) > catalogSystemStringSuggestionMaxUTF8Bytes {
		return attributecatalog.SpanAttributeMaps{}, false, true
	}
	return attributecatalog.SpanAttributeMaps{Strings: map[string]string{"model": model}}, true, true
}

// typedMap treats a missing or explicit nil field as an empty typed map. A
// present map with the wrong canonical Go type is rejected rather than coerced.
func typedMap[T any](row map[string]any, key string) (map[string]T, bool) {
	raw, present := row[key]
	if !present || raw == nil {
		return nil, true
	}
	typed, ok := raw.(map[string]T)
	return typed, ok
}

func parseCanonicalTime(raw any) (time.Time, bool) {
	switch typed := raw.(type) {
	case string:
		if len(typed) != len(dateTime64Layout) {
			return time.Time{}, false
		}
		parsed, err := time.ParseInLocation(dateTime64Layout, typed, time.UTC)
		return parsed, err == nil
	case time.Time:
		if typed.IsZero() {
			return time.Time{}, false
		}
		return typed.UTC().Truncate(time.Microsecond), true
	default:
		return time.Time{}, false
	}
}

func compactKeyRow(source attributecatalog.KeyRow) keyRow {
	seen := source.FirstSeen.UTC().Format(dateTime64Layout)
	return keyRow{
		ProjectID: strings.Clone(source.ProjectID), SourceKind: strings.Clone(source.SourceKind),
		AttributeKey: strings.Clone(source.AttributeKey),
		KeyFolded:    strings.Clone(source.KeyFolded), AttributeType: strings.Clone(source.AttributeType),
		FirstSeen: seen, LastSeen: seen, CatalogEpoch: source.CatalogEpoch,
	}
}

func compactValueRow(source attributecatalog.ValueRow) valueRow {
	seen := source.FirstSeen.UTC().Format(dateTime64Layout)
	return valueRow{
		ProjectID: strings.Clone(source.ProjectID), SourceKind: strings.Clone(source.SourceKind),
		AttributeKey:     strings.Clone(source.AttributeKey),
		AttributeType:    strings.Clone(source.AttributeType),
		ValueFingerprint: strings.Clone(source.ValueFingerprint), ValueJSON: strings.Clone(source.ValueJSON),
		ValueSearchText: strings.Clone(source.ValueSearchText),
		FirstSeen:       seen, LastSeen: seen, CatalogEpoch: source.CatalogEpoch,
	}
}

func mergeSeen(first, last *string, seen string) {
	if seen < *first {
		*first = seen
	}
	if seen > *last {
		*last = seen
	}
}

func wireSize(row any) (int, error) {
	encoded, err := json.Marshal(row)
	if err != nil {
		return 0, err
	}
	return len(encoded) + 1, nil
}

func lessKeyRow(left, right keyRow) bool {
	if left.ProjectID != right.ProjectID {
		return left.ProjectID < right.ProjectID
	}
	if left.CatalogEpoch != right.CatalogEpoch {
		return left.CatalogEpoch < right.CatalogEpoch
	}
	if left.SourceKind != right.SourceKind {
		return left.SourceKind < right.SourceKind
	}
	if left.KeyFolded != right.KeyFolded {
		return left.KeyFolded < right.KeyFolded
	}
	if left.AttributeKey != right.AttributeKey {
		return left.AttributeKey < right.AttributeKey
	}
	return left.AttributeType < right.AttributeType
}

func lessValueRow(left, right valueRow) bool {
	if left.ProjectID != right.ProjectID {
		return left.ProjectID < right.ProjectID
	}
	if left.CatalogEpoch != right.CatalogEpoch {
		return left.CatalogEpoch < right.CatalogEpoch
	}
	if left.SourceKind != right.SourceKind {
		return left.SourceKind < right.SourceKind
	}
	if left.AttributeKey != right.AttributeKey {
		return left.AttributeKey < right.AttributeKey
	}
	if left.AttributeType != right.AttributeType {
		return left.AttributeType < right.AttributeType
	}
	return left.ValueFingerprint < right.ValueFingerprint
}

func sortedSet(values map[string]struct{}) []string {
	if len(values) == 0 {
		return nil
	}
	out := make([]string, 0, len(values))
	for value := range values {
		out = append(out, value)
	}
	sort.Strings(out)
	return out
}

func sortedUnion(left, right []string) []string {
	values := make(map[string]struct{}, len(left)+len(right))
	for _, value := range left {
		values[value] = struct{}{}
	}
	for _, value := range right {
		values[value] = struct{}{}
	}
	return sortedSet(values)
}

// Submit durably spools a compact job and returns without making a network
// request, so a catalog-network outage cannot hold admission behind Replay.
// Local encoding and fsync are intentionally synchronous and can add disk
// latency; runtime wiring remains disabled until that latency is qualified or
// moved behind a bounded actor. A separate worker calls Replay. If the total
// spool ceiling is reached, Submit returns an explicit metadata-carrying error
// and preserves every existing envelope.
func (w *Writer) Submit(ctx context.Context, job Job) error {
	if w == nil || !w.cfg.Enabled || (job.Empty() && job.metadata.InputSpans == 0) {
		return nil
	}
	if err := w.validateJob(job); err != nil {
		return submissionGap("validate staged job", job, err)
	}
	if err := w.acquireAdmission(ctx); err != nil {
		return submissionGap("acquire spool admission", job, err)
	}
	defer w.releaseAdmission()
	if w.spoolFiles >= w.cfg.MaxSpoolFiles {
		return submissionGap("admit spool file", job, fmt.Errorf(
			"spool file limit reached: used %d limit %d", w.spoolFiles, w.cfg.MaxSpoolFiles,
		))
	}
	pending, err := w.spool.save(job, w.cfg.MaxSpoolBytes-w.spoolBytes)
	// save returns a populated pendingFile once rename publishes the durable
	// name, including when the following directory fsync reports an error.
	// Count that file conservatively so a sync error cannot bypass either cap.
	if pending.name != "" {
		w.spoolFiles++
		w.spoolBytes += pending.size
	}
	if err != nil {
		return submissionGap("persist spool envelope", job, err)
	}
	return nil
}

func submissionGap(operation string, job Job, err error) error {
	return &SubmissionGapError{
		Operation: operation, Metadata: job.Metadata(), Err: err,
	}
}

// Pending describes a validated catalog spool envelope without exposing its
// path or mutable rows.
type Pending struct {
	ID           string
	CreatedAt    time.Time
	Rows         int
	EncodedBytes int
	Metadata     JobMetadata
}

// Pending returns catalog envelopes in stable oldest/name order. Temporary
// atomic-write files and unrelated files are ignored.
func (w *Writer) Pending() ([]Pending, error) {
	if w == nil || !w.cfg.Enabled {
		return nil, nil
	}
	w.replayMu.Lock()
	defer w.replayMu.Unlock()
	if err := w.acquireAdmission(context.Background()); err != nil {
		return nil, err
	}
	files, err := w.spool.enumerate(w.cfg.MaxSpoolFiles)
	w.releaseAdmission()
	if err != nil {
		return nil, err
	}
	out := make([]Pending, 0, len(files))
	for _, file := range files {
		envelope, err := w.spool.load(file, w.maxEnvelopeBytes())
		if err != nil {
			return nil, err
		}
		if err := w.validateJob(envelope.Job); err != nil {
			return nil, fmt.Errorf("catalogwriter: invalid pending job %s: %w", envelope.ID, err)
		}
		out = append(out, Pending{
			ID: envelope.ID, CreatedAt: envelope.CreatedAt,
			Rows: envelope.Job.RowCount(), EncodedBytes: envelope.Job.encodedBytes,
			Metadata: envelope.Job.Metadata(),
		})
	}
	return out, nil
}

// ReplayResult reports attempts only; it is intentionally not a catalog
// coverage/watermark signal. Quarantined envelopes remain durably retained
// under the same spool capacity bound for operator inspection.
type ReplayResult struct {
	Attempted   int
	Delivered   int
	Quarantined int
}

// Replay drains pending envelopes in deterministic order. It stops at the
// first failure, leaves that envelope (and all later ones) intact, and retries
// it on the next call. There is no terminal deletion path that could silently
// lose catalog work.
func (w *Writer) Replay(ctx context.Context) (ReplayResult, error) {
	if w == nil || !w.cfg.Enabled {
		return ReplayResult{}, nil
	}
	if w.inserter == nil || w.progress == nil {
		return ReplayResult{}, errors.New("catalogwriter: direct Replay unavailable on a transport writer; use ReplayTo")
	}
	w.replayMu.Lock()
	defer w.replayMu.Unlock()
	if err := w.acquireAdmission(ctx); err != nil {
		return ReplayResult{}, err
	}
	files, err := w.spool.enumerate(w.cfg.MaxSpoolFiles)
	w.releaseAdmission()
	if err != nil {
		return ReplayResult{}, err
	}
	result := ReplayResult{}
	for _, file := range files {
		result.Attempted++
		if err := w.drainPending(ctx, file); err != nil {
			return result, err
		}
		result.Delivered++
	}
	return result, nil
}

func (w *Writer) drainPending(ctx context.Context, pending pendingFile) error {
	envelope, err := w.spool.load(pending, w.maxEnvelopeBytes())
	if err != nil {
		return err
	}
	if err := w.validateJob(envelope.Job); err != nil {
		return fmt.Errorf("catalogwriter: invalid pending job %s: %w", envelope.ID, err)
	}
	if err := w.insertJob(ctx, envelope.Job); err != nil {
		return fmt.Errorf("catalogwriter: drain %s: %w", envelope.ID, err)
	}
	if err := w.progress.AcknowledgeCatalogProgress(ctx, ProgressRecord{
		EnvelopeID: envelope.ID, CreatedAt: envelope.CreatedAt,
		Metadata: envelope.Job.Metadata(),
	}); err != nil {
		return fmt.Errorf("catalogwriter: acknowledge progress %s: %w", envelope.ID, err)
	}
	if err := w.acquireAdmission(ctx); err != nil {
		return fmt.Errorf("catalogwriter: finalize delivered envelope %s: %w", envelope.ID, err)
	}
	removed, removeErr := w.spool.remove(pending)
	if removed {
		w.spoolFiles--
		w.spoolBytes -= pending.size
		if w.spoolFiles < 0 || w.spoolBytes < 0 {
			// This signals an internal accounting invariant breach without
			// allowing an undercount to admit unbounded future work. Fail closed
			// until a restart reconstructs exact counters from disk.
			w.spoolFiles = w.cfg.MaxSpoolFiles
			w.spoolBytes = w.cfg.MaxSpoolBytes
			removeErr = errors.Join(removeErr, errors.New("catalogwriter: spool accounting underflow"))
		}
	}
	w.releaseAdmission()
	if removeErr != nil {
		return fmt.Errorf("catalogwriter: remove delivered envelope %s: %w", envelope.ID, removeErr)
	}
	return nil
}

func (w *Writer) acquireAdmission(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-w.admission:
		return nil
	}
}

func (w *Writer) releaseAdmission() { w.admission <- struct{}{} }

func (w *Writer) insertJob(ctx context.Context, job Job) error {
	if err := w.insertKeyChunks(ctx, job.keyRows); err != nil {
		return err
	}
	return w.insertValueChunks(ctx, job.valueRows)
}

func (w *Writer) insertKeyChunks(ctx context.Context, rows []keyRow) error {
	for start := 0; start < len(rows); {
		end, mapped, err := keyChunk(rows, start, w.cfg.MaxChunkRows, w.cfg.MaxChunkEncodedBytes)
		if err != nil {
			return err
		}
		if err := w.inserter.InsertCatalog(ctx, KeyTable, mapped); err != nil {
			return err
		}
		start = end
	}
	return nil
}

func (w *Writer) insertValueChunks(ctx context.Context, rows []valueRow) error {
	for start := 0; start < len(rows); {
		end, mapped, err := valueChunk(rows, start, w.cfg.MaxChunkRows, w.cfg.MaxChunkEncodedBytes)
		if err != nil {
			return err
		}
		if err := w.inserter.InsertCatalog(ctx, ValueTable, mapped); err != nil {
			return err
		}
		start = end
	}
	return nil
}

func keyChunk(rows []keyRow, start, maxRows, maxBytes int) (int, []map[string]any, error) {
	out := make([]map[string]any, 0, min(maxRows, len(rows)-start))
	bytesUsed := 0
	end := start
	for end < len(rows) && len(out) < maxRows {
		size, err := wireSize(rows[end])
		if err != nil {
			return start, nil, err
		}
		if size > maxBytes-bytesUsed {
			break
		}
		out = append(out, keyRowMap(rows[end]))
		bytesUsed += size
		end++
	}
	if end == start {
		return start, nil, errors.New("catalogwriter: key row exceeds chunk limit")
	}
	return end, out, nil
}

func valueChunk(rows []valueRow, start, maxRows, maxBytes int) (int, []map[string]any, error) {
	out := make([]map[string]any, 0, min(maxRows, len(rows)-start))
	bytesUsed := 0
	end := start
	for end < len(rows) && len(out) < maxRows {
		size, err := wireSize(rows[end])
		if err != nil {
			return start, nil, err
		}
		if size > maxBytes-bytesUsed {
			break
		}
		out = append(out, valueRowMap(rows[end]))
		bytesUsed += size
		end++
	}
	if end == start {
		return start, nil, errors.New("catalogwriter: value row exceeds chunk limit")
	}
	return end, out, nil
}

func keyRowMap(row keyRow) map[string]any {
	out := map[string]any{
		"project_id": row.ProjectID, "attribute_key": row.AttributeKey,
		"key_folded": row.KeyFolded, "attribute_type": row.AttributeType,
		"first_seen": row.FirstSeen, "last_seen": row.LastSeen, "catalog_epoch": row.CatalogEpoch,
	}
	if row.SourceKind != "" {
		out["source_kind"] = row.SourceKind
	}
	return out
}

func valueRowMap(row valueRow) map[string]any {
	out := map[string]any{
		"project_id": row.ProjectID, "attribute_key": row.AttributeKey,
		"attribute_type": row.AttributeType, "value_fingerprint": row.ValueFingerprint,
		"value_json": row.ValueJSON, "value_search_text": row.ValueSearchText,
		"first_seen": row.FirstSeen, "last_seen": row.LastSeen, "catalog_epoch": row.CatalogEpoch,
	}
	if row.SourceKind != "" {
		out["source_kind"] = row.SourceKind
	}
	return out
}

func (w *Writer) validateJob(job Job) error {
	if job.metadata.InputSpans <= 0 {
		return errors.New("job has no staged input spans")
	}
	if job.RowCount() > w.cfg.MaxJobRows {
		return fmt.Errorf("job has %d rows, limit %d", job.RowCount(), w.cfg.MaxJobRows)
	}
	computed := 0
	for _, row := range job.keyRows {
		if row.CatalogEpoch != job.metadata.CatalogEpoch {
			return errors.New("key row/job metadata epoch mismatch")
		}
		if row.SourceKind != "" &&
			row.SourceKind != attributecatalog.SourceKindCustomAttribute &&
			row.SourceKind != attributecatalog.SourceKindSystemAttribute {
			return errors.New("key row has unsupported source kind")
		}
		size, err := wireSize(row)
		if err != nil {
			return err
		}
		if size > w.cfg.MaxChunkEncodedBytes {
			return errors.New("key row exceeds chunk byte limit")
		}
		computed += size
	}
	for _, row := range job.valueRows {
		if row.CatalogEpoch != job.metadata.CatalogEpoch {
			return errors.New("value row/job metadata epoch mismatch")
		}
		if row.SourceKind != "" &&
			row.SourceKind != attributecatalog.SourceKindCustomAttribute &&
			row.SourceKind != attributecatalog.SourceKindSystemAttribute {
			return errors.New("value row has unsupported source kind")
		}
		size, err := wireSize(row)
		if err != nil {
			return err
		}
		if size > w.cfg.MaxChunkEncodedBytes {
			return errors.New("value row exceeds chunk byte limit")
		}
		computed += size
	}
	if computed != job.encodedBytes {
		return fmt.Errorf("encoded byte count mismatch: stored %d computed %d", job.encodedBytes, computed)
	}
	if computed > w.cfg.MaxJobEncodedBytes {
		return fmt.Errorf("job has %d encoded bytes, limit %d", computed, w.cfg.MaxJobEncodedBytes)
	}
	metadata := job.metadata
	if metadata.CatalogEpoch == 0 || metadata.CatalogEpoch != w.cfg.CatalogEpoch ||
		metadata.OverflowSpans < 0 ||
		metadata.InputSpans != metadata.AcceptedSpans+metadata.RejectedSpans ||
		metadata.UnscopedRejectedSpans < 0 ||
		metadata.UnscopedRejectedSpans > metadata.RejectedSpans ||
		metadata.KeyRows != len(job.keyRows) || metadata.ValueRows != len(job.valueRows) ||
		metadata.EncodedBytes != computed || metadata.AcceptedSpans < 0 ||
		metadata.RejectedSpans < 0 || metadata.IncompleteSpans < 0 ||
		metadata.DuplicateRows < 0 || metadata.RowsOmitted < 0 ||
		metadata.IncompleteSpans > metadata.AcceptedSpans ||
		metadata.GlobalTruncated != (metadata.RowsOmitted > 0) {
		return errors.New("job metadata count invariant mismatch")
	}
	if !sort.StringsAreSorted(metadata.GapReasons) || hasDuplicateStrings(metadata.GapReasons) {
		return errors.New("job metadata gap reasons are not unique and sorted")
	}
	if !sort.StringsAreSorted(metadata.UnscopedGapReasons) ||
		hasDuplicateStrings(metadata.UnscopedGapReasons) {
		return errors.New("job metadata unscoped gap reasons are not unique and sorted")
	}
	encodedMetadata, err := json.Marshal(metadata)
	if err != nil {
		return fmt.Errorf("encode job metadata: %w", err)
	}
	if len(encodedMetadata) > w.cfg.MaxJobMetadataBytes {
		return fmt.Errorf("job metadata uses %d bytes, limit %d", len(encodedMetadata), w.cfg.MaxJobMetadataBytes)
	}
	if len(metadata.Projects) > w.cfg.MaxJobProjects || metadata.InputSpans > w.cfg.MaxJobInputSpans {
		return errors.New("job metadata exceeds input/project bounds")
	}
	if metadata.OverflowSpans > 0 && (len(metadata.Projects) != 0 ||
		metadata.InputSpans != metadata.UnscopedRejectedSpans ||
		!containsString(metadata.UnscopedGapReasons, "input_span_limit")) {
		return errors.New("job overflow metadata is not an unscoped input-limit gap")
	}
	if len(metadata.Projects) == 0 {
		if metadata.MinSpanStart != "" || metadata.MaxSpanStart != "" {
			return errors.New("job metadata has span bounds without scoped spans")
		}
	} else {
		minTime, minOK := parseCanonicalTime(metadata.MinSpanStart)
		maxTime, maxOK := parseCanonicalTime(metadata.MaxSpanStart)
		if !minOK || !maxOK || minTime.After(maxTime) {
			return errors.New("job metadata has invalid span bounds")
		}
	}
	if err := validateProjectMetadata(metadata); err != nil {
		return err
	}
	return nil
}

func validateProjectMetadata(metadata JobMetadata) error {
	var input, accepted, rejected, incomplete, keyRows, valueRows, duplicates, omitted int
	allGaps := make(map[string]struct{})
	previousProject := ""
	for _, project := range metadata.Projects {
		parsed, err := uuid.Parse(project.ProjectID)
		if err != nil || parsed == uuid.Nil || parsed.String() != project.ProjectID ||
			(previousProject != "" && project.ProjectID <= previousProject) {
			return errors.New("job project metadata identity/order mismatch")
		}
		previousProject = project.ProjectID
		if project.InputSpans <= 0 || project.InputSpans != project.AcceptedSpans+project.RejectedSpans ||
			project.AcceptedSpans < 0 || project.RejectedSpans < 0 ||
			project.IncompleteSpans < 0 || project.IncompleteSpans > project.AcceptedSpans ||
			project.KeyRows < 0 || project.ValueRows < 0 || project.DuplicateRows < 0 ||
			project.RowsOmitted < 0 || !sort.StringsAreSorted(project.GapReasons) ||
			hasDuplicateStrings(project.GapReasons) {
			return errors.New("job project metadata count/gap invariant mismatch")
		}
		minimum, minOK := parseCanonicalTime(project.MinSpanStart)
		maximum, maxOK := parseCanonicalTime(project.MaxSpanStart)
		if !minOK || !maxOK || minimum.After(maximum) {
			return errors.New("job project metadata has invalid span bounds")
		}
		input += project.InputSpans
		accepted += project.AcceptedSpans
		rejected += project.RejectedSpans
		incomplete += project.IncompleteSpans
		keyRows += project.KeyRows
		valueRows += project.ValueRows
		duplicates += project.DuplicateRows
		omitted += project.RowsOmitted
		for _, reason := range project.GapReasons {
			allGaps[reason] = struct{}{}
		}
	}
	for _, reason := range metadata.UnscopedGapReasons {
		allGaps[reason] = struct{}{}
	}
	if input+metadata.UnscopedRejectedSpans != metadata.InputSpans ||
		accepted != metadata.AcceptedSpans ||
		rejected+metadata.UnscopedRejectedSpans != metadata.RejectedSpans ||
		incomplete != metadata.IncompleteSpans || keyRows != metadata.KeyRows ||
		valueRows != metadata.ValueRows || duplicates != metadata.DuplicateRows ||
		omitted != metadata.RowsOmitted {
		return errors.New("job project/global metadata totals mismatch")
	}
	if want := sortedSet(allGaps); !equalStrings(want, metadata.GapReasons) {
		return errors.New("job project/global gap reasons mismatch")
	}
	minimum, maximum := projectTimeBounds(metadata.Projects)
	if minimum != metadata.MinSpanStart || maximum != metadata.MaxSpanStart {
		return errors.New("job project/global span bounds mismatch")
	}
	return nil
}

func equalStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}

func containsString(values []string, target string) bool {
	index := sort.SearchStrings(values, target)
	return index < len(values) && values[index] == target
}

func hasDuplicateStrings(values []string) bool {
	for index := 1; index < len(values); index++ {
		if values[index] == values[index-1] {
			return true
		}
	}
	return false
}

func (w *Writer) maxEnvelopeBytes() int64 {
	// JSON escaping can expand a valid UTF-8 byte to at most six bytes. Fixed
	// field names/commas are bounded by a small factor of the row count.
	return int64(w.cfg.MaxJobEncodedBytes)*6 + int64(w.cfg.MaxJobRows)*512 +
		int64(w.cfg.MaxJobMetadataBytes)*6 + 4096
}
