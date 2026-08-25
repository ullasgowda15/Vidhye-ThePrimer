import { useState } from 'react';
import { Link } from 'react-router-dom';

import { errorMessage } from '@/lib/api/client';
import {
  useChildren,
  useDeleteAccount,
  useDeleteChild,
  useExportAccount,
  usePrivacyDisclosure,
} from '@/lib/api/queries';

import { DashboardShell } from './DashboardShell';
import {
  LINK,
  Card,
  Field,
  Notice,
  PasswordInput,
  PrimaryButton,
  QuietButton,
  Section,
  TextInput,
} from './ui';

/**
 * The privacy centre (PRODUCT.md §12.8, §13.7, §13.8).
 *
 * Three things, in the order a parent needs them: what we do with their child's
 * data, a copy of all of it, and the way to destroy it.
 *
 * **The disclosure text comes from the server** (`apps/accounts/privacy.py`) and is
 * not written in this file. Every sentence in it is a claim about what the backend
 * does, and a claim rendered from a hard-coded constant in a React page is a claim
 * that drifts the first time somebody adds a provider. The page renders whatever
 * the API says, including its version.
 *
 * **The two destructive actions are deliberately unequal.** Deleting a child needs
 * their name typed; deleting the account needs the password. That is not
 * escalating friction for its own sake — a mis-tap and a stranger holding an
 * unlocked tablet are different threats, and only the second one is stopped by a
 * credential. Neither has an undo, and the page says so before the button rather
 * than after it.
 *
 * **No dark patterns, in either direction** (§2.6). Deletion is not hidden behind
 * a support email, is not preceded by a retention offer, and the confirm button is
 * not styled quieter than the cancel. Equally, nothing here nags a parent toward
 * deleting; the page states what each action does and stops.
 */
export default function Privacy() {
  const { data: disclosure, isLoading, error } = usePrivacyDisclosure();

  return (
    <DashboardShell
      title="Privacy"
      subtitle="What we hold, what we do with it, and how to take it back."
    >
      <ExportCard />
      <DeleteChildSection />
      <DeleteAccountCard />

      <Section title="What we do with your family's data" isLoading={isLoading} error={error}>
        {disclosure ? (
          <div className="grid gap-4">
            <p className="text-3">{disclosure.headline}</p>
            {disclosure.sections.map((section) => (
              <Card key={section.id}>
                <h3 className="text-3 font-semibold">{section.title}</h3>
                {/* Split on the blank lines the server writes, so paragraphs are
                    paragraphs. Rendered as text, never as HTML — this is copy
                    about trustworthiness and it will not be the thing that
                    introduces an injection sink. */}
                {section.body.split('\n\n').map((paragraph) => (
                  <p key={paragraph.slice(0, 32)} className="mt-2 text-2 text-text-muted">
                    {paragraph}
                  </p>
                ))}
              </Card>
            ))}
            <p className="text-1 text-text-muted">
              This statement was last changed on {disclosure.version}.
            </p>
          </div>
        ) : null}
      </Section>
    </DashboardShell>
  );
}

function ExportCard() {
  const exportAccount = useExportAccount();

  return (
    <Section title="Take a copy">
      <Card>
        <p className="text-2 text-text-muted">
          One file with everything: your account, each child&rsquo;s profile and settings, their
          companion, every story word for word with what they chose, everything the companion
          remembers, their progress, and every report. Our own safety refusals are in it too.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <PrimaryButton
            type="button"
            onClick={() => exportAccount.mutate()}
            disabled={exportAccount.isPending}
            data-testid="export-account"
          >
            {exportAccount.isPending ? 'Preparing…' : 'Download my data'}
          </PrimaryButton>
          {exportAccount.isSuccess ? (
            <span className="text-2 text-success">Saved as {exportAccount.data}.</span>
          ) : null}
        </div>
        {exportAccount.error ? (
          <Notice>{errorMessage(exportAccount.error, 'We could not build the file.')}</Notice>
        ) : null}
      </Card>
    </Section>
  );
}

/**
 * Delete one child (§12.8).
 *
 * Typing the name, rather than a checkbox: this is the control a parent with two
 * children reaches for, and the whole risk is deleting the wrong one. A
 * confirmation that says "yes" cannot distinguish those; one that says the name
 * can.
 */
function DeleteChildSection() {
  const { data: children, isLoading, error } = useChildren();
  const remove = useDeleteChild();
  const [pending, setPending] = useState<string | null>(null);
  const [typed, setTyped] = useState('');

  return (
    <Section
      title="Delete a child's data"
      isLoading={isLoading}
      error={error}
      isEmpty={children?.length === 0}
      emptyText="No children on this account yet."
    >
      <ul className="grid gap-4">
        {(children ?? []).map((child) => (
          <li key={child.id}>
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h3 className="text-3 font-semibold">{child.first_name}</h3>
                  <p className="mt-1 text-2 text-text-muted">
                    Their profile, companion, every story, every memory, every report, and every
                    picture and recording we made for them. This cannot be undone.
                  </p>
                </div>
                {pending === child.id ? null : (
                  <QuietButton
                    type="button"
                    onClick={() => {
                      setPending(child.id);
                      setTyped('');
                    }}
                    data-testid={`delete-child-${child.id}`}
                  >
                    Delete
                  </QuietButton>
                )}
              </div>

              {pending === child.id ? (
                <div className="mt-4 border-t border-border pt-4">
                  <Field
                    label={`Type ${child.first_name} to confirm`}
                    hint="Everything about them goes immediately. There is no undo and no grace period."
                  >
                    <TextInput
                      value={typed}
                      autoFocus
                      data-testid="confirm-child-name"
                      onChange={(event) => setTyped(event.target.value)}
                    />
                  </Field>
                  <div className="mt-3 flex flex-wrap gap-3">
                    <PrimaryButton
                      type="button"
                      disabled={
                        typed.trim().toLowerCase() !== child.first_name.toLowerCase() ||
                        remove.isPending
                      }
                      onClick={() => remove.mutate(child.id, { onSuccess: () => setPending(null) })}
                      data-testid="confirm-delete-child"
                    >
                      Delete {child.first_name}&rsquo;s data
                    </PrimaryButton>
                    <QuietButton type="button" onClick={() => setPending(null)}>
                      Cancel
                    </QuietButton>
                  </div>
                  {remove.error ? (
                    <Notice>{errorMessage(remove.error, 'We could not delete that.')}</Notice>
                  ) : null}
                </div>
              ) : null}
            </Card>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function DeleteAccountCard() {
  const remove = useDeleteAccount();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState('');

  return (
    <Section title="Delete your account">
      <Card>
        <p className="text-2 text-text-muted">
          Your account, every child on it, and everything above. Immediate and irreversible — take a
          copy first if you want one.
        </p>

        {open ? (
          <div className="mt-4 border-t border-border pt-4">
            <Field
              label="Your password"
              hint="Asked for because a signed-in device on a kitchen table should not be enough to erase this."
            >
              <PasswordInput
                value={password}
                autoFocus
                autoComplete="current-password"
                data-testid="confirm-account-password"
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>
            <div className="mt-3 flex flex-wrap gap-3">
              <PrimaryButton
                type="button"
                disabled={!password || remove.isPending}
                onClick={() => remove.mutate(password)}
                data-testid="confirm-delete-account"
              >
                Delete everything
              </PrimaryButton>
              <QuietButton type="button" onClick={() => setOpen(false)}>
                Cancel
              </QuietButton>
            </div>
            {remove.error ? (
              <Notice>{errorMessage(remove.error, 'We could not delete the account.')}</Notice>
            ) : null}
          </div>
        ) : (
          <div className="mt-4">
            <QuietButton type="button" onClick={() => setOpen(true)} data-testid="delete-account">
              Delete my account
            </QuietButton>
          </div>
        )}

        <p className="mt-4 text-1 text-text-muted">
          Changed your mind about something smaller? Each child&rsquo;s limits and topics live in{' '}
          <Link to="/parent/children" className={LINK}>
            Children
          </Link>
          .
        </p>
      </Card>
    </Section>
  );
}
