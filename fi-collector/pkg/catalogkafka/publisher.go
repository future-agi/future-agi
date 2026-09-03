package catalogkafka

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
)

const (
	publisherStateFormat     = "futureagi.span-attribute-catalog-publisher-state"
	publisherStateVersion    = uint16(1)
	publisherStatePrefix     = "catalog-kafka-publisher-"
	publisherStateSuffix     = ".json"
	publisherStateTempPrefix = ".catalog-kafka-publisher-state-"

	// A MaxRecordBytes envelope expands by four thirds in the JSON base64
	// representation. This bound leaves room for the fixed state fields while
	// preventing a corrupt state file from becoming an unbounded read.
	maxPublisherStateBytes = 2 << 20
	maxSpoolIDBytes        = 32
)

// EnvelopePublisher returns only after the destination acknowledged or
// rejected the immutable envelope. Producer and DirectClickHouseEnvelopePublisher
// satisfy this deliberately narrow seam.
type EnvelopePublisher interface {
	Publish(context.Context, WireEnvelope) error
}

// PublisherConfig configures the crash-safe adapter between catalogwriter's
// durable spool and a synchronous version-3 envelope destination (Kafka or
// direct ClickHouse). One producer stream ID may carry independent project/
// epoch streams; each gets a separate state file.
type PublisherConfig struct {
	StateDirectory  string
	ProducerStreamID string
	MaxChunkRows    int
	MaxChunkBytes   int
}

// SpoolPublisher implements catalogwriter.DeliveryHandler. It assigns a
// sequence only after durably recording the exact envelope bytes, then retains
// that pending envelope until the synchronous destination ACK is received.
type SpoolPublisher struct {
	stateDirectory  string
	producerStreamID string
	maxChunkRows    int
	maxChunkBytes   int
	producer        EnvelopePublisher

	mu         sync.Mutex
	writeState func(string, publisherState) error
}

type publisherState struct {
	Format                string `json:"format"`
	Version               uint16 `json:"version"`
	ProjectID             string `json:"project_id"`
	CatalogEpoch          uint16 `json:"catalog_epoch"`
	ProducerStreamID      string `json:"producer_stream_id"`
	LastSpoolID           string `json:"last_spool_id"`
	Sequence              uint64 `json:"sequence"`
	LastPayloadSHA256     string `json:"last_payload_sha256"`
	PendingSpoolID        string `json:"pending_spool_id"`
	PendingEnvelope       []byte `json:"pending_envelope"`
}

type sourceBatchIdentity struct {
	Format   string                    `json:"format"`
	Version  uint16                    `json:"version"`
	SpoolID  string                    `json:"spool_id"`
	Metadata catalogwriter.JobMetadata `json:"metadata"`
}

// NewSpoolPublisher prepares a dedicated mode-0700 state directory. It does
// not publish or mutate a catalog table.
func NewSpoolPublisher(config PublisherConfig, producer EnvelopePublisher) (*SpoolPublisher, error) {
	if producer == nil {
		return nil, errors.New("catalogkafka: spool publisher requires a producer")
	}
	if config.StateDirectory == "" {
		return nil, errors.New("catalogkafka: publisher state directory is required")
	}
	if !filepath.IsAbs(config.StateDirectory) {
		return nil, errors.New("catalogkafka: publisher state directory must be an absolute dedicated path")
	}
	if err := validateCanonicalUUID("producer stream", config.ProducerStreamID); err != nil {
		return nil, err
	}
	if config.MaxChunkRows < 0 || config.MaxChunkRows > MaxRowsPerChunk {
		return nil, errors.New("catalogkafka: publisher chunk row bound is outside its hard limit")
	}
	if config.MaxChunkBytes < 0 || config.MaxChunkBytes > MaxChunkJSONEachRowBytes {
		return nil, errors.New("catalogkafka: publisher chunk byte bound is outside its hard limit")
	}
	stateDirectory := filepath.Clean(config.StateDirectory)
	if filepath.Dir(stateDirectory) == stateDirectory {
		return nil, errors.New("catalogkafka: publisher state directory cannot be a filesystem root")
	}
	if err := preparePublisherStateDirectory(stateDirectory); err != nil {
		return nil, err
	}
	publisher := &SpoolPublisher{
		stateDirectory: stateDirectory, producerStreamID: strings.Clone(config.ProducerStreamID),
		maxChunkRows: config.MaxChunkRows, maxChunkBytes: config.MaxChunkBytes,
		producer: producer,
	}
	publisher.writeState = publisher.writePublisherState
	return publisher, nil
}

// DeliverCatalogJob publishes one validated writer spool envelope. Returning
// nil is the durable acknowledgement ReplayTo needs before removing its spool
// file. A failed or ambiguous destination call leaves byte-identical pending
// state.
func (p *SpoolPublisher) DeliverCatalogJob(ctx context.Context, delivery catalogwriter.PendingDelivery) error {
	if p == nil || p.producer == nil || p.writeState == nil {
		return errors.New("catalogkafka: nil spool publisher")
	}
	if ctx == nil {
		return errors.New("catalogkafka: nil spool publisher context")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if !validPublisherSpoolID(delivery.ID) {
		return errors.New("catalogkafka: pending delivery has an invalid spool ID")
	}
	metadata := delivery.WireJob.Metadata
	if len(metadata.Projects) != 1 {
		return errors.New("catalogkafka: pending delivery must contain exactly one project")
	}
	projectID := metadata.Projects[0].ProjectID
	sourceBatchDigest, err := publisherSourceBatchDigest(delivery)
	if err != nil {
		return err
	}
	payload, err := PayloadInputFromWireJob(
		delivery.WireJob, projectID, sourceBatchDigest, p.maxChunkRows, p.maxChunkBytes,
	)
	if err != nil {
		return fmt.Errorf("catalogkafka: prepare pending delivery %s: %w", delivery.ID, err)
	}

	p.mu.Lock()
	defer p.mu.Unlock()

	statePath := p.statePath(projectID, metadata.CatalogEpoch)
	state, exists, err := loadPublisherState(statePath)
	if err != nil {
		return err
	}
	if !exists {
		state = initialPublisherState(projectID, metadata.CatalogEpoch, p.producerStreamID)
	}
	if err := validatePublisherState(state, projectID, metadata.CatalogEpoch, p.producerStreamID); err != nil {
		return fmt.Errorf("catalogkafka: invalid publisher state: %w", err)
	}

	if state.PendingSpoolID == "" && state.LastSpoolID == delivery.ID {
		return nil
	}
	if state.PendingSpoolID != "" {
		if state.PendingSpoolID != delivery.ID {
			return fmt.Errorf(
				"catalogkafka: publisher stream has pending spool %s, refusing %s",
				state.PendingSpoolID, delivery.ID,
			)
		}
		envelope, parseErr := ParseWireEnvelope(state.PendingEnvelope)
		if parseErr != nil {
			return fmt.Errorf("catalogkafka: parse pending publisher envelope: %w", parseErr)
		}
		if envelope.Snapshot().Payload.SourceBatchDigest != sourceBatchDigest {
			return errors.New("catalogkafka: pending publisher source digest does not match spool metadata")
		}
		return p.publishAndFinalize(ctx, statePath, state, envelope)
	}

	if state.Sequence == math.MaxUint64 {
		return errors.New("catalogkafka: publisher sequence exhausted")
	}
	envelope, err := NewWireEnvelope(EnvelopeInput{
		ProjectID: projectID, CatalogEpoch: metadata.CatalogEpoch,
		ProducerStreamID: p.producerStreamID, Sequence: state.Sequence + 1,
		PreviousPayloadSHA256: state.LastPayloadSHA256, Payload: payload,
	})
	if err != nil {
		return fmt.Errorf("catalogkafka: build pending delivery %s: %w", delivery.ID, err)
	}
	raw, err := envelope.MarshalBinary()
	if err != nil {
		return err
	}
	if len(raw) > MaxRecordBytes {
		return fmt.Errorf("catalogkafka: pending envelope uses %d bytes, limit %d", len(raw), MaxRecordBytes)
	}
	state.PendingSpoolID = strings.Clone(delivery.ID)
	state.PendingEnvelope = bytes.Clone(raw)
	if err := p.writeState(statePath, state); err != nil {
		return fmt.Errorf("catalogkafka: persist pending publisher envelope: %w", err)
	}
	return p.publishAndFinalize(ctx, statePath, state, envelope)
}

func (p *SpoolPublisher) publishAndFinalize(
	ctx context.Context, statePath string, state publisherState, envelope WireEnvelope,
) error {
	if err := p.producer.Publish(ctx, envelope); err != nil {
		return fmt.Errorf("catalogkafka: publish pending spool %s: %w", state.PendingSpoolID, err)
	}
	state.LastSpoolID = state.PendingSpoolID
	state.Sequence = envelope.Sequence()
	state.LastPayloadSHA256 = envelope.PayloadSHA256()
	state.PendingSpoolID = ""
	state.PendingEnvelope = nil
	if err := p.writeState(statePath, state); err != nil {
		// Persistence is ambiguous after a rename/fsync failure. Disk exposes
		// either the old pending bytes (which are delivered byte-identically)
		// or this completed state (which deduplicates the spool ID).
		return fmt.Errorf("catalogkafka: persist publisher acknowledgement: %w", err)
	}
	return nil
}

func publisherSourceBatchDigest(delivery catalogwriter.PendingDelivery) (string, error) {
	if !validPublisherSpoolID(delivery.ID) {
		return "", errors.New("catalogkafka: pending delivery has an invalid spool ID")
	}
	identity := sourceBatchIdentity{
		Format: "futureagi.span-attribute-catalog-source-batch", Version: 1,
		SpoolID: strings.Clone(delivery.ID), Metadata: delivery.WireJob.Metadata,
	}
	encoded, err := json.Marshal(identity)
	if err != nil {
		return "", fmt.Errorf("catalogkafka: encode source batch identity: %w", err)
	}
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:]), nil
}

func initialPublisherState(projectID string, epoch uint16, streamID string) publisherState {
	return publisherState{
		Format: publisherStateFormat, Version: publisherStateVersion,
		ProjectID: strings.Clone(projectID), CatalogEpoch: epoch,
		ProducerStreamID: strings.Clone(streamID), LastPayloadSHA256: ZeroSHA256,
	}
}

func (p *SpoolPublisher) statePath(projectID string, epoch uint16) string {
	digest := sha256.Sum256([]byte(projectID + "/" + strconv.FormatUint(uint64(epoch), 10) + "/" + p.producerStreamID))
	return filepath.Join(
		p.stateDirectory, publisherStatePrefix+hex.EncodeToString(digest[:])+publisherStateSuffix,
	)
}

func validatePublisherState(state publisherState, projectID string, epoch uint16, streamID string) error {
	if state.Format != publisherStateFormat || state.Version != publisherStateVersion {
		return errors.New("unsupported format or version")
	}
	if state.ProjectID != projectID || state.CatalogEpoch != epoch || state.ProducerStreamID != streamID {
		return errors.New("stream identity mismatch")
	}
	if err := validateCanonicalUUID("publisher project", state.ProjectID); err != nil {
		return err
	}
	if state.CatalogEpoch == 0 {
		return errors.New("zero catalog epoch")
	}
	if err := validateCanonicalUUID("publisher producer stream", state.ProducerStreamID); err != nil {
		return err
	}
	if !isLowerSHA256(state.LastPayloadSHA256) {
		return errors.New("invalid last payload digest")
	}
	if state.Sequence == 0 {
		if state.LastPayloadSHA256 != ZeroSHA256 || state.LastSpoolID != "" {
			return errors.New("unpublished stream has non-zero completion state")
		}
	} else if !validPublisherSpoolID(state.LastSpoolID) || state.LastPayloadSHA256 == ZeroSHA256 {
		return errors.New("published stream has invalid completion state")
	}
	if state.PendingSpoolID == "" {
		if len(state.PendingEnvelope) != 0 {
			return errors.New("pending envelope exists without a spool ID")
		}
		return nil
	}
	if !validPublisherSpoolID(state.PendingSpoolID) || state.PendingSpoolID == state.LastSpoolID {
		return errors.New("invalid pending spool ID")
	}
	if len(state.PendingEnvelope) == 0 || len(state.PendingEnvelope) > MaxRecordBytes {
		return errors.New("pending envelope size is outside its hard bound")
	}
	if state.Sequence == math.MaxUint64 {
		return errors.New("pending envelope follows an exhausted sequence")
	}
	envelope, err := ParseWireEnvelope(state.PendingEnvelope)
	if err != nil {
		return fmt.Errorf("invalid pending envelope: %w", err)
	}
	if envelope.ProjectID() != state.ProjectID || envelope.CatalogEpoch() != state.CatalogEpoch ||
		envelope.ProducerStreamID() != state.ProducerStreamID {
		return errors.New("pending envelope stream identity mismatch")
	}
	if envelope.Sequence() != state.Sequence+1 || envelope.PreviousPayloadSHA256() != state.LastPayloadSHA256 {
		return errors.New("pending envelope sequence or payload chain mismatch")
	}
	return nil
}

func validPublisherSpoolID(value string) bool {
	if len(value) != maxSpoolIDBytes || value != strings.ToLower(value) {
		return false
	}
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == maxSpoolIDBytes/2
}

func preparePublisherStateDirectory(directory string) error {
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return fmt.Errorf("catalogkafka: prepare publisher state directory: %w", err)
	}
	info, err := os.Lstat(directory)
	if err != nil {
		return fmt.Errorf("catalogkafka: inspect publisher state directory: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return errors.New("catalogkafka: publisher state path is not a dedicated directory")
	}
	if info.Mode().Perm() != 0o700 {
		return fmt.Errorf(
			"catalogkafka: publisher state directory must already be mode 0700, got %04o",
			info.Mode().Perm(),
		)
	}
	return nil
}

func loadPublisherState(path string) (publisherState, bool, error) {
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return publisherState{}, false, nil
	}
	if err != nil {
		return publisherState{}, false, fmt.Errorf("catalogkafka: inspect publisher state: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return publisherState{}, false, errors.New("catalogkafka: publisher state is not a regular file")
	}
	if info.Size() <= 0 || info.Size() > maxPublisherStateBytes {
		return publisherState{}, false, fmt.Errorf(
			"catalogkafka: publisher state size %d is outside (0,%d]", info.Size(), maxPublisherStateBytes,
		)
	}
	file, err := os.Open(path)
	if err != nil {
		return publisherState{}, false, fmt.Errorf("catalogkafka: open publisher state: %w", err)
	}
	encoded, readErr := io.ReadAll(io.LimitReader(file, maxPublisherStateBytes+1))
	closeErr := file.Close()
	if readErr != nil || closeErr != nil {
		return publisherState{}, false, fmt.Errorf(
			"catalogkafka: read publisher state: %w", errors.Join(readErr, closeErr),
		)
	}
	if len(encoded) == 0 || len(encoded) > maxPublisherStateBytes {
		return publisherState{}, false, errors.New("catalogkafka: publisher state exceeded its read bound")
	}
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.DisallowUnknownFields()
	var state publisherState
	if err := decoder.Decode(&state); err != nil {
		return publisherState{}, false, fmt.Errorf("catalogkafka: decode publisher state: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return publisherState{}, false, fmt.Errorf("catalogkafka: decode publisher state: %w", err)
	}
	canonical, err := json.Marshal(state)
	if err != nil {
		return publisherState{}, false, fmt.Errorf("catalogkafka: re-encode publisher state: %w", err)
	}
	if !bytes.Equal(encoded, canonical) {
		return publisherState{}, false, errors.New("catalogkafka: publisher state is not canonical JSON")
	}
	return state, true, nil
}

func (p *SpoolPublisher) writePublisherState(path string, state publisherState) error {
	if err := validatePublisherState(
		state, state.ProjectID, state.CatalogEpoch, state.ProducerStreamID,
	); err != nil {
		return err
	}
	encoded, err := json.Marshal(state)
	if err != nil {
		return fmt.Errorf("encode publisher state: %w", err)
	}
	if len(encoded) == 0 || len(encoded) > maxPublisherStateBytes {
		return fmt.Errorf("publisher state uses %d bytes, limit %d", len(encoded), maxPublisherStateBytes)
	}
	temporary, err := os.CreateTemp(p.stateDirectory, publisherStateTempPrefix+"*")
	if err != nil {
		return fmt.Errorf("create publisher state temp: %w", err)
	}
	temporaryPath := temporary.Name()
	keepTemporary := true
	defer func() {
		_ = temporary.Close()
		if keepTemporary {
			_ = os.Remove(temporaryPath)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return fmt.Errorf("secure publisher state temp: %w", err)
	}
	if err := writeAll(temporary, encoded); err != nil {
		return fmt.Errorf("write publisher state temp: %w", err)
	}
	if err := temporary.Sync(); err != nil {
		return fmt.Errorf("sync publisher state temp: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("close publisher state temp: %w", err)
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		return fmt.Errorf("publish publisher state: %w", err)
	}
	keepTemporary = false
	if err := syncPublisherDirectory(p.stateDirectory); err != nil {
		return fmt.Errorf("sync publisher state directory: %w", err)
	}
	return nil
}

func writeAll(writer io.Writer, encoded []byte) error {
	for len(encoded) != 0 {
		written, err := writer.Write(encoded)
		if err != nil {
			return err
		}
		if written <= 0 {
			return io.ErrShortWrite
		}
		encoded = encoded[written:]
	}
	return nil
}

func syncPublisherDirectory(directory string) error {
	handle, err := os.Open(directory)
	if err != nil {
		return err
	}
	return errors.Join(handle.Sync(), handle.Close())
}
