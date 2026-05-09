package alerting

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Manager holds alert rules and channels.
type Manager struct {
	rules    []*Rule
	channels map[string]Channel
}

// Rule is a single alerting rule with a sliding window counter.
type Rule struct {
	Name      string
	Metric    string
	Condition string
	Threshold float64
	Window    time.Duration
	Cooldown  time.Duration
	Channels  []string
	counter   *WindowCounter
	mu        sync.Mutex
	lastFired time.Time
}

// Alert is the payload sent to channels.
type Alert struct {
	Name      string    `json:"alert"`
	Metric    string    `json:"metric"`
	Value     float64   `json:"value"`
	Threshold float64   `json:"threshold"`
	Condition string    `json:"condition"`
	Window    string    `json:"window"`
	FiredAt   time.Time `json:"fired_at"`
	Service   string    `json:"service"`
}

// Channel is an alert delivery target.
type Channel interface {
	Send(alert Alert) error
}

// NewManager creates a manager from config.
func NewManager(cfg config.AlertingConfig) *Manager {
	channels := make(map[string]Channel, len(cfg.Channels))
	for _, ch := range cfg.Channels {
		switch ch.Type {
		case "webhook":
			channels[ch.Name] = NewWebhookChannel(ch.URL, ch.Headers)
		case "slack":
			channels[ch.Name] = NewSlackChannel(ch.URL)
		case "log":
			channels[ch.Name] = &LogChannel{}
		default:
			slog.Warn("unknown alert channel type", "name", ch.Name, "type", ch.Type)
		}
	}

	rules := make([]*Rule, 0, len(cfg.Rules))
	for _, rc := range cfg.Rules {
		window := rc.Window
		if window <= 0 {
			window = time.Minute
		}
		rules = append(rules, &Rule{
			Name:      rc.Name,
			Metric:    rc.Metric,
			Condition: rc.Condition,
			Threshold: rc.Threshold,
			Window:    window,
			Cooldown:  rc.Cooldown,
			Channels:  rc.Channels,
			counter:   NewWindowCounter(window, 60),
		})
	}

	return &Manager{
		rules:    rules,
		channels: channels,
	}
}

// NewManagerWithChannels creates a manager with injected channels (for testing).
func NewManagerWithChannels(rules []*Rule, channels map[string]Channel) *Manager {
	return &Manager{rules: rules, channels: channels}
}

// NewRule creates a rule with a fresh window counter.
func NewRule(name, metric, condition string, threshold float64, window, cooldown time.Duration, channels []string) *Rule {
	if window <= 0 {
		window = time.Minute
	}
	return &Rule{
		Name:      name,
		Metric:    metric,
		Condition: condition,
		Threshold: threshold,
		Window:    window,
		Cooldown:  cooldown,
		Channels:  channels,
		counter:   NewWindowCounter(window, 60),
	}
}

// Record adds a value to a metric across all rules that track it.
func (m *Manager) Record(metric string, value float64) {
	for _, r := range m.rules {
		if r.Metric == metric {
			r.counter.Record(value)
		}
	}
}

// Evaluate checks all rules and fires alerts if thresholds are exceeded.
func (m *Manager) Evaluate() {
	now := time.Now()
	for _, r := range m.rules {
		sum := r.counter.Sum()
		if !conditionMet(sum, r.Condition, r.Threshold) {
			continue
		}

		// Check cooldown.
		r.mu.Lock()
		if r.Cooldown > 0 && !r.lastFired.IsZero() && now.Sub(r.lastFired) < r.Cooldown {
			r.mu.Unlock()
			continue
		}
		r.lastFired = now
		r.mu.Unlock()

		alert := Alert{
			Name:      r.Name,
			Metric:    r.Metric,
			Value:     sum,
			Threshold: r.Threshold,
			Condition: r.Condition,
			Window:    r.Window.String(),
			FiredAt:   now,
			Service:   "agentcc-gateway",
		}

		for _, chName := range r.Channels {
			ch, ok := m.channels[chName]
			if !ok {
				slog.Warn("alert channel not found", "channel", chName, "rule", r.Name)
				continue
			}
			if err := ch.Send(alert); err != nil {
				slog.Warn("alert send failed", "channel", chName, "rule", r.Name, "error", err)
			}
		}
	}
}

// RuleCount returns the number of rules.
func (m *Manager) RuleCount() int {
	return len(m.rules)
}

func conditionMet(value float64, condition string, threshold float64) bool {
	switch condition {
	case ">=":
		return value >= threshold
	case ">":
		return value > threshold
	case "<=":
		return value <= threshold
	case "<":
		return value < threshold
	case "==":
		return value == threshold
	default:
		return false
	}
}

// --- Window Counter ---

// WindowCounter is a sliding window counter using a circular buffer.
type WindowCounter struct {
	mu       sync.Mutex
	buckets  []float64
	interval time.Duration // per-bucket interval
	nBuckets int
	cursor   int
	lastTick time.Time
}

// NewWindowCounter creates a sliding window counter.
func NewWindowCounter(window time.Duration, nBuckets int) *WindowCounter {
	if nBuckets <= 0 {
		nBuckets = 60
	}
	return &WindowCounter{
		buckets:  make([]float64, nBuckets),
		interval: window / time.Duration(nBuckets),
		nBuckets: nBuckets,
		lastTick: time.Now(),
	}
}

// Record adds a value to the current bucket.
func (w *WindowCounter) Record(value float64) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.advance()
	w.buckets[w.cursor] += value
}

// Sum returns the total across all buckets.
func (w *WindowCounter) Sum() float64 {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.advance()
	var total float64
	for _, v := range w.buckets {
		total += v
	}
	return total
}

// advance moves the cursor forward, zeroing stale buckets.
// Snaps lastTick to tick boundary to prevent drift accumulation.
func (w *WindowCounter) advance() {
	now := time.Now()
	elapsed := now.Sub(w.lastTick)
	ticks := int(elapsed / w.interval)
	if ticks <= 0 {
		return
	}
	if ticks > w.nBuckets {
		ticks = w.nBuckets
	}
	for i := 0; i < ticks; i++ {
		w.cursor = (w.cursor + 1) % w.nBuckets
		w.buckets[w.cursor] = 0
	}
	// Snap to tick boundary instead of using wall clock to prevent drift.
	w.lastTick = w.lastTick.Add(time.Duration(ticks) * w.interval)
}

// --- Channels ---

// WebhookChannel sends alerts via HTTP POST.
type WebhookChannel struct {
	url     string
	headers map[string]string
	client  *http.Client
}

// NewWebhookChannel creates a webhook alert channel.
func NewWebhookChannel(url string, headers map[string]string) *WebhookChannel {
	return &WebhookChannel{
		url:     url,
		headers: headers,
		client:  &http.Client{Timeout: 10 * time.Second},
	}
}

func (c *WebhookChannel) Send(alert Alert) error {
	body, err := json.Marshal(alert)
	if err != nil {
		return fmt.Errorf("marshal alert: %w", err)
	}
	req, err := http.NewRequest("POST", c.url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range c.headers {
		req.Header.Set(k, v)
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return fmt.Errorf("send webhook: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("webhook returned %d", resp.StatusCode)
	}
	return nil
}

// SlackChannel sends alerts to a Slack webhook.
type SlackChannel struct {
	url    string
	client *http.Client
}

// NewSlackChannel creates a Slack alert channel.
func NewSlackChannel(url string) *SlackChannel {
	return &SlackChannel{
		url:    url,
		client: &http.Client{Timeout: 10 * time.Second},
	}
}

func (c *SlackChannel) Send(alert Alert) error {
	text := fmt.Sprintf(":rotating_light: *Alert: %s*\nMetric: `%s` = %.2f (threshold: %s %.2f)\nWindow: %s | Fired: %s",
		alert.Name, alert.Metric, alert.Value, alert.Condition, alert.Threshold, alert.Window, alert.FiredAt.Format(time.RFC3339))

	payload := map[string]string{"text": text}
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal slack payload: %w", err)
	}

	resp, err := c.client.Post(c.url, "application/json", bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("send slack: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("slack returned %d", resp.StatusCode)
	}
	return nil
}

// LogChannel writes alerts to structured log output.
type LogChannel struct{}

func (c *LogChannel) Send(alert Alert) error {
	slog.Warn("alert fired",
		"alert", alert.Name,
		"metric", alert.Metric,
		"value", alert.Value,
		"threshold", alert.Threshold,
		"condition", alert.Condition,
		"window", alert.Window,
	)
	return nil
}
