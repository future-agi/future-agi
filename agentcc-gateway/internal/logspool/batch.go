// Package logspool keeps request-log batches the webhook could not take, so a
// restart or an outage does not lose them. A batch is kept in the gateway's
// Redis until the webhook takes it, its spend is counted in the budget seed
// meanwhile, and a batch that has to go undelivered leaves its spend behind
// as a tombstone until its budget period ends.
package logspool

import (
	"crypto/hkdf"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Batch is one spooled webhook batch: log entries (never their bodies or
// request headers) and the events that rode with them.
type Batch struct {
	ID        string            `json:"id"`
	SpooledAt time.Time         `json:"spooled_at"`
	Attempts  int               `json:"attempts"`
	Entries   []json.RawMessage `json:"entries"`
	Events    []json.RawMessage `json:"events,omitempty"`
	// Spend is the spend of Entries, worked out when the batch is spooled:
	// what the seed counts and what a tombstone keeps if the batch is dropped.
	// A platform-funded key's entry has no line: it is in Entries, delivered
	// with its cost, but its spend is not the org's.
	Spend []SpendLine `json:"spend"`
	MAC   string      `json:"mac"`
}

// SpendLine is one request's spend, under the identities the control plane's
// spend summary groups by.
type SpendLine struct {
	Org       string    `json:"org"`
	RequestID string    `json:"request_id"`
	KeyID     string    `json:"key_id,omitempty"`
	UserID    string    `json:"user_id,omitempty"`
	Model     string    `json:"model,omitempty"`
	Cost      float64   `json:"cost"`
	StartedAt time.Time `json:"started_at"`
}

// spendFields are the fields of a log entry SpendLine reads.
type spendFields struct {
	RequestID string            `json:"request_id"`
	Timestamp time.Time         `json:"timestamp"`
	Model     string            `json:"model"`
	Cost      float64           `json:"cost"`
	AuthKeyID string            `json:"auth_key_id"`
	UserID    string            `json:"user_id"`
	Metadata  map[string]string `json:"metadata"`
}

// ErrNoRequestID is why an entry without a request id is never spooled: the
// control plane could not tell a redelivery of it from a new request.
var ErrNoRequestID = errors.New("logspool: entry has no request id")

// SpendOf is the spend line of one encoded log entry. The org is the one the
// control plane stores the entry under: its key's org, else its org id.
func SpendOf(entry json.RawMessage) (SpendLine, error) {
	line, _, err := parseSpend(entry)
	return line, err
}

// parseSpend is SpendOf, and whether the line counts in its org's spend. The
// request of a platform-funded key does not: its entry's metadata carries
// key_billing: platform_funded, which the gateway writes only from the key's
// platform-set billing, and the org's gateway budgets never count its spend.
func parseSpend(entry json.RawMessage) (SpendLine, bool, error) {
	var f spendFields
	if err := json.Unmarshal(entry, &f); err != nil {
		return SpendLine{}, false, err
	}
	if f.RequestID == "" {
		return SpendLine{}, false, ErrNoRequestID
	}
	org := f.Metadata["key_org_id"]
	if org == "" {
		org = f.Metadata["org_id"]
	}
	counted := f.Metadata[models.MetadataKeyBilling] != models.BillingPlatformFunded
	// The key a spend line counts under is the key's name (auth_key_name), as
	// the budgets and the control plane's spend summary key it.
	return SpendLine{Org: org, RequestID: f.RequestID, KeyID: f.Metadata["auth_key_name"], UserID: f.UserID,
		Model: f.Model, Cost: f.Cost, StartedAt: f.Timestamp.UTC()}, counted, nil
}

// NewBatch builds the batch of entries and events, with a fresh random id, and
// seals it. Entries without a request id are left out and counted in skipped.
// A platform-funded key's entry is spooled without a spend line, so neither
// the budget seed nor a tombstone counts it.
func NewBatch(key []byte, now time.Time, entries, events []json.RawMessage) (b *Batch, skipped int, err error) {
	id, err := randomID()
	if err != nil {
		return nil, 0, err
	}
	b = &Batch{ID: id, SpooledAt: now.UTC()}
	for _, e := range entries {
		line, counted, err := parseSpend(e)
		if err != nil {
			skipped++
			continue
		}
		b.Entries = append(b.Entries, e)
		if counted {
			b.Spend = append(b.Spend, line)
		}
	}
	b.Events = events
	b.Seal(key)
	return b, skipped, nil
}

// Body is what the webhook is sent for the batch: {"logs": entries, then events}.
func (b *Batch) Body() ([]byte, error) {
	items := make([]json.RawMessage, 0, len(b.Entries)+len(b.Events))
	items = append(items, b.Entries...)
	items = append(items, b.Events...)
	return json.Marshal(struct {
		Logs []json.RawMessage `json:"logs"`
	}{items})
}

// Seal sets the batch's MAC under key: HMAC-SHA256 over every other field,
// spooled_at and attempts included.
func (b *Batch) Seal(key []byte) {
	b.MAC = hex.EncodeToString(b.mac(key))
}

// Verify reports whether the batch's MAC is right under one of keys.
func (b *Batch) Verify(keys ...[]byte) bool {
	got, err := hex.DecodeString(b.MAC)
	if err != nil || len(got) == 0 {
		return false
	}
	for _, k := range keys {
		if len(k) > 0 && hmac.Equal(got, b.mac(k)) {
			return true
		}
	}
	return false
}

func (b *Batch) mac(key []byte) []byte {
	c := *b
	c.MAC = ""
	msg, _ := json.Marshal(c)
	m := hmac.New(sha256.New, key)
	m.Write([]byte("agentcc-logspool-batch-v1\x00"))
	m.Write(msg)
	return m.Sum(nil)
}

// MACKey is the spool's MAC key: the dedicated spool key when one is set,
// else one derived from the webhook secret under its own label, so the spool
// never MACs with the key the webhook is signed with. Empty when neither is
// set: the spool then stays off.
func MACKey(spoolKey, webhookSecret string) []byte {
	if spoolKey != "" {
		return derive(spoolKey)
	}
	if webhookSecret != "" {
		return derive(webhookSecret)
	}
	return nil
}

func derive(secret string) []byte {
	out, err := hkdf.Key(sha256.New, []byte(secret), nil, "agentcc-logspool-v1", 32)
	if err != nil {
		return nil
	}
	return out
}

func randomID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(b[:]), nil
}
