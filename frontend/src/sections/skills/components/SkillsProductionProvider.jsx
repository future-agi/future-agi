import PropTypes from 'prop-types';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { contextAuthoringReady } from 'src/api/skills/context-authoring';
import { contextInspectionReady } from 'src/api/skills/context-production';
import { reviewsReady } from 'src/api/skills/review-production';
import { ContextAuthoringProvider } from '../contexts/ContextLibraryView';
import ContextAuthoringController from '../contexts/ContextAuthoringController';
import ContextDetailController from '../contexts/ContextDetailController';
import { ReviewInboxController } from '../review/SkillsReviewControllers';

// The production seam owns real controllers, never placeholder availability.
// Current authentication and generated server contracts are both prerequisites.
export default function SkillsProductionProvider({ children }) {
  const access = useSkillV11Scope();
  const enabled = access.enabled && contextAuthoringReady();
  const bindings = {
    ...(enabled ? {
      library: { element: <ContextAuthoringController mode="library" /> },
      editor: { scopeKey: access.identity, element: <ContextAuthoringController mode="editor" /> },
      ...(contextInspectionReady() ? { detail: { element: <ContextDetailController /> } } : {}),
    } : {}),
    ...(access.enabled && reviewsReady() ? { reviews: { Controller: ReviewInboxController } } : {}),
  };
  return <ContextAuthoringProvider key={access.identity} scopeKey={access.identity} bindings={bindings}>{children}</ContextAuthoringProvider>;
}
SkillsProductionProvider.propTypes = { children: PropTypes.node };
