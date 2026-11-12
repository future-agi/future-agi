package capmock

import "time"

// job is an async vendor job (Azure DI analyze, AssemblyAI transcript, fal
// queue request). It is queued for the first half of its job_delay, running
// for the second, then finished: succeeded, or failed when job_fail_at said
// so.
type job struct {
	id      string
	created time.Time
	delay   time.Duration
	failed  bool
	expiry  time.Duration     // url_expiry for the files its result mints
	vars    map[string]string // fixture values fixed at creation
}

// Job phases, which each fake maps to its vendor's status words.
const (
	phaseQueued    = "queued"
	phaseRunning   = "running"
	phaseSucceeded = "succeeded"
	phaseFailed    = "failed"
)

func (j *job) phase(now time.Time) string {
	elapsed := now.Sub(j.created)
	switch {
	case elapsed < j.delay/2:
		return phaseQueued
	case elapsed < j.delay:
		return phaseRunning
	case j.failed:
		return phaseFailed
	}
	return phaseSucceeded
}

// newJob starts a job with the call's job_delay and job_fail_at.
func (m *Mock) newJob(c *call, vars map[string]string) *job {
	j := &job{id: c.id, created: m.now(), delay: c.b.jobDelay, failed: c.b.jobFailAt != "", expiry: c.b.urlExpiry, vars: vars}
	m.mu.Lock()
	m.jobs[j.id] = j
	m.mu.Unlock()
	return j
}

func (m *Mock) job(id string) (*job, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	j, ok := m.jobs[id]
	return j, ok
}

// session is a hosted-browser session (Kernel, Browserbase).
type session struct {
	id      string
	created time.Time
	ended   time.Time // zero while running
}

func (m *Mock) newSession(id string) *session {
	s := &session{id: id, created: m.now()}
	m.mu.Lock()
	m.sessions[id] = s
	m.mu.Unlock()
	return s
}

func (m *Mock) session(id string) (*session, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	s, ok := m.sessions[id]
	return s, ok
}

// endSession releases a session; it reports false for an unknown one.
func (m *Mock) endSession(id string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	s, ok := m.sessions[id]
	if ok && s.ended.IsZero() {
		s.ended = m.now()
	}
	return ok
}

func (m *Mock) deleteSession(id string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	_, ok := m.sessions[id]
	delete(m.sessions, id)
	return ok
}
