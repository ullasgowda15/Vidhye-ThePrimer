import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { errorMessage } from '@/lib/api/client';
import { useAuthStore } from '@/store/auth';

import { LINK, Field, Notice, ParentShell, PasswordInput, PrimaryButton, TextInput } from './ui';

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const login = useAuthStore((state) => state.login);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Come back to wherever they were headed before the redirect.
  const destination = (location.state as { from?: string } | null)?.from ?? '/parent';

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
      navigate(destination, { replace: true });
    } catch (caught) {
      setError(errorMessage(caught, 'That email and password did not match.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <ParentShell title="Welcome back">
      <form onSubmit={submit} className="space-y-4">
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

        <Field label="Password">
          <PasswordInput
            name="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>

        <PrimaryButton type="submit" disabled={busy}>
          {busy ? 'Logging in…' : 'Log in'}
        </PrimaryButton>

        <Notice>{error}</Notice>
      </form>

      <p className="mt-5 text-2 text-text-muted">
        New here?{' '}
        <Link to="/parent/signup" className={LINK}>
          Create an account
        </Link>
      </p>
    </ParentShell>
  );
}
