import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, sqlApi } from "./api";

const isActive = (job) => job && ["queued", "running"].includes(job.state);

function admissionDelay(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Submission cancelled", "AbortError"));
    };
    if (signal.aborted) {
      reject(new DOMException("Submission cancelled", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export default function useSQLExecution(
  projectId,
  {
    retryAdmission = false,
    cancelOnUnmount = false,
    admissionTimeoutMs = 90_000,
  } = {},
) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [waitingForAdmission, setWaitingForAdmission] = useState(false);
  const generation = useRef(0);
  const submission = useRef(null);
  const activeJob = useRef(null);
  const active = isActive(job);
  const updateJob = useCallback(
    (value) => {
      activeJob.current = isActive(value) ? { projectId, id: value.id } : null;
      setJob(value);
    },
    [projectId],
  );

  useEffect(() => {
    generation.current += 1;
    activeJob.current = null;
    setJob(null);
    setError("");
    setSubmitting(false);
    setWaitingForAdmission(false);
    return () => {
      generation.current += 1;
      submission.current?.abort("unmounted");
      const current = activeJob.current;
      if (cancelOnUnmount && current) {
        void sqlApi.cancel(current.projectId, current.id).catch(() => {});
      }
    };
  }, [projectId, cancelOnUnmount]);

  useEffect(() => {
    if (!active) return undefined;
    const controller = new AbortController();
    const current = generation.current;
    const deadline = Date.now() + 120_000;
    let timer;
    const poll = async () => {
      try {
        const next = await sqlApi.result(projectId, job.id, controller.signal);
        if (!controller.signal.aborted && generation.current === current) {
          updateJob(next);
          setError("");
          if (isActive(next)) timer = setTimeout(poll, 1000);
        }
      } catch (e) {
        if (!controller.signal.aborted && generation.current === current) {
          setError(errorMessage(e));
          // Keep the id for Cancel, but do not poll an unreachable service forever.
          if (Date.now() < deadline) timer = setTimeout(poll, 3000);
        }
      }
    };
    timer = setTimeout(poll, 500);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, job?.id, projectId, updateJob]);

  const run = useCallback(
    async (body, saved = false) => {
      submission.current?.abort("superseded");
      const current = ++generation.current;
      const controller = new AbortController();
      submission.current = controller;
      const previous = activeJob.current;
      activeJob.current = null;
      setSubmitting(true);
      setWaitingForAdmission(false);
      setJob(null);
      setError("");
      const deadline =
        Date.now() + Math.min(90_000, Math.max(1, admissionTimeoutMs));
      try {
        if (previous) await sqlApi.cancel(previous.projectId, previous.id);
        for (let attempt = 0; ; attempt += 1) {
          if (controller.signal.aborted) return;
          try {
            const result = await (saved ? sqlApi.runSaved : sqlApi.run)(
              { ...body, project_id: projectId },
              { signal: controller.signal },
            );
            if (generation.current === current && !controller.signal.aborted) {
              setWaitingForAdmission(false);
              updateJob(result);
            } else if (
              isActive(result) &&
              (cancelOnUnmount || controller.signal.reason !== "unmounted")
            ) {
              // A POST can be accepted just before the client cancels its wait.
              void sqlApi.cancel(projectId, result.id).catch(() => {});
            }
            return;
          } catch (e) {
            const status = e?.statusCode ?? e?.response?.status;
            const remaining = deadline - Date.now();
            if (
              !retryAdmission ||
              status !== 429 ||
              remaining <= 0 ||
              controller.signal.aborted
            )
              throw e;
            setWaitingForAdmission(true);
            await admissionDelay(
              Math.min(1000 * 2 ** Math.min(attempt, 3), 5000, remaining),
              controller.signal,
            );
            if (Date.now() >= deadline) throw e;
          }
        }
      } catch (e) {
        if (!controller.signal.aborted && generation.current === current)
          setError(errorMessage(e));
      } finally {
        if (generation.current === current) {
          setSubmitting(false);
          setWaitingForAdmission(false);
        }
      }
    },
    [projectId, updateJob, retryAdmission, cancelOnUnmount, admissionTimeoutMs],
  );

  const cancel = useCallback(async () => {
    submission.current?.abort("cancelled");
    setSubmitting(false);
    setWaitingForAdmission(false);
    const target = activeJob.current;
    if (!target) {
      // Invalidate a submission that has not produced a cancellable job yet.
      generation.current += 1;
      return;
    }
    const current = generation.current;
    try {
      const result = await sqlApi.cancel(target.projectId, target.id);
      if (generation.current === current) updateJob(result);
    } catch (e) {
      // Keep the polling generation alive when cancellation fails transiently.
      // The job may still complete, and its result must not leave the UI busy.
      if (generation.current === current) setError(errorMessage(e));
    }
  }, [updateJob]);

  return {
    job,
    error,
    waitingForAdmission,
    busy: submitting || active,
    run,
    cancel,
  };
}
