import type { IconProps } from './icons/props.ts'

/** The 「问」 glyph painted inside {@link WenMark}; kept as a constant for reuse in favicon-like artwork. */
export const WEN_MARK_TEXT = '问'

/** Contrast ink used for the glyph cut inside the filled mark (theme base surface). */
const WEN_MARK_GLYPH_FILL = 'var(--dsw-alias-bg-base, #fff)'

/** Fallback font stack that guarantees a CJK glyph across renderers. */
const WEN_MARK_FONT_FAMILY =
  "system-ui, -apple-system, 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', 'Noto Sans CJK SC', sans-serif"

/**
 * The product brand mark: a rounded square filled with the current ink and a
 * 「问」 glyph cut in the surface color. Monochrome, so it follows text color
 * on any surface (replaces the previous whale/fish brand mark).
 * @param props.size - square edge in px (default 24).
 * @param props.className - extra class for layout placement.
 * @returns the mark svg (aria-hidden; pair with the wordmark for accessibility).
 */
export function WenMark({ size = 24, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      aria-hidden="true"
    >
      <rect x="1" y="1" width="22" height="22" rx="6.5" fill="currentColor" />
      <text
        x="12"
        y="12.9"
        textAnchor="middle"
        dominantBaseline="central"
        fontFamily={WEN_MARK_FONT_FAMILY}
        fontSize="13.5"
        fontWeight="600"
        fill={WEN_MARK_GLYPH_FILL}
      >
        {WEN_MARK_TEXT}
      </text>
    </svg>
  )
}
