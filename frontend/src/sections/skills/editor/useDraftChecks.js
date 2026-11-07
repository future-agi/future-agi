import { useEffect, useState } from "react";

import { useRunSkillChecks } from "src/api/skills";

export default function useDraftChecks(files, name, delay = 600) {
  const { mutateAsync } = useRunSkillChecks();
  const [result, setResult] = useState(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      mutateAsync({ files, name }).then(
        (checks) => { if (active) setResult({ files, checks, error: null }); },
        (error) => { if (active) setResult({ files, checks: null, error }); },
      );
    }, delay);
    return () => { active = false; clearTimeout(timer); };
  }, [files, name, delay, attempt, mutateAsync]);

  const current = result?.files === files;
  return {
    checks: current ? result.checks : null,
    error: current ? result.error : null,
    pending: !current,
    retry: () => { setResult(null); setAttempt((value) => value + 1); },
  };
}
