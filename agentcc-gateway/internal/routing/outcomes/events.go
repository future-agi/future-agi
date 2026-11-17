package outcomes

import (
	"crypto/sha1"
	"encoding/hex"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// NewEvent is the outcome-event-v1 payload for a verdict a caller reported
// on generation (source feedback or prev_outcome) at receivedAt. It carries
// ids, the step, the model and the verdict, never content, so every content
// mode allows it. last is the generation's Last record.
func NewEvent(org, generationID, verdict, source string, score *float64, last Last, receivedAt time.Time) models.OutcomeEvent {
	return models.OutcomeEvent{
		EventID:      EventID(org, generationID, source, receivedAt),
		OrgID:        org,
		GenerationID: generationID,
		Outcome:      verdict,
		Source:       source,
		Score:        score,
		AgentRunID:   last.Run,
		AgentID:      last.Agent,
		AgentStep:    last.Step,
		Model:        last.Model,
		TsMs:         receivedAt.UnixMilli(),
	}
}

// EventID is the deterministic id of an outcome event:
// uuid5(NAMESPACE_URL, "agentcc:outcome:<org>:<generation>:<source>:<received_at_ms>"),
// the namespace the control plane's other uuid5 event ids use, so a replayed
// batch is ingested once.
func EventID(org, generationID, source string, receivedAt time.Time) string {
	return uuid5URL("agentcc:outcome:" + org + ":" + generationID + ":" + source + ":" + strconv.FormatInt(receivedAt.UnixMilli(), 10))
}

// namespaceURL is RFC 4122's NAMESPACE_URL, 6ba7b811-9dad-11d1-80b4-00c04fd430c8.
var namespaceURL = [16]byte{0x6b, 0xa7, 0xb8, 0x11, 0x9d, 0xad, 0x11, 0xd1, 0x80, 0xb4, 0x00, 0xc0, 0x4f, 0xd4, 0x30, 0xc8}

// uuid5URL is the RFC 4122 version-5 UUID of name in NAMESPACE_URL, as
// Python's uuid.uuid5(uuid.NAMESPACE_URL, name) writes it.
func uuid5URL(name string) string {
	h := sha1.New()
	h.Write(namespaceURL[:])
	h.Write([]byte(name))
	var u [16]byte
	copy(u[:], h.Sum(nil))
	u[6] = u[6]&0x0f | 0x50 // version 5
	u[8] = u[8]&0x3f | 0x80 // RFC 4122 variant
	var out [36]byte
	hex.Encode(out[0:8], u[0:4])
	out[8] = '-'
	hex.Encode(out[9:13], u[4:6])
	out[13] = '-'
	hex.Encode(out[14:18], u[6:8])
	out[18] = '-'
	hex.Encode(out[19:23], u[8:10])
	out[23] = '-'
	hex.Encode(out[24:], u[10:])
	return string(out[:])
}
