/**
 * Tailwind reads the semantic token layer, never raw values (PRODUCT.md §15.11).
 * That means `bg-surface` renders Storyland cream in the child tree and Calm
 * white under [data-theme="calm"] with no component-level branching.
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        primary: 'var(--primary)',
        accent: 'var(--accent)',
        success: 'var(--success)',
        text: 'var(--text)',
        'text-muted': 'var(--text-muted)',
        border: 'var(--border)',
      },
      spacing: {
        1: 'var(--space-1)',
        2: 'var(--space-2)',
        3: 'var(--space-3)',
        4: 'var(--space-4)',
        5: 'var(--space-5)',
        6: 'var(--space-6)',
        7: 'var(--space-7)',
        8: 'var(--space-8)',
        9: 'var(--space-9)',
        10: 'var(--space-10)',
      },
      fontSize: {
        1: 'var(--font-size-1)',
        2: 'var(--font-size-2)',
        3: 'var(--font-size-3)',
        4: 'var(--font-size-4)',
        5: 'var(--font-size-5)',
        6: 'var(--font-size-6)',
        7: 'var(--font-size-7)',
        8: 'var(--font-size-8)',
      },
      fontFamily: {
        display: 'var(--font-display)',
        text: 'var(--font-text)',
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        full: 'var(--radius-full)',
      },
      boxShadow: {
        card: 'var(--shadow-card)',
      },
      transitionDuration: {
        fast: 'var(--motion-fast)',
        base: 'var(--motion-base)',
        slow: 'var(--motion-slow)',
      },
      minHeight: {
        target: 'var(--target-min)',
      },
      minWidth: {
        target: 'var(--target-min)',
      },
    },
  },
  plugins: [],
};
