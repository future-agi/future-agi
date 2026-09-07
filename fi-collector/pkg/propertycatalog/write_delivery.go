package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"

	"github.com/google/uuid"
	"golang.org/x/sys/unix"
)

type catalogWriteIdentityKey struct{}
type catalogWriteIdentity struct {
	envelopeID string
	token      string
	proofOnly  bool
}

func withCatalogWriteIdentity(ctx context.Context, envelopeID, suffix string, proofOnly bool) context.Context {
	return context.WithValue(ctx, catalogWriteIdentityKey{}, catalogWriteIdentity{
		envelopeID: envelopeID, token: "property-catalog-v1:" + envelopeID + ":" + suffix, proofOnly: proofOnly,
	})
}

func (s *ClickHouseSink) BeginPropertyCatalogDelivery(ctx context.Context, envelope WireEnvelope) error {
	if s == nil || s.writer == nil || ctx == nil || ctx.Err() != nil {
		return errors.New("propertycatalog: delivery manifest requires durable writer/context")
	}
	return s.writer.journal.storeEnvelope(envelope)
}

func (s *ClickHouseSink) VerifyPropertyCatalogDelivery(ctx context.Context, envelope WireEnvelope) error {
	if s == nil || s.writer == nil || ctx == nil {
		return errors.New("propertycatalog: duplicate requires durable completion proof")
	}
	persisted, err := s.writer.journal.loadEnvelope(envelope.EnvelopeID())
	if err != nil {
		return errors.Join(ErrWriteUnresolved, err)
	}
	want, _ := envelope.MarshalBinary()
	actual, _ := persisted.MarshalBinary()
	if !bytes.Equal(want, actual) {
		return errors.New("propertycatalog: durable envelope manifest conflict")
	}
	for _, chunk := range envelope.Snapshot().Payload.Chunks {
		rows, err := strictDecodeJSONEachRow(chunk.JSONEachRow, chunk.Table)
		if err != nil {
			return err
		}
		proofCtx := withCatalogWriteIdentity(ctx, envelope.EnvelopeID(), fmt.Sprintf("chunk:%d", chunk.Index), true)
		if err := s.InsertPropertyCatalog(proofCtx, chunk.Table, rows); err != nil {
			return err
		}
	}
	lease, err := s.writer.journal.Lock("property-catalog-v1:" + envelope.EnvelopeID() + ":delivery")
	if err != nil {
		return err
	}
	attempt, err := lease.Load()
	lease.Close()
	if err != nil {
		return errors.Join(ErrWriteUnresolved, err)
	}
	if !ledgerDescribesEnvelope(attempt.Body, envelope.Snapshot()) {
		return errors.New("propertycatalog: saved delivery does not describe exact envelope")
	}
	return s.writer.Execute(ctx, attempt, true)
}

func sameLogicalDelivery(a, b []byte) bool {
	first, err := decodeExactWriteRows(a, "property_catalog_deliveries")
	second, otherErr := decodeExactWriteRows(b, "property_catalog_deliveries")
	if err != nil || otherErr != nil || len(first) != 1 || len(second) != 1 {
		return false
	}
	for _, name := range []string{"delivered_at", "_version", "transport", "kafka_partition", "kafka_offset"} {
		delete(first[0], name)
		delete(second[0], name)
	}
	x, _ := json.Marshal(first)
	y, _ := json.Marshal(second)
	return bytes.Equal(x, y)
}

func ledgerDescribesEnvelope(raw []byte, e EnvelopeSnapshot) bool {
	rows, err := decodeExactWriteRows(raw, "property_catalog_deliveries")
	if err != nil || len(rows) != 1 || len(rows[0]) != len(deliveryColumns) {
		return false
	}
	terminal := uint8(0)
	if e.Terminal {
		terminal = 1
	}
	expected := map[string]any{
		"organization_id": e.OrganizationID, "workspace_id": e.WorkspaceID,
		"catalog_epoch": e.CatalogEpoch, "catalog_revision": e.CatalogRevision, "build_token": e.BuildToken,
		"projection_version": e.ProjectionVersion, "source_adapter": string(e.SourceAdapter),
		"producer_stream_id": e.ProducerStreamID, "sequence": e.Sequence,
		"envelope_format": e.Format, "envelope_version": e.Version, "envelope_id": e.EnvelopeID,
		"payload_sha256": e.PayloadSHA256, "previous_payload_sha256": e.PreviousPayloadSHA256,
		"source_batch_digest": e.Payload.SourceBatchDigest, "outcome": string(e.Payload.Outcome),
		"terminal": terminal, "gap_reasons": append([]string{}, e.Payload.GapReasons...),
		"source_rows": e.Payload.SourceRows, "definition_rows": e.Payload.DefinitionRows,
		"value_rows": e.Payload.ValueRows, "tombstone_rows": e.Payload.TombstoneRows,
	}
	for key, value := range expected {
		a, _ := json.Marshal(value)
		b, _ := json.Marshal(rows[0][key])
		if !bytes.Equal(a, b) {
			return false
		}
	}
	return true
}

func readJournalRegular(directoryFD int, name string, limit int64) ([]byte, error) {
	fd, err := unix.Openat(directoryFD, name, unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), name)
	defer f.Close()
	if err := requirePrivateFile(f, false); err != nil {
		return nil, err
	}
	info, err := f.Stat()
	if err != nil || info.Size() <= 0 || info.Size() > limit {
		return nil, errors.New("propertycatalog: invalid bounded manifest size")
	}
	raw, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil || int64(len(raw)) > limit {
		return nil, errors.New("propertycatalog: incomplete bounded manifest read")
	}
	return raw, nil
}

func (j *WriteAttemptJournal) storeEnvelope(envelope WireEnvelope) error {
	raw, err := envelope.MarshalBinary()
	if err != nil {
		return err
	}
	lease, err := j.Lock("property-catalog-v1:" + envelope.EnvelopeID() + ":delivery")
	if err != nil {
		return err
	}
	defer lease.Close()
	name := envelope.EnvelopeID() + ".envelope.json"
	dir := int(j.directory.Fd())
	existing, err := readJournalRegular(dir, name, MaxRecordBytes)
	if err == nil {
		if !bytes.Equal(existing, raw) {
			return errors.New("propertycatalog: durable envelope conflicts")
		}
		return nil
	}
	if !errors.Is(err, unix.ENOENT) {
		return err
	}
	temporary := ".envelope-" + uuid.NewString() + ".tmp"
	fd, err := unix.Openat(dir, temporary, unix.O_CREAT|unix.O_EXCL|unix.O_WRONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0o600)
	if err != nil {
		return err
	}
	f := os.NewFile(uintptr(fd), temporary)
	defer f.Close()
	defer unix.Unlinkat(dir, temporary, 0)
	if n, err := f.Write(raw); err != nil || n != len(raw) {
		return errors.Join(io.ErrShortWrite, err)
	}
	if err := f.Sync(); err != nil {
		return err
	}
	if err := unix.Renameat(dir, temporary, dir, name); err != nil {
		return err
	}
	return j.directory.Sync()
}

func (j *WriteAttemptJournal) loadEnvelope(id string) (WireEnvelope, error) {
	if !isLowerSHA256(id) {
		return WireEnvelope{}, errors.New("propertycatalog: invalid envelope manifest identity")
	}
	raw, err := readJournalRegular(int(j.directory.Fd()), id+".envelope.json", MaxRecordBytes)
	if err != nil {
		return WireEnvelope{}, err
	}
	envelope, err := ParseWireEnvelope(raw)
	if err != nil || envelope.EnvelopeID() != id {
		return WireEnvelope{}, errors.New("propertycatalog: corrupt envelope manifest")
	}
	return envelope, nil
}

// ProveCheckpoint prevents a local ledger seed from skipping an unresolved
// earlier ordered-envelope attempt. Every sequence in the prefix crosses the
// same manifest/data/ledger boundary as a duplicate before Kafka can start.
func (s *ClickHouseSink) ProveCheckpoint(ctx context.Context, checkpoint StreamCheckpoint, proof *HTTPWriteProof) error {
	params := map[string]string{
		"param_org": checkpoint.OrganizationID, "param_workspace": checkpoint.WorkspaceID,
		"param_epoch":    strconv.FormatUint(uint64(checkpoint.CatalogEpoch), 10),
		"param_revision": strconv.FormatUint(checkpoint.CatalogRevision, 10), "param_build": checkpoint.BuildToken,
		"param_adapter": string(checkpoint.SourceAdapter), "param_stream": checkpoint.ProducerStreamID,
		"param_sequence": strconv.FormatUint(checkpoint.Sequence, 10),
	}
	query := `SELECT sequence,envelope_id FROM property_catalog_deliveries
 WHERE organization_id={org:UUID} AND workspace_id={workspace:UUID} AND catalog_epoch={epoch:UInt16}
 AND catalog_revision={revision:UInt64} AND build_token={build:UUID} AND source_adapter={adapter:String}
 AND producer_stream_id={stream:UUID} AND sequence<={sequence:UInt64}
 GROUP BY sequence,envelope_id ORDER BY sequence LIMIT 100001 FORMAT JSONEachRow`
	rows, err := proof.readAgreed(ctx, query, params, maxWriteProofBytes)
	if err != nil {
		return err
	}
	if checkpoint.Sequence > 100000 || uint64(len(rows)) != checkpoint.Sequence {
		return errors.New("propertycatalog: checkpoint completion prefix incomplete or over bound")
	}
	for i, row := range rows {
		var item struct {
			Sequence   uint64 `json:"sequence"`
			EnvelopeID string `json:"envelope_id"`
		}
		if err := decodeProofRow(row, &item); err != nil {
			return err
		}
		if item.Sequence != uint64(i+1) {
			return errors.New("propertycatalog: checkpoint completion prefix gap")
		}
		envelope, err := s.writer.journal.loadEnvelope(item.EnvelopeID)
		if err != nil {
			return errors.Join(ErrWriteUnresolved, err)
		}
		e := envelope.Snapshot()
		if e.OrganizationID != checkpoint.OrganizationID || e.WorkspaceID != checkpoint.WorkspaceID || e.CatalogEpoch != checkpoint.CatalogEpoch ||
			e.CatalogRevision != checkpoint.CatalogRevision || e.BuildToken != checkpoint.BuildToken || e.ProducerStreamID != checkpoint.ProducerStreamID ||
			e.SourceAdapter != checkpoint.SourceAdapter || e.Sequence != item.Sequence {
			return errors.New("propertycatalog: checkpoint manifest belongs to another stream")
		}
		if err := s.VerifyPropertyCatalogDelivery(ctx, envelope); err != nil {
			return err
		}
	}
	return nil
}
