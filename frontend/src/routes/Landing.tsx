import { Link } from 'react-router-dom';

import { FOCUS_RING } from './parent/ui';

/**
 * The product's front door, for a grown-up who has not met it yet (§14.2).
 *
 * `/` used to be the child's star and nothing else, which was right when the only
 * person who could arrive there was a child holding a tablet their parent had
 * already set up. It is wrong for a stranger: they got a storybook page inviting
 * them to tap a star, with no answer to "what is this and should I trust it with
 * my four-year-old?"
 *
 * So `/` now answers whichever question its visitor actually has — `ChildHome`
 * decides, and this is the half for the grown-up. A family with a child profile
 * never sees this page; they get the star, because the child is the one holding
 * the tablet.
 *
 * **It has its own palette, and that is deliberate.** The first two versions of
 * this page were built out of the Storyland tokens, and they looked washed out:
 * those colours are tuned for a *storybook page* — a small warm rectangle a child
 * holds — and spread across a full-width marketing page they turn into acres of
 * flat cream with nothing to look at. §15.11 makes themes config rather than
 * rewrites precisely so a surface can have the one it needs, so the palette below
 * is scoped to this page and leaks into nothing: deep night-sky indigo as the
 * ground, gold for the star, and the product itself glowing against the dark. It
 * is the colour of the hour this thing is actually used in.
 *
 * The illustration is all inline SVG, theme-aware where it sits inside a world
 * card, so there is no asset pipeline and nothing to go stale.
 *
 * **Written for a parent, and deliberately not sold to.** §13.6 forbids
 * child-directed pull and the same instinct belongs here: no countdowns, no
 * "join 10,000 families", no scarcity. A product that keeps a small child's
 * attention honestly cannot advertise itself dishonestly to their parent.
 */

/** Scoped to this page. Nothing here reaches the child or parent themes. */
const PALETTE = {
  '--ink': '#151234',
  '--ink-soft': '#221d4d',
  '--ink-line': '#2f2960',
  '--cream': '#fbf6ec',
  '--cream-dim': '#c9c2e0',
  '--gold': '#f5b93f',
  '--gold-soft': '#ffd98a',
  '--rose': '#ef8d76',
  '--sage': '#79bd93',
  '--lilac': '#b49df5',
} as React.CSSProperties;

export default function Landing() {
  return (
    <div style={PALETTE} className="min-h-full" data-landing>
      <a
        href="#worlds"
        className={`sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-white focus:px-4 focus:py-2 ${FOCUS_RING}`}
      >
        Skip to content
      </a>

      {/* ---------------------------------------------------------------- */}
      {/* Night: the hero                                                   */}
      {/* ---------------------------------------------------------------- */}
      <div className="relative overflow-hidden" style={{ background: 'var(--ink)' }}>
        <NightSky />

        <header className="relative mx-auto flex max-w-[72rem] items-center justify-between px-6 py-6">
          <span
            className="flex items-center gap-2 font-display text-4"
            style={{ color: 'var(--cream)' }}
          >
            <StarMark size={22} /> The Primer
          </span>
          <Link
            to="/parent"
            className={`rounded-sm font-text text-2 ${FOCUS_RING}`}
            style={{ color: 'var(--cream-dim)' }}
          >
            Parents
          </Link>
        </header>

        <section className="relative mx-auto grid max-w-[72rem] items-center gap-9 px-6 pb-10 pt-6 lg:grid-cols-[1.05fr_0.95fr]">
          <div className="text-center lg:text-left">
            <p
              className="font-text text-1 uppercase tracking-[0.22em]"
              style={{ color: 'var(--gold)' }}
            >
              A living storybook · ages 3 to 7
            </p>
            <h1
              className="mt-5 font-display text-8 leading-[1.04]"
              style={{ color: 'var(--cream)' }}
            >
              A friend who <span style={{ color: 'var(--gold-soft)' }}>remembers</span> your child
            </h1>
            <p
              className="mx-auto mt-5 max-w-[46ch] font-text text-3 leading-relaxed lg:mx-0"
              style={{ color: 'var(--cream-dim)' }}
            >
              Every evening a companion your child names and keeps tells them a story that has never
              been told before — built around what they love, and remembering what happened
              yesterday.
            </p>

            <div className="mt-8 flex flex-col items-center gap-3 sm:flex-row sm:justify-center lg:justify-start">
              <Link
                to="/parent/signup"
                className={`inline-flex min-h-target w-full items-center justify-center whitespace-nowrap rounded-full px-8 font-display text-4 shadow-card transition-transform duration-base motion-safe:hover:scale-[1.03] motion-safe:active:scale-95 sm:w-auto ${FOCUS_RING}`}
                style={{ background: 'var(--gold)', color: 'var(--ink)' }}
              >
                Start a storybook
              </Link>
              <Link
                to="/parent/login"
                className={`inline-flex min-h-target w-full items-center justify-center whitespace-nowrap rounded-full border px-7 font-text text-2 sm:w-auto ${FOCUS_RING}`}
                style={{ borderColor: 'var(--ink-line)', color: 'var(--cream-dim)' }}
              >
                I already have an account
              </Link>
            </div>
            <p className="mt-4 font-text text-1" style={{ color: 'var(--cream-dim)' }}>
              About four minutes to set up. No adverts, no streaks, ever.
            </p>
          </div>

          {/* The product itself, lit against the dark. */}
          <BeatMock />
        </section>

        <Curve />
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* Daylight: what it is                                              */}
      {/* ---------------------------------------------------------------- */}
      <div style={{ background: 'var(--cream)' }}>
        <section id="worlds" className="mx-auto max-w-[72rem] px-6 py-9">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <h2 className="font-display text-6" style={{ color: 'var(--ink)' }}>
              Somewhere to come back to
            </h2>
            <p className="max-w-[44ch] font-text text-2" style={{ color: '#5f5980' }}>
              Four worlds, each with its own light and its own inhabitants. The screen itself
              changes colour with the place your child is standing in.
            </p>
          </div>

          <ul className="mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <World world="whispering_woods" name="The Whispering Woods" note="Mossy and green" />
            <World world="star_harbor" name="Star Harbor" note="Lanterns on dark water" />
            <World world="coral_deep" name="The Coral Deep" note="Slow, bright, underwater" />
            <World world="sunlit_canyon" name="The Sunlit Canyon" note="Warm rock and wide sky" />
          </ul>
        </section>

        <section className="mx-auto max-w-[72rem] px-6 pb-10">
          <h2 className="font-display text-6" style={{ color: 'var(--ink)' }}>
            How an evening goes
          </h2>
          <ol className="mt-7 grid gap-5 md:grid-cols-3">
            <Step
              n="1"
              tint="var(--lilac)"
              title="They name their companion"
              art={<ArtCompanion />}
              body="Your child meets a companion and gives it a name. It keeps that name, that voice and that face for as long as they know each other."
            />
            <Step
              n="2"
              tint="var(--sage)"
              title="The story asks them something"
              art={<ArtChoice />}
              body="Every beat ends with something to tap. No wrong options, nothing that ends badly — whichever they pick, the world answers them."
            />
            <Step
              n="3"
              tint="var(--gold)"
              title="Tomorrow, it remembers"
              art={<ArtMemory />}
              body="Follow the river tonight and the companion still knows in the morning. It greets your child by name and mentions what they actually did."
            />
          </ol>
        </section>
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* Night again: the part parents ask about                           */}
      {/* ---------------------------------------------------------------- */}
      <div className="relative overflow-hidden" style={{ background: 'var(--ink)' }}>
        <section className="relative mx-auto max-w-[72rem] px-6 py-10">
          <h2 className="font-display text-6" style={{ color: 'var(--cream)' }}>
            Built to be handed to a small child
          </h2>
          <p
            className="mt-3 max-w-[56ch] font-text text-3 leading-relaxed"
            style={{ color: 'var(--cream-dim)' }}
          >
            These are not settings you have to go and find. They are how it works by default.
          </p>

          <ul className="mt-8 grid gap-4 sm:grid-cols-2">
            <Assurance title="Every story is checked before it is shown">
              A second, independent model reads each beat for age-appropriateness before your child
              hears it. If anything is refused they get a gentle story instead — never an error,
              never a blank screen.
            </Assurance>
            <Assurance title="You decide what never comes up">
              Name a topic to avoid and it will not appear. Set how gentle the stories stay, how
              long a sitting lasts, and which hours the story is awake.
            </Assurance>
            <Assurance title="You can read every word they were told">
              The dashboard shows each story verbatim, what your child chose, and what the safety
              layer did. Nothing is summarised away.
            </Assurance>
            <Assurance title="Your child's data is theirs">
              Memories are encrypted. Nothing trains anybody&apos;s model. Export or delete all of
              it — profile, stories, pictures, voice — whenever you like.
            </Assurance>
          </ul>
        </section>
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* The honest close                                                  */}
      {/* ---------------------------------------------------------------- */}
      <div style={{ background: 'var(--cream)' }}>
        <section className="mx-auto max-w-[56rem] px-6 py-10 text-center">
          <span className="inline-flex" aria-hidden="true">
            <StarMark size={40} />
          </span>
          <h2 className="mt-4 font-display text-6" style={{ color: 'var(--ink)' }}>
            One story an evening, and then it stops
          </h2>
          <p
            className="mx-auto mt-4 max-w-[52ch] font-text text-3 leading-relaxed"
            style={{ color: '#4b4568' }}
          >
            There is no streak to keep, nothing to unlock and no reason to hurry back. When the
            sitting ends the companion says so happily and the story lands. Coming back tomorrow
            should be your child&apos;s idea.
          </p>
          <Link
            to="/parent/signup"
            className={`mt-8 inline-flex min-h-target items-center whitespace-nowrap rounded-full px-8 font-display text-4 shadow-card transition-transform duration-base motion-safe:hover:scale-[1.03] motion-safe:active:scale-95 ${FOCUS_RING}`}
            style={{ background: 'var(--ink)', color: 'var(--cream)' }}
          >
            Start a storybook
          </Link>
        </section>

        <footer style={{ borderTop: '1px solid #e6dfd0' }}>
          <div
            className="mx-auto flex max-w-[72rem] flex-wrap items-center justify-between gap-4 px-6 py-7 font-text text-1"
            style={{ color: '#6b6486' }}
          >
            <span>The Primer — a storybook that grows with one child.</span>
            {/* Two links, to two different things, and the distinction is worth
                keeping straight. `/privacy` is the *statement* — public, no gate,
                readable before anyone has an account, which is the only moment it
                is any use in deciding whether to have one. `/parent/privacy` is
                the privacy *centre* — export, delete, the controls for an account
                that already exists — and it stays behind the adult gate and a
                login, because those controls act on real data. */}
            <span className="flex items-center gap-5">
              <Link
                to="/privacy"
                className={`rounded-sm underline underline-offset-4 ${FOCUS_RING}`}
              >
                Privacy
              </Link>
              <Link
                to="/parent"
                className={`rounded-sm underline underline-offset-4 ${FOCUS_RING}`}
              >
                Parents
              </Link>
            </span>
          </div>
        </footer>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Illustration                                                              */
/* ------------------------------------------------------------------------- */

/** Fixed star field. Deterministic, not random: a hero that reshuffles itself on
 *  every render is a hero nobody can art-direct. */
const STARS = [
  [6, 18, 1.6],
  [14, 46, 1],
  [22, 12, 2.1],
  [31, 34, 1.2],
  [39, 8, 1.4],
  [47, 27, 1.8],
  [56, 14, 1],
  [63, 40, 1.5],
  [71, 10, 2],
  [79, 31, 1.2],
  [86, 17, 1.6],
  [93, 44, 1.1],
  [11, 66, 1.3],
  [27, 74, 1],
  [42, 61, 1.5],
  [58, 70, 1.2],
  [74, 63, 1.7],
  [90, 72, 1.1],
];

function NightSky() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0">
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(120% 80% at 78% 8%, rgba(180,157,245,0.22), transparent 60%),' +
            'radial-gradient(90% 60% at 12% 0%, rgba(245,185,63,0.12), transparent 55%)',
        }}
      />
      <svg
        className="absolute inset-0 h-full w-full"
        preserveAspectRatio="none"
        viewBox="0 0 100 80"
      >
        {STARS.map(([x, y, r]) => (
          <circle
            key={`${x}-${y}`}
            cx={x}
            cy={y}
            r={r / 8}
            fill="var(--gold-soft)"
            opacity="0.55"
          />
        ))}
      </svg>
    </div>
  );
}

/** The soft transition out of the night section. */
function Curve() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 1440 80"
      preserveAspectRatio="none"
      className="relative block h-10 w-full sm:h-14"
    >
      <path d="M0 80V34c220 34 460 44 720 22S1220 8 1440 30v50z" fill="var(--cream)" />
    </svg>
  );
}

function StarMark({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 2.6l2.7 5.9 6.4.7-4.8 4.3 1.3 6.3L12 16.6 6.4 19.8l1.3-6.3L2.9 9.2l6.4-.7z"
        fill="var(--gold)"
      />
    </svg>
  );
}

/** The companion: round, soft, and never a face a child could mistake for a
 *  person. `tone` lets it sit on a light card or a dark one. */
function Companion({ size = 64, body = 'var(--lilac)' }: { size?: number; body?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <ellipse cx="32" cy="53" rx="15" ry="3.5" fill="#000" opacity="0.12" />
      <path d="M15 29c0-6 3-11 8-13-3 6-2 10 0 12z" fill={body} opacity="0.6" />
      <path d="M49 29c0-6-3-11-8-13 3 6 2 10 0 12z" fill={body} opacity="0.6" />
      <circle cx="32" cy="34" r="17" fill={body} />
      <ellipse cx="32" cy="39" rx="10" ry="8" fill="#fff" opacity="0.18" />
      <circle cx="26" cy="31" r="2.7" fill="#1b1738" />
      <circle cx="38" cy="31" r="2.7" fill="#1b1738" />
      <circle cx="27" cy="30" r="0.9" fill="#fff" />
      <circle cx="39" cy="30" r="0.9" fill="#fff" />
      <path
        d="M27.5 39.5c2 2.4 7 2.4 9 0"
        stroke="#1b1738"
        strokeWidth="1.8"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

/**
 * A beat, mocked up the way a child sees it: a picture, one line read aloud, and
 * two things to tap. Static markup rather than the real component — this is an
 * advertisement, and wiring it to the story engine would make the front page
 * depend on a generation.
 */
function BeatMock() {
  return (
    <div className="relative mx-auto w-full max-w-[27rem]">
      {/* The glow that makes it read as lit rather than pasted on. */}
      <div
        aria-hidden="true"
        className="absolute -inset-6 rounded-[2rem] blur-2xl"
        style={{ background: 'radial-gradient(closest-side, rgba(245,185,63,0.28), transparent)' }}
      />
      <div
        className="relative rounded-[1.4rem] p-3 shadow-card"
        style={{ background: '#fffdf8', border: '1px solid #efe6d4' }}
        role="img"
        aria-label="An example of one beat of a story"
      >
        <div className="overflow-hidden rounded-[1rem]">
          <svg viewBox="0 0 320 196" className="w-full" aria-hidden="true">
            <rect width="320" height="196" fill="#eef5e6" />
            <circle cx="262" cy="40" r="20" fill="#f5b93f" opacity="0.9" />
            <path d="M0 128c46-20 84-16 120 2s72 20 112 2 60-10 88 2v62H0z" fill="#cfe3c2" />
            <path d="M0 152c52-16 92-10 128 6s70 12 106-4 56-6 86 4v38H0z" fill="#a8cf9b" />
            <path d="M0 176c60-12 104-6 140 6h180v14H0z" fill="#8bbd83" />
            {[36, 74, 250, 292].map((x, i) => (
              <path
                key={x}
                d={`M${x} ${132 + (i % 2) * 8}c-9-14-9-32 0-46 9 14 9 32 0 46z`}
                fill="#6fa870"
              />
            ))}
            <g transform="translate(122 92)">
              <Companion size={66} body="#7d5fd1" />
            </g>
            {[196, 218, 240].map((x) => (
              <g key={x} transform={`translate(${x} 158)`}>
                <ellipse cx="0" cy="8" rx="9" ry="6" fill="#5e9e63" />
                <circle cx="-3.4" cy="3.4" r="2.1" fill="#fff" />
                <circle cx="3.4" cy="3.4" r="2.1" fill="#fff" />
                <circle cx="-3.4" cy="3.6" r="1" fill="#22301f" />
                <circle cx="3.4" cy="3.6" r="1" fill="#22301f" />
              </g>
            ))}
          </svg>
        </div>

        <p className="mt-3 px-1 font-display text-3 leading-snug" style={{ color: '#3b3357' }}>
          &ldquo;Look, Mira — three little frogs on the warm stone. Shall we say hello, or count
          them first?&rdquo;
        </p>

        <div className="mt-3 grid grid-cols-2 gap-2.5">
          {[
            { label: 'Say hello', art: <ChipHello /> },
            { label: 'Count them', art: <ChipCount /> },
          ].map((choice) => (
            <div
              key={choice.label}
              className="rounded-[0.9rem] p-2.5 text-center"
              style={{ background: '#f4f8ee', border: '1px solid #dfeacf' }}
            >
              {choice.art}
              <span className="mt-1.5 block font-display text-2" style={{ color: '#3b3357' }}>
                {choice.label}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ChipHello() {
  return (
    <svg viewBox="0 0 64 40" className="mx-auto h-11" aria-hidden="true">
      <circle cx="32" cy="20" r="15" fill="#f5b93f" opacity="0.28" />
      <path
        d="M22 17c3 9 17 9 20 0"
        stroke="#d99a25"
        strokeWidth="3"
        strokeLinecap="round"
        fill="none"
      />
      <circle cx="25" cy="15" r="2" fill="#d99a25" />
      <circle cx="39" cy="15" r="2" fill="#d99a25" />
    </svg>
  );
}

function ChipCount() {
  return (
    <svg viewBox="0 0 64 40" className="mx-auto h-11" aria-hidden="true">
      {[18, 32, 46].map((x) => (
        <g key={x}>
          <ellipse cx={x} cy="24" rx="6.5" ry="4.5" fill="#5e9e63" />
          <circle cx={x - 2.4} cy="20.4" r="1.6" fill="#fff" />
          <circle cx={x + 2.4} cy="20.4" r="1.6" fill="#fff" />
        </g>
      ))}
    </svg>
  );
}

function World({ world, name, note }: { world: string; name: string; note: string }) {
  return (
    <li
      data-world={world}
      className="overflow-hidden rounded-[1.1rem] shadow-card"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <svg viewBox="0 0 200 104" className="w-full" aria-hidden="true">
        <rect width="200" height="104" fill="var(--bg)" />
        <circle cx="166" cy="26" r="13" fill="var(--accent)" opacity="0.9" />
        <path
          d="M0 66c26-14 48-10 70 2s46 8 70-4 42-4 60 6v34H0z"
          fill="var(--primary)"
          opacity="0.28"
        />
        <path
          d="M0 82c30-11 54-6 78 4s48 2 70-6 34-2 52 4v20H0z"
          fill="var(--success)"
          opacity="0.45"
        />
      </svg>
      <div className="px-4 py-3">
        <p className="font-display text-3" style={{ color: 'var(--primary)' }}>
          {name}
        </p>
        <p className="mt-0.5 font-text text-1" style={{ color: 'var(--text-muted)' }}>
          {note}
        </p>
      </div>
    </li>
  );
}

function Step({
  n,
  title,
  body,
  art,
  tint,
}: {
  n: string;
  title: string;
  body: string;
  art: React.ReactNode;
  tint: string;
}) {
  return (
    <li
      className="flex h-full flex-col overflow-hidden rounded-[1.1rem]"
      style={{ background: '#fffdf8', border: '1px solid #ece3d2' }}
    >
      <div
        className="flex h-32 items-center justify-center"
        style={{ background: `color-mix(in srgb, ${tint} 16%, #fffdf8)` }}
      >
        {art}
      </div>
      <div className="flex flex-1 flex-col p-5">
        <p className="font-text text-1 uppercase tracking-[0.18em]" style={{ color: tint }}>
          Step {n}
        </p>
        <h3 className="mt-1 font-display text-4" style={{ color: 'var(--ink)' }}>
          {title}
        </h3>
        <p className="mt-2 font-text text-2 leading-relaxed" style={{ color: '#5f5980' }}>
          {body}
        </p>
      </div>
    </li>
  );
}

function ArtCompanion() {
  return <Companion size={92} body="#8b6ee0" />;
}

function ArtChoice() {
  return (
    <svg viewBox="0 0 150 92" className="h-24" aria-hidden="true">
      {[4, 80].map((x, i) => (
        <g key={x}>
          <rect
            x={x}
            y="10"
            width="66"
            height="72"
            rx="14"
            fill="#fffdf8"
            stroke={i ? '#79bd93' : '#f0c46b'}
            strokeWidth="2"
          />
          <circle cx={x + 33} cy="38" r="15" fill={i ? '#79bd93' : '#f5b93f'} opacity="0.9" />
          <rect x={x + 16} y="62" width="34" height="6" rx="3" fill="#e7dfcd" />
        </g>
      ))}
    </svg>
  );
}

function ArtMemory() {
  return (
    <svg viewBox="0 0 150 92" className="h-24" aria-hidden="true">
      <path
        d="M24 68C40 26 96 22 122 40"
        stroke="#b49df5"
        strokeWidth="3.5"
        strokeLinecap="round"
        strokeDasharray="2 10"
        fill="none"
      />
      <circle cx="24" cy="68" r="9" fill="#b49df5" opacity="0.5" />
      <g transform="translate(108 22)">
        <StarMark size={34} />
      </g>
    </svg>
  );
}

function Assurance({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <li
      className="rounded-[1.1rem] p-6"
      style={{ background: 'var(--ink-soft)', border: '1px solid var(--ink-line)' }}
    >
      <h3 className="flex items-start gap-3 font-display text-4" style={{ color: 'var(--cream)' }}>
        <Tick /> {title}
      </h3>
      <p
        className="mt-2 pl-8 font-text text-2 leading-relaxed"
        style={{ color: 'var(--cream-dim)' }}
      >
        {children}
      </p>
    </li>
  );
}

function Tick() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" width="22" height="22" className="mt-1 shrink-0">
      <circle cx="12" cy="12" r="11" fill="var(--sage)" opacity="0.22" />
      <path
        d="M7 12.5l3.2 3.2L17 9"
        stroke="var(--sage)"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}
