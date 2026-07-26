import type { ReactNode } from 'react'

import { KindNode } from '@/components/kind-node'
import { PersonAvatar } from '@/components/person-avatar'
import { dayKey, formatOccurredAt } from '@/lib/datetime'
import { usePeople } from '@/lib/people'
import { timelineKind } from '@/lib/timeline'
import { cn } from '@/lib/utils'
import type { Event } from '@/types/api'

/**
 * The standing facts, beside the timeline rather than inside it.
 *
 * These do not happen on a day, so they have no place on a spine ordered by date — but they are
 * the context every entry is read against, which is why they sit alongside instead of on a second
 * screen. Identity moved out of here and into the page header: it was the one panel that framed
 * everything else rather than sitting level with it.
 *
 * TWO OF THE PROTOTYPE'S PANELS ARE NOT HERE. "Medications" and "Living with" were fixtures, and
 * there is no care-profile endpoint behind them yet. A list of invented drugs in a care record is
 * not a placeholder, it is a clinical error waiting for somebody to read it out at an
 * appointment. They are omitted entirely rather than stubbed. The vitals band is the one
 * exception, and the band's own comment says why.
 *
 * "Coming up" takes their place, and is real: the same `Event` objects the timeline renders,
 * filtered server-side by `occurred_at > now()`.
 */
export function ContextColumn({
  upcoming,
  timeZone,
}: {
  /** `undefined` while `/api/upcoming` is still in flight, or if it failed. */
  upcoming: Event[] | undefined
  timeZone: string
}) {
  const people = usePeople()

  return (
    // Sticky and self-start: these facts stay true for every year the timeline
    // scrolls through, so they stay on screen instead of being left behind at
    // the top of a history that runs for pages. `self-start` is what stops the
    // flex row stretching the column to full height and killing the stick.
    <aside className="sticky top-6 hidden max-h-[calc(100vh-3rem)] w-[330px] shrink-0 flex-col gap-9 self-start overflow-y-auto pt-10 pb-6 lg:flex">
      <VitalsBand />

      <section>
        <PanelLabel>Coming up</PanelLabel>
        <div className="mt-4 space-y-3.5">
          {upcoming === undefined && <p className="text-[13px] text-muted-foreground">Loading…</p>}
          {/* Nothing booked is the ordinary state of most weeks, not a failure to load. */}
          {upcoming?.length === 0 && (
            <p className="text-[13px] text-muted-foreground">Nothing scheduled.</p>
          )}
          {upcoming?.map((event) => (
            // Order comes from the API, ascending. Re-sorting here would only hide a backend bug.
            <div key={event.id} className="flex gap-2.5">
              <span className="mt-0.5">
                <KindNode kind={timelineKind(event)} size="sm" />
              </span>
              <div className="min-w-0">
                <div className="text-[13.5px] leading-snug font-medium">{event.title}</div>
                <div className="mt-0.5 text-[12.5px] text-muted-foreground">
                  {countdown(event.occurred_at, timeZone)} ·{' '}
                  {formatOccurredAt(event.occurred_at, event.occurred_at_precision, timeZone)}
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {people.all.length > 0 && (
        <section>
          <PanelLabel>Cared by</PanelLabel>
          <div className="mt-4 space-y-3">
            {people.all.map((person) => (
              <div key={person.id} className="flex items-center gap-2.5">
                <PersonAvatar person={person} size="sm" />
                <span className="text-[13.5px] font-medium">{person.name}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </aside>
  )
}

/** The prototype's `action` slot went with the panels that had actions; a label with a dead
 *  button on it is worse than a label. */
function PanelLabel({ children }: { children: ReactNode }) {
  return (
    <h3 className="text-[10.5px] font-semibold tracking-[0.1em] text-muted-foreground">
      {children}
    </h3>
  )
}

/**
 * The four facts a clinician asks for before anything else. Kept as a band under the name because
 * they are read together, at a glance, and never scrolled to.
 *
 * EVERY VALUE IS BLANK, and every row is rendered anyway. There are no care-profile fields in the
 * API yet, so there is nothing to put in them — but "we do not know this person's allergies" is
 * itself one of the most important things on the page, and it is only visible if the label is.
 * Hiding the empty rows would turn a record with four unknowns into a record that looks complete.
 * The day the fields exist, this reads them; until then it is an honest set of blanks.
 *
 * "Baseline" rather than the prototype's "Her baseline": the care recipient is whoever the family
 * says they are, and the record should not guess a pronoun for them.
 */
const VITALS: { label: string; alert: boolean }[] = [
  { label: 'NHS number', alert: false },
  // Red even while empty. An unknown allergy list is the alarming case, not the neutral one.
  { label: 'Allergies', alert: true },
  { label: 'RESPECT form', alert: false },
  { label: 'Baseline', alert: false },
]

function VitalsBand() {
  return (
    <section>
      <dl className="space-y-3.5 rounded-lg bg-band px-4 py-4">
        {VITALS.map((item) => (
          <div key={item.label}>
            <dt
              className={cn(
                'text-[10.5px] font-semibold tracking-[0.1em]',
                item.alert ? 'text-symptom' : 'text-muted-foreground',
              )}
            >
              {item.label}
            </dt>
            {/* `min-h-[1lh]` is what keeps an empty value a visible gap rather than a collapsed
                row nobody notices is missing. */}
            <dd className="mt-1 min-h-[1lh] text-[13.5px] leading-snug" />
          </div>
        ))}
      </dl>
    </section>
  )
}

const DAY_MS = 86_400_000

/**
 * Counted in the household's timezone by comparing day keys rather than subtracting instants: an
 * appointment at 09:00 tomorrow is "Tomorrow" even when it is 21 hours away, and 08:00 today is
 * "Today" even when it has just gone 07:00.
 */
function countdown(iso: string, timeZone: string, now = new Date()): string {
  const today = Date.parse(dayKey(now.toISOString(), timeZone))
  const then = Date.parse(dayKey(iso, timeZone))
  const days = Math.round((then - today) / DAY_MS)
  if (days <= 0) return 'Today'
  if (days === 1) return 'Tomorrow'
  return `In ${days} days`
}
