/**
 * The choice cards (PRODUCT.md §8.5, §14.4, §14.5).
 *
 * **Pictures, not a text list.** The audience cannot read, so a menu of words
 * excludes every one of them. Each card is a picture of where that choice goes,
 * with the words underneath for the adult in the room and for a screen reader
 * (§14.6). The companion has already said the options aloud — they ride in the
 * same voice clip as the narration — so a card whose picture has not landed yet is
 * still a card the child has heard described. That is why nothing here waits for
 * an image: a choice a pre-reader cannot take because its icon is still rendering
 * would be a worse failure than a plain card.
 *
 * **Immediate feedback, within ~100ms** (§14.7). The tap is acknowledged by the
 * card itself — it lifts, the others fade back, and a soft chime plays — before
 * the network has answered anything. The consequence beat takes seconds to
 * generate; what stops that from feeling like a hang is that the child can *see*
 * their tap landed.
 *
 * **No wrong card.** There is no disabled state, no red, no correct answer
 * highlighted afterwards. Every option leads somewhere good (§8.5), so the only
 * states a card has are "waiting to be picked" and "picked".
 */

import type { ChoiceOption } from '@/lib/sse';

interface ChoiceCardsProps {
  options: ChoiceOption[];
  /** The key already picked, if any. Locks the row without disabling anything. */
  chosen: string | null;
  onChoose: (option: ChoiceOption) => void;
}

export default function ChoiceCards({ options, chosen, onChoose }: ChoiceCardsProps) {
  return (
    <div
      role="group"
      aria-label="What would you like to do?"
      data-testid="choice-cards"
      // Wraps rather than scrolls: two or three cards (§14.5) always fit a tablet,
      // and a row a child can scroll is a row with options they may never see.
      className="mt-8 flex flex-wrap items-stretch justify-center gap-5"
    >
      {options.map((option) => (
        <ChoiceCard
          key={option.key}
          option={option}
          picked={chosen === option.key}
          dimmed={chosen !== null && chosen !== option.key}
          onChoose={onChoose}
        />
      ))}
    </div>
  );
}

function ChoiceCard({
  option,
  picked,
  dimmed,
  onChoose,
}: {
  option: ChoiceOption;
  picked: boolean;
  dimmed: boolean;
  onChoose: (option: ChoiceOption) => void;
}) {
  return (
    <button
      type="button"
      // The scene behind is a tap target too (it starts the narration when
      // autoplay was refused), so the click stops here.
      onClick={(event) => {
        event.stopPropagation();
        onChoose(option);
      }}
      data-testid={`choice-${option.key}`}
      data-picked={picked || undefined}
      aria-label={option.label}
      // Far above the 64px floor: these are the main targets on the screen, and
      // §14.4 asks for large, spaced and forgiving (§15.4).
      className={`min-h-target w-[min(38vw,14rem)] overflow-hidden rounded-lg bg-surface p-3 text-center shadow-card transition-all duration-base motion-safe:hover:scale-105 active:scale-95 ${
        picked ? 'ring-4 ring-accent motion-safe:scale-105' : ''
      } ${dimmed ? 'opacity-50' : ''}`}
    >
      <ChoicePicture option={option} />
      <span className="font-display text-3 mt-3 block leading-tight text-text">{option.label}</span>
    </button>
  );
}

/**
 * The card's picture, or the shape that stands in for one.
 *
 * The placeholder is a soft coloured field derived from the option's own key, so
 * the two or three cards on screen are visibly *different* from each other from
 * the first frame — which is the part of "a picture of each path" that a child
 * actually uses to tell them apart. It is deliberately not a spinner and not a
 * grey box: neither tells a four-year-old anything, and one of them tells them to
 * wait (§14.5).
 */
function ChoicePicture({ option }: { option: ChoiceOption }) {
  if (option.imageUrl) {
    return (
      <img
        src={option.imageUrl}
        alt=""
        data-testid={`choice-image-${option.key}`}
        className="aspect-[4/3] w-full rounded-md object-cover motion-safe:animate-[fade-in_var(--motion-slow)_var(--motion-ease)]"
      />
    );
  }
  return (
    <div
      aria-hidden="true"
      data-testid={`choice-placeholder-${option.key}`}
      className="aspect-[4/3] w-full rounded-md"
      style={{ background: `hsl(${hueFor(option.key)} 55% 82%)` }}
    />
  );
}

/** A stable hue per option key, so a card does not change colour when it rerenders. */
function hueFor(key: string): number {
  let hash = 0;
  for (const character of key) hash = (hash * 31 + character.charCodeAt(0)) % 360;
  return hash;
}
