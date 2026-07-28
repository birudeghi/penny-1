/**
 * The translation layer between the API's four event kinds and the record's five.
 *
 * The design was drawn against five kinds — symptom, medication, appointment, wellbeing, admin —
 * because that is how a family talks about a week. The API stores four, with everything that is
 * not clinical folded into `note` and sub-classified by `details.category`. Both are right for
 * their own layer, so the reconciliation happens here, CLIENT-SIDE, and the backend is left
 * alone: a `wellbeing` event kind would be a migration, an extraction-prompt change and a new
 * arm in every server-side switch, to draw a different icon.
 *
 * THE MAPPING IS ONE LINE AND IT IS THE WHOLE POINT: `note` + `mood` is wellbeing — the good
 * afternoon in the garden, the flat week after the hospital — and every other note category
 * (logistics, finance, equipment, admin, other) is admin. Wellbeing is the one thing a care
 * record habitually loses, so it gets its own hue rather than sitting in the grey pile.
 */

import { formatRelativeTime, formatTime, formatTimelineYear } from './datetime'
import type { People } from './people'
import { assertNever, type Event } from '../types/api'

export type TimelineKind = 'symptom' | 'medication' | 'appointment' | 'wellbeing' | 'admin'

/**
 * The label and the tint for each kind. Same structure as the prototype's `kindStyle`, kept as
 * whole class strings because Tailwind reads source files as text and cannot follow `bg-${kind}`.
 */
export const KIND_STYLE: Record<TimelineKind, { label: string; text: string; bg: string }> = {
  symptom: { label: 'Symptom', text: 'text-symptom', bg: 'bg-symptom-wash' },
  medication: { label: 'Medication', text: 'text-penny', bg: 'bg-penny-wash' },
  appointment: { label: 'Appointment', text: 'text-appointment', bg: 'bg-appointment-wash' },
  wellbeing: { label: 'Wellbeing', text: 'text-wellbeing', bg: 'bg-wellbeing-wash' },
  admin: { label: 'Admin', text: 'text-admin', bg: 'bg-admin-wash' },
}

/**
 * `assertNever` in the default arm on purpose: a fifth API kind then fails `tsc -b` here, rather
 * than reaching the spine as a node with no icon and no colour.
 */
export function timelineKind(event: Event): TimelineKind {
  switch (event.kind) {
    case 'symptom':
    case 'medication':
    case 'appointment':
      return event.kind
    case 'note':
      return event.details.category === 'mood' ? 'wellbeing' : 'admin'
    default:
      return assertNever(event)
  }
}

/**
 * The short stamp beside the headline: "2h ago", "1d ago".
 *
 * Relative, but ONLY as precisely as the extraction placed the event. The hedge still has to
 * live here: "3h ago" on an event the model could only place to the day would invent a certainty
 * no message ever carried, and it is the kind of invention a GP would act on. So day-precision
 * events never claim hours, and anything vaguer keeps its hedge in words.
 */
export function entryTimeLabel(event: Event): string {
  switch (event.occurred_at_precision) {
    case 'exact':
      return formatRelativeTime(event.occurred_at)
    case 'day': {
      const days = Math.floor((Date.now() - Date.parse(event.occurred_at)) / 86_400_000)
      return days < 1 ? 'today' : formatRelativeTime(event.occurred_at)
    }
    case 'week':
      return 'Sometime that week'
    case 'month':
      return 'Sometime that month'
    case 'unknown':
      return 'Date uncertain'
  }
}

/**
 * The plain-language line naming where the entry came from.
 *
 * It names a person because provenance in this product is a person, not a system: "from Liz's
 * WhatsApp" is checkable by anybody in the group, and "extracted from message 4f2a…" is not.
 * An entry with no excerpts behind it says so outright rather than staying silent — a reader who
 * assumes everything came from the chat would otherwise mistake somebody's typed note for
 * evidence.
 */
export function provenanceOf(event: Event, timeZone: string, people: People): string {
  const first = event.source_excerpts[0]
  const edited = event.edited_at ? ' · edited by the family' : ''

  if (!first) return `Added to the record by hand${edited}`

  const who = event.actor ? people.byId(event.actor.member_id).name : people.bySender(first.sender).name
  return `from ${who}'s WhatsApp · ${formatTime(first.sent_at, timeZone)}${edited}`
}

export interface TimelineYear {
  year: string
  items: Event[]
}

/**
 * Walks the already-sorted feed and starts a new group when the year changes.
 *
 * It does not bucket: a page that came back out of order shows up as a repeated year marker
 * rather than being silently tidied into looking right. Same rule, and the same reason, as
 * `groupByDay` in `datetime.ts`.
 */
export function groupByYear(events: Event[], timeZone: string): TimelineYear[] {
  const groups: TimelineYear[] = []
  for (const event of events) {
    const year = formatTimelineYear(event.occurred_at, timeZone)
    const last = groups.at(-1)
    if (last && last.year === year) last.items.push(event)
    else groups.push({ year, items: [event] })
  }
  return groups
}
