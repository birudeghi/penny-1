import { Activity, CalendarDays, FileText, Heart, Pill } from 'lucide-react'

import { cn } from '@/lib/utils'
import { KIND_STYLE, type TimelineKind } from '@/lib/timeline'

/**
 * The entry's kind, as a filled disc with the glyph knocked out in white.
 *
 * Luma puts a plain grey dot on the spine. The dot is the right size and the right silhouette, so
 * this keeps it and fills it — one solid disc in the kind's hue. It signposts the type from the
 * left margin without the ring and the tinted glyph making a small object out of three colours.
 *
 * The same disc marks what is coming up in the column beside the record, at `sm`. Reusing it
 * there rather than drawing a second set of icons is the point: an appointment two weeks out is
 * the same object, in the same colour, as the appointment you will later scan for on the spine.
 *
 * THE DISC HAS A NAME, AND IT USED TO BE `aria-hidden`. Nothing else on an entry says what kind of
 * thing it is: the headline is a sentence about a day ("Ramipril reduced to 2.5 mg"), the
 * provenance line names a person, and the kind itself is carried entirely by this 26px disc — by
 * its hue and by a 13px glyph inside it. Hidden from the accessibility tree, every entry in the
 * record was untyped to anyone using a screen reader, and to anyone who cannot separate
 * `--appointment` (L 0.6, hue 74) from `--symptom` (L 0.52, hue 38) the kind was conveyed by
 * colour alone — the two failures are the same failure, which is that meaning lived in a channel
 * one reader does not receive. `role="img"` plus a name puts the kind back into the text of the
 * page: "Symptom", then the time, then the headline. It adds no visible mark, no extra box in the
 * flow, and nothing at all to the disc's geometry, which matters because the spine is drawn at a
 * hard 137px and the ring below is what lets it pass behind rather than into every node.
 *
 * The label is `KIND_STYLE[kind].label` rather than a second table beside it. One list of the five
 * words the product uses for its five kinds; a private copy here is how the record ends up calling
 * the same disc "Wellbeing" in one place and "Mood" in another.
 *
 * There is deliberately NO `decorative` or `label={false}` escape hatch. An opt-out is exactly how
 * this disc lost its name the first time — it is always the sole carrier of the kind at every site
 * that renders it today, and a caller who genuinely repeats the kind in adjacent text should
 * change the text, not silence the disc.
 */
const NODE: Record<TimelineKind, { icon: typeof Activity; fill: string }> = {
  symptom: { icon: Activity, fill: 'bg-symptom' },
  medication: { icon: Pill, fill: 'bg-penny' },
  appointment: { icon: CalendarDays, fill: 'bg-appointment' },
  wellbeing: { icon: Heart, fill: 'bg-wellbeing' },
  admin: { icon: FileText, fill: 'bg-admin' },
}

const SIZES = {
  sm: { disc: 'size-[18px]', glyph: 'size-[10px]' },
  md: { disc: 'size-[26px]', glyph: 'size-[13px]' },
} as const

export function KindNode({
  kind,
  size = 'md',
  /** Adds a ring in the ground colour so the timeline spine passes behind the
      disc rather than appearing to touch it. Only the spine needs it. */
  onSpine = false,
}: {
  kind: TimelineKind
  size?: keyof typeof SIZES
  onSpine?: boolean
}) {
  const { icon: Icon, fill } = NODE[kind]
  const s = SIZES[size]

  return (
    <span
      role="img"
      aria-label={KIND_STYLE[kind].label}
      className={cn(
        'grid shrink-0 place-items-center rounded-full',
        s.disc,
        fill,
        onSpine && 'ring-4 ring-ground',
      )}
    >
      {/* `role="img"` already makes its subtree presentational; this is belt and braces against a
          future glyph that ships with a `<title>` and starts announcing itself twice. */}
      <Icon aria-hidden className={cn(s.glyph, 'text-white dark:text-background')} strokeWidth={2.1} />
    </span>
  )
}
