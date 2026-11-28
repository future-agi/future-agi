import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import ContextTargetRegistration from './ContextTargetRegistration';
import { useCreateContextBinding } from 'src/api/skills/context-consumers';

vi.mock('src/api/skills/context-consumers', () => ({ useCreateContextBinding: vi.fn() }));
const mutateAsync = vi.fn();
const manifest = { code_commit: 'b'.repeat(40), entrypoint: 'app:invoke', image_digest: `sha256:${'a'.repeat(64)}`, runtime_version: '3.11', tools_digest: `sha256:${'c'.repeat(64)}`, side_effect_policy: 'fixture_only' };
beforeEach(() => {
  vi.clearAllMocks();
  useCreateContextBinding.mockReturnValue({ mutateAsync, isPending: false });
  mutateAsync.mockResolvedValue({ id: 'binding', name: 'Refund agent', revision: 1 });
});
afterEach(cleanup);
const choose = (value) => {
  const file = new File([typeof value === 'string' ? value : JSON.stringify(value)], 'manifest.json', { type: 'application/json' });
  fireEvent.change(screen.getByLabelText('Config file'), { target: { files: [file] } });
};

it('registers one runtime target with the build manifest, never a typed digest', async () => {
  render(<ContextTargetRegistration contextId="context" canRegister />);
  fireEvent.click(screen.getByRole('button', { name: 'Connect an app' }));
  const submit = screen.getByRole('button', { name: 'Connect app' });
  expect(submit).toBeDisabled();
  fireEvent.change(screen.getByLabelText('App name'), { target: { value: 'Refund agent' } });
  choose(manifest);
  await screen.findByText(manifest.entrypoint);
  fireEvent.click(submit);
  await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
  const input = mutateAsync.mock.calls[0][0];
  expect(input).toMatchObject({ name: 'Refund agent', kind: 'runtime', selector: { label: 'production' }, updateMode: 'new_thread', configuration: manifest, repository: null });
  expect(input).not.toHaveProperty('configurationDigest');
  expect(await screen.findByText('Registered Refund agent · configuration r1')).toBeVisible();
});

it('rejects a manifest that is not a JSON object', async () => {
  render(<ContextTargetRegistration contextId="context" canRegister />);
  fireEvent.click(screen.getByRole('button', { name: 'Connect an app' }));
  fireEvent.change(screen.getByLabelText('App name'), { target: { value: 'Refund agent' } });
  choose('[1, 2]');
  expect(await screen.findByText(/not a JSON object/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Connect app' })).toBeDisabled();
});

it('defaults the target name from the manifest entrypoint without overriding a typed name', async () => {
  render(<ContextTargetRegistration contextId="context" canRegister />);
  fireEvent.click(screen.getByRole('button', { name: 'Connect an app' }));
  choose({ ...manifest, entrypoint: 'refund_agent.app:invoke' });
  await screen.findByText('refund_agent.app:invoke');
  expect(screen.getByLabelText('App name')).toHaveValue('refund_agent');
  fireEvent.click(screen.getByRole('button', { name: 'Connect app' }));
  await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ name: 'refund_agent' })));
  cleanup();
  render(<ContextTargetRegistration contextId="context" canRegister />);
  fireEvent.click(screen.getByRole('button', { name: 'Connect an app' }));
  fireEvent.change(screen.getByLabelText('App name'), { target: { value: 'Refund agent' } });
  choose({ ...manifest, entrypoint: 'refund_agent.app:invoke' });
  await screen.findByText('refund_agent.app:invoke');
  expect(screen.getByLabelText('App name')).toHaveValue('Refund agent');
});

it('cannot be opened without writer permission', () => {
  render(<ContextTargetRegistration contextId="context" canRegister={false} />);
  expect(screen.getByRole('button', { name: 'Connect an app' })).toBeDisabled();
});

it('says what to do once before a config file is chosen, and keeps the reason on the disabled primary', () => {
  render(<ContextTargetRegistration contextId="context" canRegister />);
  fireEvent.click(screen.getByRole('button', { name: 'Connect an app' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText('Connect an app')).toBeVisible();
  expect(within(dialog).getByText("Upload your app's config file.")).toBeVisible();
  expect(within(dialog).queryByText(/\(JSON\)\./)).toBeNull();
  // The footer does not repeat the instruction. It says what connecting does, beside Cancel and Connect app,
  // and the disabled primary still carries its reason for assistive technology.
  const submit = within(dialog).getByRole('button', { name: 'Connect app' });
  const note = within(dialog).getByText("Connecting doesn't change production.");
  expect(note).toBeVisible();
  expect(note.parentElement).toBe(submit.parentElement.parentElement);
  expect(submit).toBeDisabled();
  expect(submit).toHaveAccessibleDescription("Choose your app's config file.");
  expect(screen.getByRole('group', { name: 'Config file (JSON)' })).toBeVisible();
  expect(screen.queryByText(/manifest/i)).toBeNull();
});

it('starts connecting by opening the config file chooser, and keeps the dialog for a cancelled choice', () => {
  const click = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {});
  render(<ContextTargetRegistration contextId="context" canRegister />);
  fireEvent.click(screen.getByRole('button', { name: 'Connect an app' }));
  expect(click).toHaveBeenCalledTimes(1);
  expect(click.mock.contexts[0]).toBe(screen.getByLabelText('Config file'));
  expect(screen.getByRole('button', { name: 'Choose config file' })).toBeVisible();
  click.mockRestore();
});
