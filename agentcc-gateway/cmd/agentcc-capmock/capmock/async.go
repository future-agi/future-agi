package capmock

import (
	"net/http"
	"strings"
	"time"
)

// --- Azure AI Document Intelligence: 202 with Operation-Location, then poll.

func azureAnalyze(m *Mock, c *call) {
	model, ok := strings.CutSuffix(c.r.PathValue("op"), ":analyze")
	if !ok || model == "" {
		writeJSON(c.w, http.StatusNotFound, map[string]any{"error": map[string]string{"code": "NotFound", "message": "unknown operation"}})
		return
	}
	version := c.r.URL.Query().Get("api-version")
	if version == "" {
		version = "2024-11-30"
	}
	j := m.newJob(c, map[string]string{"model": model, "created": m.now().UTC().Format(time.RFC3339)})
	c.w.Header().Set("Operation-Location", m.baseURL()+"/azure-di/documentintelligence/documentModels/"+model+
		"/analyzeResults/"+j.id+"?api-version="+version)
	c.w.Header().Set("apim-request-id", j.id)
	c.w.WriteHeader(http.StatusAccepted)
}

func azureResult(m *Mock, c *call) {
	j, ok := m.job(c.r.PathValue("id"))
	if !ok {
		writeJSON(c.w, http.StatusNotFound, map[string]any{"error": map[string]string{"code": "NotFound", "message": "no such analyze result"}})
		return
	}
	vars := m.jobVars(j)
	head := map[string]any{"createdDateTime": vars["created"], "lastUpdatedDateTime": m.now().UTC().Format(time.RFC3339)}
	switch j.phase(m.now()) {
	case phaseQueued, phaseRunning:
		head["status"] = map[string]string{phaseQueued: "notStarted", phaseRunning: "running"}[j.phase(m.now())]
		c.w.Header().Set("Retry-After", "1")
		m.respond(c, http.StatusOK, head)
	case phaseFailed:
		head["status"] = "failed"
		head["error"] = map[string]string{"code": "InternalServerError", "message": "capmock: the job failed (job_fail_at)"}
		m.respond(c, http.StatusOK, head)
	default:
		m.render(c, http.StatusOK, "azure_di_result.json", vars)
	}
}

// --- AssemblyAI: upload, transcript queued, poll until completed or error.

func assemblyUpload(m *Mock, c *call) {
	url := m.mintFile(c.id, c.body, "application/octet-stream", c.b.urlExpiry)
	m.respond(c, http.StatusOK, map[string]any{"upload_url": url})
}

func assemblyCreate(m *Mock, c *call) {
	audio, _ := c.json()["audio_url"].(string)
	if audio == "" {
		writeJSON(c.w, http.StatusBadRequest, map[string]string{"error": "audio_url is required"})
		return
	}
	j := m.newJob(c, map[string]string{"audio_url": audio})
	m.respond(c, http.StatusOK, map[string]any{"id": j.id, "status": "queued", "audio_url": audio, "text": nil})
}

func assemblyPoll(m *Mock, c *call) {
	j, ok := m.job(c.r.PathValue("id"))
	if !ok {
		writeJSON(c.w, http.StatusNotFound, map[string]string{"error": "Transcript not found"})
		return
	}
	audio := m.jobVars(j)["audio_url"]
	switch phase := j.phase(m.now()); phase {
	case phaseQueued, phaseRunning:
		status := map[string]string{phaseQueued: "queued", phaseRunning: "processing"}[phase]
		m.respond(c, http.StatusOK, map[string]any{"id": j.id, "status": status, "audio_url": audio, "text": nil})
	case phaseFailed:
		m.respond(c, http.StatusOK, map[string]any{"id": j.id, "status": "error", "audio_url": audio,
			"error": "capmock: the transcript failed (job_fail_at)"})
	default:
		m.render(c, http.StatusOK, "assemblyai_transcript.json", map[string]string{"id": j.id, "audio_url": audio})
	}
}

// --- fal queue: submit, status, result (fal's queue.fal.run paths).

func falSubmit(m *Mock, c *call) {
	model := c.r.PathValue("model")
	prompt, _ := c.json()["prompt"].(string)
	j := m.newJob(c, map[string]string{"model": model, "prompt": prompt})
	base := m.baseURL() + "/fal/" + model + "/requests/" + j.id
	m.respond(c, http.StatusOK, map[string]any{"request_id": j.id, "response_url": base,
		"status_url": base + "/status", "cancel_url": base + "/cancel", "queue_position": 0})
}

func falGet(m *Mock, c *call) {
	path := c.r.PathValue("path")
	i := strings.LastIndex(path, "/requests/")
	if i < 0 {
		writeJSON(c.w, http.StatusNotFound, map[string]string{"detail": "Not Found"})
		return
	}
	id, rest, _ := strings.Cut(path[i+len("/requests/"):], "/")
	j, ok := m.job(id)
	if !ok || (rest != "" && rest != "status") {
		writeJSON(c.w, http.StatusNotFound, map[string]string{"detail": "Request not found"})
		return
	}
	phase := j.phase(m.now())
	if rest == "status" {
		switch phase {
		case phaseQueued:
			m.respond(c, http.StatusOK, map[string]any{"status": "IN_QUEUE", "queue_position": 0})
		case phaseRunning:
			m.respond(c, http.StatusOK, map[string]any{"status": "IN_PROGRESS", "logs": []any{}})
		case phaseFailed:
			m.respond(c, http.StatusOK, map[string]any{"status": "COMPLETED", "logs": []any{}, "error": "capmock: the request failed (job_fail_at)"})
		default:
			m.respond(c, http.StatusOK, map[string]any{"status": "COMPLETED", "logs": []any{}, "metrics": map[string]float64{"inference_time": 1.2}})
		}
		return
	}
	switch phase {
	case phaseQueued, phaseRunning:
		writeJSON(c.w, http.StatusBadRequest, map[string]string{"detail": "Request is still in progress"})
	case phaseFailed:
		writeJSON(c.w, http.StatusInternalServerError, map[string]string{"detail": "capmock: the request failed (job_fail_at)"})
	default:
		m.render(c, http.StatusOK, "fal_result.json", map[string]string{"prompt": m.jobVars(j)["prompt"], "file_url": m.jobFile(j)})
	}
}

// jobFile mints a job's result file once, so every fetch returns one URL.
func (m *Mock) jobFile(j *job) string {
	m.mu.Lock()
	url, ok := j.vars["file_url"]
	m.mu.Unlock()
	if ok {
		return url
	}
	url = m.mintFile(j.id+"-out", []byte("capmock image bytes"), "image/png", j.expiry)
	m.mu.Lock()
	j.vars["file_url"] = url
	m.mu.Unlock()
	return url
}

// jobVars copies a job's fixture values.
func (m *Mock) jobVars(j *job) map[string]string {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make(map[string]string, len(j.vars))
	for k, v := range j.vars {
		out[k] = v
	}
	return out
}
