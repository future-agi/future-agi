import { useEffect, useState } from "react";

import { useRunSkillChecks } from "src/api/skills";

export default function useDraftChecks(files, name, delay = 600, enabled = true) {
  const { mutateAsync } = useRunSkillChecks();
  const [result, setResult] = useState(null);
  const [previous, setPrevious] = useState(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled) return undefined;
    let active = true;
    const timer = setTimeout(() => {
      mutateAsync({ files, name }).then(
        (checks) => {
          if (active) {
            setResult({ files, name, attempt, checks, error: null });
            setPrevious({ name, checks });
          }
        },
        (error) => { if (active) setResult({ files, name, attempt, checks: null, error }); },
      );
    }, delay);
    return () => { active = false; clearTimeout(timer); };
  }, [files, name, delay, attempt, mutateAsync, enabled]);

  const settled = result?.files === files && result?.name === name && result?.attempt === attempt;
  return {
    checks: settled ? result.checks : null,
    previous: previous?.name === name ? previous.checks : null,
    current: Boolean(settled && result.checks && !result.error),
    error: settled ? result.error : null,
    pending: !settled,
    retry: () => setAttempt((value) => value + 1),
  };
}
