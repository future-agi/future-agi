import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ContextLabelRequestPanel from './ContextLabelRequestPanel';
import { useContextDetail } from 'src/api/skills/context-authoring';

// UXF-14: the Versions row action reads "Request release for v2".

vi.mock('src/api/skills/context-authoring', () => ({ useContextDetail: vi.fn() }));
afterEach(cleanup);

it('names the release request with its version', () => {
  useContextDetail.mockReturnValue({ data: { context: { name: 'support-agent' }, releaseState: { labels: [{ name: 'production', version: 1 }] } } });
  render(<MemoryRouter><ContextLabelRequestPanel contextId="context" version={{ id: 'version', digest: 'sha256:x', number: 2 }} /></MemoryRouter>);
  expect(screen.getByRole('button', { name: 'Request release for v2' })).toBeEnabled();
  expect(screen.queryByText(/Request release review/)).not.toBeInTheDocument();
});
