import { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";

import { useSkillFile, useSkillVersion } from "src/api/skills";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";

function FileLoader({ skillId, number, path, onLoad }) {
  const query = useSkillFile(skillId, number, path);
  useEffect(() => {
    if (query.data) onLoad(path, query.data.text);
  }, [path, query.data, onLoad]);
  return query.isError ? (
    <ErrorState title={`Couldn't load ${path}`} onRetry={query.refetch} />
  ) : null;
}

FileLoader.propTypes = {
  skillId: PropTypes.string.isRequired,
  number: PropTypes.number.isRequired,
  path: PropTypes.string.isRequired,
  onLoad: PropTypes.func.isRequired,
};

/** Load every chunk of every file before allowing the first draft save. */
export default function VersionDraftLoader({ skillId, number, children }) {
  const query = useSkillVersion(skillId, number);
  const [contents, setContents] = useState({});
  const onLoad = useCallback((path, content) => {
    setContents((previous) => previous[path] === content ? previous : { ...previous, [path]: content });
  }, []);
  if (query.isError) return <ErrorState title="Couldn't load the latest version" onRetry={query.refetch} />;
  if (!query.data) return <ContentSkeleton variant="panel" label="Loading version files" />;
  const ready = query.data.files.every(({ path }) => Object.hasOwn(contents, path));
  if (ready) {
    return children({
      baseVersion: number,
      files: query.data.files.map(({ path }) => ({ path, content: contents[path] })),
      note: "",
      revision: 0,
    });
  }
  return (
    <>
      <ContentSkeleton variant="panel" label="Loading version files" />
      {query.data.files.map(({ path }) => (
        <FileLoader key={path} skillId={skillId} number={number} path={path} onLoad={onLoad} />
      ))}
    </>
  );
}

VersionDraftLoader.propTypes = {
  skillId: PropTypes.string.isRequired,
  number: PropTypes.number.isRequired,
  children: PropTypes.func.isRequired,
};
