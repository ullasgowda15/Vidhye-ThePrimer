/**
 * Create-account form, shared by the standalone `/parent/signup` screen and
 * step one of onboarding (§4.2). One form, so the two can never drift.
 */

import { useState } from 'react';

import { errorMessage } from '@/lib/api/client';
import { useAuthStore } from '@/store/auth';

import { Field, Notice, PrimaryButton, TextInput } from './ui';

export default function AccountForm({ onDone }: { onDone: () => void }) {
  const signup = useAuthStore((state) => state.signup);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await signup({ email, password, display_name: displayName });
      onDone();
    } catch (caught) {
      setError(errorMessage(caught, 'We could not create that account.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Your name" hint="What the companion calls you. Optional.">
        <TextInput
          name="display_name"
          autoComplete="name"
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
        />
      </Field>

      <Field label="Email">
        <TextInput
          name="email"
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </Field>

      <Field label="Password" hint="At least eight characters, and not a common one.">
        <TextInput
          name="password"
          type="password"
          required
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </Field>

      <PrimaryButton type="submit" disabled={busy}>
        {busy ? 'Creating…' : 'Create account'}
      </PrimaryButton>

      <Notice>{error}</Notice>
    </form>
  );
}
