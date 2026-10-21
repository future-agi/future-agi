import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, sqlApi } from "./api";

export default function useSQLExecution(projectId) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const generation = useRef(0);
  const active = job && ["queued", "running"].includes(job.state);

  useEffect(() => {
    generation.current += 1;
    setJob(null);
    setError("");
    setSubmitting(false);
    return () => {
      generation.current += 1;
    };
  }, [projectId]);

  useEffect(() => {
    if (!active) return undefined;
    const controller = new AbortController();
    const current = generation.current;
    let timer;
    const poll = async () => {
      try {
        const next = await sqlApi.result(projectId, job.id, controller.signal);
        if (!controller.signal.aborted && generation.current === current) {
          setJob(next);
          if (["queued", "running"].includes(next.state))
            timer = setTimeout(poll, 1000);
        }
      } catch (e) {
        if (!controller.signal.aborted && generation.current === current) {
          setError(errorMessage(e));
          // Preserve the id so Cancel remains available after a transient failure.
          timer = setTimeout(poll, 3000);
        }
      }
    };
    timer = setTimeout(poll, 500);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, job?.id, projectId]);

  const run = useCallback(
    async (body, saved = false) => {
      const current = ++generation.current;
      setSubmitting(true);
      setJob(null);
      setError("");
      try {
        const result = await (saved ? sqlApi.runSaved : sqlApi.run)({
          ...body,
          project_id: projectId,
        });
        if (generation.current === current) setJob(result);
      } catch (e) {
        if (generation.current === current) setError(errorMessage(e));
      } finally {
        if (generation.current === current) setSubmitting(false);
      }
    },
    [projectId],
  );

  const cancel = useCallback(async () => {
    if (!job) return;
    const current = generation.current;
    try {
      const result = await sqlApi.cancel(projectId, job.id);
      if (generation.current === current) setJob(result);
    } catch (e) {
      if (generation.current === current) setError(errorMessage(e));
    }
  }, [job, projectId]);

  return { job, error, busy: submitting || active, run, cancel };
}
