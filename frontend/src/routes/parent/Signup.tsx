import { Link, useNavigate } from 'react-router-dom';

import AccountForm from './AccountForm';
import { LINK, ParentShell } from './ui';

/** Standalone signup. Onboarding reuses the same form as its first step (§4.2). */
export default function Signup() {
  const navigate = useNavigate();

  return (
    <ParentShell title="Create your account" subtitle="Two minutes, then hand the tablet over.">
      <AccountForm onDone={() => navigate('/parent/onboarding', { replace: true })} />

      <p className="mt-5 text-2 text-text-muted">
        Already have one?{' '}
        <Link to="/parent/login" className={LINK}>
          Log in
        </Link>
      </p>
    </ParentShell>
  );
}
