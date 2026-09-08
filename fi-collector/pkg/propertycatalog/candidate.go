package propertycatalog

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
)

const (
	CandidateFormat  = "futureagi.property-catalog-candidate"
	CandidateVersion = uint16(1)
)

// CandidateValue is the bounded collector projection needed to build one hot
// value envelope. It deliberately excludes revision, stream, and sequence
// fields: autoscaled collectors are not permitted to choose any of them.
type CandidateValue struct {
	SourceKind            PropertyKind `json:"source_kind"`
	AttributeKey          string       `json:"attribute_key"`
	AttributeType         string       `json:"attribute_type"`
	ValueFingerprint      string       `json:"value_fingerprint"`
	ValueJSON             string       `json:"value_json"`
	ValueSearchTextFolded string       `json:"value_search_text_folded"`
	FirstSeen             string       `json:"first_seen"`
	LastSeen              string       `json:"last_seen"`
}

type candidateUnsignedJSON struct {
	Format            string           `json:"format"`
	Version           uint16           `json:"version"`
	OrganizationID    string           `json:"organization_id"`
	WorkspaceID       string           `json:"workspace_id"`
	ProjectID         string           `json:"project_id"`
	CatalogEpoch      uint16           `json:"catalog_epoch"`
	ProjectionVersion uint16           `json:"projection_version"`
	SourceRows        uint64           `json:"source_rows"`
	FirstSeen         string           `json:"first_seen"`
	LastSeen          string           `json:"last_seen"`
	GapReasons        []string         `json:"gap_reasons"`
	Values            []CandidateValue `json:"values"`
}

type candidateJSON struct {
	Format            string           `json:"format"`
	Version           uint16           `json:"version"`
	CandidateID       string           `json:"candidate_id"`
	OrganizationID    string           `json:"organization_id"`
	WorkspaceID       string           `json:"workspace_id"`
	ProjectID         string           `json:"project_id"`
	CatalogEpoch      uint16           `json:"catalog_epoch"`
	ProjectionVersion uint16           `json:"projection_version"`
	SourceRows        uint64           `json:"source_rows"`
	FirstSeen         string           `json:"first_seen"`
	LastSeen          string           `json:"last_seen"`
	GapReasons        []string         `json:"gap_reasons"`
	Values            []CandidateValue `json:"values"`
}

// CandidateSnapshot is a defensive copy of immutable candidate metadata.
type CandidateSnapshot struct {
	CandidateID       string
	OrganizationID    string
	WorkspaceID       string
	ProjectID         string
	CatalogEpoch      uint16
	ProjectionVersion uint16
	SourceRows        uint64
	FirstSeen         string
	LastSeen          string
	GapReasons        []string
	Values            []CandidateValue
}

// CandidateNotAdmittedReason identifies an expected rollout boundary. These
// outcomes are safe to skip because canonical ClickHouse ingestion already
// succeeded and reconciliation remains authoritative. They are deliberately
// narrower than malformed candidates, fence conflicts, and transient I/O.
type CandidateNotAdmittedReason string

const (
	CandidateNoCurrentBuildFence     CandidateNotAdmittedReason = "no_current_build_fence"
	CandidateWorkspaceNotInRollout   CandidateNotAdmittedReason = "workspace_not_in_rollout"
	CandidateOutsideBuildSourceScope CandidateNotAdmittedReason = "outside_current_build_source_scope"
)

var ErrCandidateNotAdmitted = errors.New("propertycatalog: candidate is not admitted by a current build")

// CandidateNotAdmittedError is an explicit non-retry outcome for the
// singleton sequencer. errors.As and errors.Is may be used by supervisors;
// every other error remains retryable or fail-closed according to its type.
type CandidateNotAdmittedError struct {
	CandidateID    string
	OrganizationID string
	WorkspaceID    string
	ProjectID      string
	Reason         CandidateNotAdmittedReason
}

func (e *CandidateNotAdmittedError) Error() string {
	if e == nil {
		return ErrCandidateNotAdmitted.Error()
	}
	return fmt.Sprintf(
		"%s: reason=%s candidate=%s organization=%s workspace=%s project=%s",
		ErrCandidateNotAdmitted, e.Reason, e.CandidateID,
		e.OrganizationID, e.WorkspaceID, e.ProjectID,
	)
}

func (*CandidateNotAdmittedError) Unwrap() error { return ErrCandidateNotAdmitted }

func candidateNotAdmitted(snapshot CandidateSnapshot, reason CandidateNotAdmittedReason) error {
	return &CandidateNotAdmittedError{
		CandidateID: snapshot.CandidateID, OrganizationID: snapshot.OrganizationID,
		WorkspaceID: snapshot.WorkspaceID, ProjectID: snapshot.ProjectID, Reason: reason,
	}
}

// WireCandidate is immutable to callers; every byte slice and collection
// returned from it is copied.
type WireCandidate struct {
	document candidateJSON
	raw      []byte
}

func candidateUnsigned(document candidateJSON) candidateUnsignedJSON {
	return candidateUnsignedJSON{
		Format: document.Format, Version: document.Version,
		OrganizationID: document.OrganizationID, WorkspaceID: document.WorkspaceID,
		ProjectID: document.ProjectID, CatalogEpoch: document.CatalogEpoch,
		ProjectionVersion: document.ProjectionVersion,
		SourceRows:        document.SourceRows,
		FirstSeen:         document.FirstSeen, LastSeen: document.LastSeen,
		GapReasons: append([]string(nil), document.GapReasons...),
		Values:     append([]CandidateValue(nil), document.Values...),
	}
}

func newWireCandidate(group hotGroup, cfg RuntimeConfig) (WireCandidate, error) {
	if group.spans == 0 {
		return WireCandidate{}, errors.New("propertycatalog: candidate requires source rows")
	}
	valueKeys := make([]string, 0, len(group.values))
	for key := range group.values {
		valueKeys = append(valueKeys, key)
	}
	sort.Strings(valueKeys)
	values := make([]CandidateValue, 0, len(valueKeys))
	for _, key := range valueKeys {
		row := group.values[key]
		values = append(values, CandidateValue{
			SourceKind: row.SourceKind, AttributeKey: row.AttributeKey,
			AttributeType: row.AttributeType, ValueFingerprint: row.ValueFingerprint,
			ValueJSON: row.ValueJSON, ValueSearchTextFolded: row.ValueSearchTextFolded,
			FirstSeen: row.FirstSeen, LastSeen: row.LastSeen,
		})
	}
	document := candidateJSON{
		Format: CandidateFormat, Version: CandidateVersion,
		OrganizationID: group.key.organizationID, WorkspaceID: group.key.workspaceID,
		ProjectID: group.key.projectID, CatalogEpoch: cfg.CatalogEpoch,
		ProjectionVersion: cfg.ProjectionVersion,
		SourceRows:        group.spans,
		FirstSeen:         group.firstSeen.UTC().Format(dateTime64Layout),
		LastSeen:          group.lastSeen.UTC().Format(dateTime64Layout),
		GapReasons:        sortedGapReasons(group.gaps), Values: values,
	}
	unsigned, err := json.Marshal(candidateUnsigned(document))
	if err != nil {
		return WireCandidate{}, fmt.Errorf("propertycatalog: encode candidate identity: %w", err)
	}
	digest := sha256.Sum256(unsigned)
	document.CandidateID = hex.EncodeToString(digest[:])
	raw, err := json.Marshal(document)
	if err != nil {
		return WireCandidate{}, fmt.Errorf("propertycatalog: encode candidate: %w", err)
	}
	if len(raw) > cfg.MaxCandidateBytes || len(raw) > MaxCandidateRecordBytes {
		return WireCandidate{}, fmt.Errorf(
			"propertycatalog: candidate uses %d bytes, configured limit %d",
			len(raw), min(cfg.MaxCandidateBytes, MaxCandidateRecordBytes),
		)
	}
	if err := validateCandidate(document); err != nil {
		return WireCandidate{}, err
	}
	return WireCandidate{document: document, raw: bytes.Clone(raw)}, nil
}

func ParseWireCandidate(raw []byte) (WireCandidate, error) {
	if len(raw) < 2 || len(raw) > MaxCandidateRecordBytes || bytes.Contains(raw, []byte{'\n'}) {
		return WireCandidate{}, errors.New("propertycatalog: candidate is empty, multiline, or exceeds the byte limit")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	decoder.UseNumber()
	var document candidateJSON
	if err := decoder.Decode(&document); err != nil {
		return WireCandidate{}, fmt.Errorf("propertycatalog: decode candidate: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return WireCandidate{}, err
	}
	canonical, err := json.Marshal(document)
	if err != nil || !bytes.Equal(canonical, raw) {
		return WireCandidate{}, errors.New("propertycatalog: candidate is not canonical JSON")
	}
	if err := validateCandidate(document); err != nil {
		return WireCandidate{}, err
	}
	return WireCandidate{document: document, raw: bytes.Clone(raw)}, nil
}

func validateCandidate(document candidateJSON) error {
	if document.Format != CandidateFormat || document.Version != CandidateVersion {
		return errors.New("propertycatalog: candidate format/version is unsupported")
	}
	if !isLowerSHA256(document.CandidateID) {
		return errors.New("propertycatalog: candidate identity is not lowercase SHA-256")
	}
	for name, value := range map[string]string{
		"candidate organization": document.OrganizationID,
		"candidate workspace":    document.WorkspaceID,
		"candidate project":      document.ProjectID,
	} {
		if err := validateCanonicalUUID(name, value); err != nil {
			return err
		}
	}
	if document.CatalogEpoch == 0 || document.ProjectionVersion == 0 || document.SourceRows == 0 {
		return errors.New("propertycatalog: candidate epoch, projection, and source rows must be positive")
	}
	first, err := parseCandidateTime("candidate first_seen", document.FirstSeen)
	if err != nil {
		return err
	}
	last, err := parseCandidateTime("candidate last_seen", document.LastSeen)
	if err != nil {
		return err
	}
	if last.Before(first) {
		return errors.New("propertycatalog: candidate last_seen precedes first_seen")
	}
	if document.GapReasons == nil || !sort.StringsAreSorted(document.GapReasons) {
		return errors.New("propertycatalog: candidate gap reasons must be a sorted non-nil list")
	}
	for index, reason := range document.GapReasons {
		if err := validateText(fmt.Sprintf("candidate gap reason %d", index), reason, true, MaxGapReasonBytes); err != nil {
			return err
		}
		if index > 0 && reason == document.GapReasons[index-1] {
			return errors.New("propertycatalog: candidate gap reasons contain a duplicate")
		}
	}
	if document.Values == nil {
		return errors.New("propertycatalog: candidate values must be a non-nil list")
	}
	previous := ""
	for index, value := range document.Values {
		if err := validateCandidateValue(value, first, last); err != nil {
			return fmt.Errorf("propertycatalog: candidate value %d: %w", index, err)
		}
		identity := candidateValueIdentity(value)
		if index > 0 && identity <= previous {
			return errors.New("propertycatalog: candidate values are unsorted or duplicated")
		}
		previous = identity
	}
	unsigned, err := json.Marshal(candidateUnsigned(document))
	if err != nil {
		return err
	}
	digest := sha256.Sum256(unsigned)
	if hex.EncodeToString(digest[:]) != document.CandidateID {
		return errors.New("propertycatalog: candidate identity digest mismatch")
	}
	return nil
}

func parseCandidateTime(name, value string) (time.Time, error) {
	parsed, err := time.Parse(dateTime64Layout, value)
	if err != nil || parsed.Format(dateTime64Layout) != value {
		return time.Time{}, fmt.Errorf("propertycatalog: %s is not canonical DateTime64(6)", name)
	}
	return parsed, nil
}

func validateCandidateValue(value CandidateValue, candidateFirst, candidateLast time.Time) error {
	if value.SourceKind != KindCustomAttribute && value.SourceKind != KindSystemAttribute {
		return errors.New("source kind is unsupported")
	}
	if err := validateText("attribute key", value.AttributeKey, true, MaxPropertyIdentityBytes); err != nil {
		return err
	}
	switch value.AttributeType {
	case attributecatalog.AttributeTypeString, attributecatalog.AttributeTypeNumber,
		attributecatalog.AttributeTypeBoolean, attributecatalog.AttributeTypeArray,
		attributecatalog.AttributeTypeMap, attributecatalog.AttributeTypeJSON:
	default:
		return errors.New("attribute type is unsupported")
	}
	if !isLowerSHA256(value.ValueFingerprint) || len(value.ValueJSON) > MaxValueJSONBytes ||
		len(value.ValueSearchTextFolded) > MaxValueSearchTextBytes || !json.Valid([]byte(value.ValueJSON)) {
		return errors.New("value fingerprint or JSON/search payload is invalid")
	}
	first, err := parseCandidateTime("value first_seen", value.FirstSeen)
	if err != nil {
		return err
	}
	last, err := parseCandidateTime("value last_seen", value.LastSeen)
	if err != nil {
		return err
	}
	if first.Before(candidateFirst) || last.After(candidateLast) || last.Before(first) {
		return errors.New("value observation range is outside the candidate")
	}
	return nil
}

func candidateValueIdentity(value CandidateValue) string {
	return strings.Join([]string{
		string(value.SourceKind), value.AttributeKey, value.AttributeType, value.ValueFingerprint,
	}, "\x00")
}

func (c WireCandidate) MarshalBinary() ([]byte, error) {
	if err := validateCandidate(c.document); err != nil || len(c.raw) == 0 {
		if err == nil {
			err = errors.New("propertycatalog: empty candidate bytes")
		}
		return nil, err
	}
	return bytes.Clone(c.raw), nil
}

func (c WireCandidate) Snapshot() CandidateSnapshot {
	return CandidateSnapshot{
		CandidateID: c.document.CandidateID, OrganizationID: c.document.OrganizationID,
		WorkspaceID: c.document.WorkspaceID, ProjectID: c.document.ProjectID,
		CatalogEpoch: c.document.CatalogEpoch, ProjectionVersion: c.document.ProjectionVersion,
		SourceRows: c.document.SourceRows, FirstSeen: c.document.FirstSeen,
		LastSeen:   c.document.LastSeen,
		GapReasons: append([]string(nil), c.document.GapReasons...),
		Values:     append([]CandidateValue(nil), c.document.Values...),
	}
}

func (c WireCandidate) hotGroup() (hotGroup, error) {
	if err := validateCandidate(c.document); err != nil {
		return hotGroup{}, err
	}
	first, _ := parseCandidateTime("candidate first_seen", c.document.FirstSeen)
	last, _ := parseCandidateTime("candidate last_seen", c.document.LastSeen)
	group := hotGroup{
		key: hotGroupKey{
			organizationID: c.document.OrganizationID,
			workspaceID:    c.document.WorkspaceID,
			projectID:      c.document.ProjectID,
		},
		spans: c.document.SourceRows, firstSeen: first, lastSeen: last,
		values: make(map[string]AttributeValueRow, len(c.document.Values)),
		gaps:   make(map[string]struct{}, len(c.document.GapReasons)),
	}
	for _, reason := range c.document.GapReasons {
		group.gaps[reason] = struct{}{}
	}
	for _, value := range c.document.Values {
		identity := candidateValueIdentity(value)
		group.values[identity] = AttributeValueRow{
			OrganizationID: c.document.OrganizationID, WorkspaceID: c.document.WorkspaceID,
			ProjectID: c.document.ProjectID, CatalogEpoch: c.document.CatalogEpoch,
			SourceKind: value.SourceKind, AttributeKey: value.AttributeKey,
			AttributeType: value.AttributeType, ValueFingerprint: value.ValueFingerprint,
			ValueJSON: value.ValueJSON, ValueSearchTextFolded: value.ValueSearchTextFolded,
			FirstSeen: value.FirstSeen, LastSeen: value.LastSeen,
		}
	}
	return group, nil
}

type candidateRow struct {
	scoped ScopedSpan
	order  string
}

// BuildCandidates deterministically groups, orders, and recursively splits a
// canonical batch into bounded workspace-keyed records. It is intentionally
// all-or-nothing before the first broker write: malformed authenticated scope
// cannot silently become a candidate for another tenant.
func BuildCandidates(cfg RuntimeConfig, rows []ScopedSpan) ([]WireCandidate, error) {
	mode, err := cfg.SelectedMode()
	if err != nil {
		return nil, err
	}
	if mode != RuntimeKafka {
		return nil, errors.New("propertycatalog: candidate building requires Kafka candidate mode")
	}
	cfg = cfg.WithDefaults()
	if len(rows) == 0 || len(rows) > cfg.MaxSpansPerBatch {
		return nil, fmt.Errorf(
			"propertycatalog: candidate batch requires 1..%d spans", cfg.MaxSpansPerBatch,
		)
	}
	grouped := make(map[hotGroupKey][]candidateRow)
	for index, scoped := range cloneHotRows(rows) {
		key, _, scopeErr := authenticatedHotRowScope(scoped)
		if scopeErr != nil {
			return nil, fmt.Errorf("propertycatalog: candidate row %d: %w", index, scopeErr)
		}
		order, orderErr := candidateRowOrder(scoped)
		if orderErr != nil {
			return nil, fmt.Errorf("propertycatalog: candidate row %d: %w", index, orderErr)
		}
		grouped[key] = append(grouped[key], candidateRow{scoped: scoped, order: order})
	}
	keys := make([]hotGroupKey, 0, len(grouped))
	for key := range grouped {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].organizationID != keys[j].organizationID {
			return keys[i].organizationID < keys[j].organizationID
		}
		if keys[i].workspaceID != keys[j].workspaceID {
			return keys[i].workspaceID < keys[j].workspaceID
		}
		return keys[i].projectID < keys[j].projectID
	})
	result := make([]WireCandidate, 0, len(keys))
	for _, key := range keys {
		groupRows := grouped[key]
		sort.SliceStable(groupRows, func(i, j int) bool { return groupRows[i].order < groupRows[j].order })
		for start := 0; start < len(groupRows); start += cfg.MaxCandidateSpans {
			end := min(start+cfg.MaxCandidateSpans, len(groupRows))
			chunk := make([]ScopedSpan, 0, end-start)
			for _, row := range groupRows[start:end] {
				chunk = append(chunk, row.scoped)
			}
			built, buildErr := buildCandidateChunk(cfg, chunk)
			if buildErr != nil {
				return nil, buildErr
			}
			result = append(result, built...)
		}
	}
	return result, nil
}

func buildCandidateChunk(cfg RuntimeConfig, rows []ScopedSpan) ([]WireCandidate, error) {
	groups, errs := collectHotGroupsWithScope(rows, len(rows), func(scoped ScopedSpan) (hotGroupKey, time.Time, bool, error) {
		key, seenAt, err := authenticatedHotRowScope(scoped)
		return key, seenAt, err == nil, err
	}, cfg)
	if len(errs) != 0 {
		return nil, errors.Join(errs...)
	}
	if len(groups) != 1 {
		return nil, errors.New("propertycatalog: candidate chunk crossed a project or tenant boundary")
	}
	candidate, err := newWireCandidate(groups[0], cfg)
	if err == nil {
		return []WireCandidate{candidate}, nil
	}
	if len(rows) == 1 || !strings.Contains(err.Error(), "candidate uses") {
		return nil, err
	}
	middle := len(rows) / 2
	left, leftErr := buildCandidateChunk(cfg, rows[:middle])
	if leftErr != nil {
		return nil, leftErr
	}
	right, rightErr := buildCandidateChunk(cfg, rows[middle:])
	if rightErr != nil {
		return nil, rightErr
	}
	return append(left, right...), nil
}

func candidateRowOrder(scoped ScopedSpan) (string, error) {
	row := scoped.Row
	identity := struct {
		OrganizationID string `json:"organization_id"`
		WorkspaceID    string `json:"workspace_id"`
		ProjectID      any    `json:"project_id"`
		TraceID        any    `json:"trace_id"`
		SpanID         any    `json:"span_id"`
		StartTime      any    `json:"start_time"`
		Model          any    `json:"model"`
		Strings        any    `json:"attrs_string"`
		Numbers        any    `json:"attrs_number"`
		Booleans       any    `json:"attrs_bool"`
		Extra          any    `json:"attributes_extra"`
	}{
		OrganizationID: scoped.OrganizationID, WorkspaceID: scoped.WorkspaceID,
		ProjectID: row["project_id"], TraceID: row["trace_id"], SpanID: row["id"],
		StartTime: row["start_time"], Model: row["model"], Strings: row["attrs_string"],
		Numbers: row["attrs_number"], Booleans: row["attrs_bool"], Extra: row["attributes_extra"],
	}
	raw, err := json.Marshal(identity)
	if err != nil {
		return "", fmt.Errorf("encode deterministic candidate row order: %w", err)
	}
	return string(raw), nil
}

// CandidateKafkaKey is workspace-only by design. All projects and collector
// replicas for one workspace therefore reach one Kafka partition before the
// singleton allocates any ordered catalog sequence.
func CandidateKafkaKey(candidate WireCandidate) ([]byte, error) {
	snapshot := candidate.Snapshot()
	if err := validateCanonicalUUID("candidate Kafka workspace", snapshot.WorkspaceID); err != nil {
		return nil, err
	}
	return []byte(snapshot.WorkspaceID), nil
}

type CandidateProducer struct {
	topic  string
	writer catalogkafka.RecordWriter
}

func NewCandidateProducer(topic string, writer catalogkafka.RecordWriter) (*CandidateProducer, error) {
	if err := validateTopic(topic); err != nil {
		return nil, err
	}
	if writer == nil {
		return nil, errors.New("propertycatalog: candidate producer requires a record writer")
	}
	return &CandidateProducer{topic: strings.Clone(topic), writer: writer}, nil
}

func (p *CandidateProducer) Publish(ctx context.Context, candidate WireCandidate) error {
	if p == nil || p.writer == nil || ctx == nil {
		return errors.New("propertycatalog: candidate producer requires a writer and context")
	}
	value, err := candidate.MarshalBinary()
	if err != nil {
		return err
	}
	key, err := CandidateKafkaKey(candidate)
	if err != nil {
		return err
	}
	if err := p.writer.WriteRecord(ctx, catalogkafka.Record{
		Topic: p.topic, Key: bytes.Clone(key), Value: bytes.Clone(value),
	}); err != nil {
		return fmt.Errorf("propertycatalog: synchronous candidate produce: %w", err)
	}
	return nil
}

func (p *CandidateProducer) Close() {
	if p != nil && p.writer != nil {
		p.writer.Close()
	}
}

// CandidateWriter is the collector-side PropertyCatalogWriter. It owns no
// revision, sequence, spool, or drain state.
type CandidateWriter struct {
	cfg      RuntimeConfig
	producer *CandidateProducer
	queue    chan []WireCandidate
	gaps     chan error
	stop     chan struct{}

	mu       sync.Mutex
	started  bool
	stopping bool
	ctx      context.Context
	cancel   context.CancelFunc
	stopOnce sync.Once
	wg       sync.WaitGroup
}

func NewCandidateWriter(cfg RuntimeConfig, producer *CandidateProducer) (*CandidateWriter, error) {
	mode, err := cfg.SelectedMode()
	if err != nil {
		return nil, err
	}
	if mode != RuntimeKafka || producer == nil {
		return nil, errors.New("propertycatalog: candidate writer requires Kafka candidate mode and producer")
	}
	cfg = cfg.WithDefaults()
	return &CandidateWriter{
		cfg: cfg, producer: producer, queue: make(chan []WireCandidate, cfg.QueueDepth),
		gaps: make(chan error, cfg.QueueDepth*2), stop: make(chan struct{}),
	}, nil
}

func (w *CandidateWriter) Start(ctx context.Context) error {
	if w == nil || ctx == nil {
		return errors.New("propertycatalog: candidate writer requires a context")
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.started || w.stopping {
		return errors.New("propertycatalog: candidate writer is already started or stopping")
	}
	w.ctx, w.cancel = context.WithCancel(ctx)
	w.started = true
	w.wg.Add(1)
	go w.run()
	return nil
}

func (w *CandidateWriter) EnqueueCanonicalSpans(rows []ScopedSpan) error {
	if w == nil || w.producer == nil {
		return errors.New("propertycatalog: nil candidate writer")
	}
	w.mu.Lock()
	ready := w.started && !w.stopping && w.ctx != nil && w.ctx.Err() == nil
	w.mu.Unlock()
	if !ready {
		return errors.New("propertycatalog: candidate writer is not accepting work")
	}
	candidates, err := BuildCandidates(w.cfg, rows)
	if err != nil {
		return err
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	if !w.started || w.stopping {
		return errors.New("propertycatalog: candidate writer stopped while building work")
	}
	select {
	case w.queue <- append([]WireCandidate(nil), candidates...):
		return nil
	default:
		return errors.New("propertycatalog: bounded candidate queue is full; canonical reconciliation must recover this batch")
	}
}

func (w *CandidateWriter) Gaps() <-chan error {
	if w == nil {
		return nil
	}
	return w.gaps
}

func (w *CandidateWriter) run() {
	defer w.wg.Done()
	for {
		select {
		case batch := <-w.queue:
			w.publishBatch(batch)
		case <-w.stop:
			for {
				select {
				case batch := <-w.queue:
					w.publishBatch(batch)
				default:
					return
				}
			}
		case <-w.ctx.Done():
			return
		}
	}
}

func (w *CandidateWriter) publishBatch(candidates []WireCandidate) {
	ctx, cancel := context.WithTimeout(w.ctx, w.cfg.Kafka.DeliveryTimeout)
	defer cancel()
	for index, candidate := range candidates {
		if err := w.producer.Publish(ctx, candidate); err != nil {
			w.reportGap(fmt.Errorf(
				"propertycatalog: publish candidate %d of %d within one batch deadline: %w",
				index+1, len(candidates), err,
			))
			return
		}
	}
}

func (w *CandidateWriter) reportGap(err error) {
	if err == nil {
		return
	}
	select {
	case w.gaps <- err:
	default:
	}
}

// Shutdown stops acceptance, drains every already-accepted batch, and cancels
// an in-flight broker call if the caller's single shutdown deadline expires.
// It returns only after the worker has stopped, so the producer may then be
// closed without racing a publish.
func (w *CandidateWriter) Shutdown(ctx context.Context) error {
	if w == nil || ctx == nil {
		return errors.New("propertycatalog: candidate shutdown requires writer and context")
	}
	w.mu.Lock()
	if !w.started {
		w.mu.Unlock()
		return errors.New("propertycatalog: candidate writer was not started")
	}
	w.stopping = true
	w.stopOnce.Do(func() { close(w.stop) })
	w.mu.Unlock()
	done := make(chan struct{})
	go func() { w.wg.Wait(); close(done) }()
	select {
	case <-done:
		w.cancel()
		return nil
	case <-ctx.Done():
		w.cancel()
		<-done
		return ctx.Err()
	}
}
