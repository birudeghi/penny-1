import type { ReactNode } from 'react'

import { KindNode } from '@/components/kind-node'
import { PersonAvatar } from '@/components/person-avatar'
import { dayKey, formatOccurredAt } from '@/lib/datetime'
import { usePeople } from '@/lib/people'
import { timelineKind } from '@/lib/timeline'
import { cn } from '@/lib/utils'
import type { CareProfileFacts, Event, Vital } from '@/types/api'

/**
 * The standing facts, beside the timeline rather than inside it.
 *
 * These do not happen on a day, so they have no place on a spine ordered by date — but they are
 * the context every entry is read against, which is why they sit alongside instead of on a second
 * screen. Identity moved out of here and into the page header: it was the one panel that framed
 * everything else rather than sitting level with it.
 *
 * TWO OF THE PROTOTYPE'S PANELS ARE NOT HERE. "Medications" and "Living with" were fixtures, and
 * nothing in the API returns them. A list of invented drugs in a care record is not a
 * placeholder, it is a clinical error waiting for somebody to read it out at an appointment.
 * They are omitted entirely rather than stubbed. The vitals band used to be omitted for the same
 * reason and no longer is: `household.vitals` is real, filled by the onboarding interview.
 *
 * "Coming up" takes their place, and is real: the same `Event` objects the timeline renders,
 * filtered server-side by `occurred_at > now()`.
 */
export function ContextColumn({
  upcoming,
  profile,
  timeZone,
}: {
  /** `undefined` while `/api/upcoming` is still in flight, or if it failed. */
  upcoming: Event[] | undefined
  /** The standing facts. `undefined` only until the session lands; the band then renders four
      "Not recorded" rows, which is exactly what is known about her at that moment. */
  profile: CareProfileFacts | undefined
  timeZone: string
}) {
  const people = usePeople()

  return (
    // Sticky and self-start: these facts stay true for every year the timeline
    // scrolls through, so they stay on screen instead of being left behind at
    // the top of a history that runs for pages. `self-start` is what stops the
    // flex row stretching the column to full height and killing the stick.
    <aside className="sticky top-6 hidden max-h-[calc(100vh-3rem)] w-[330px] shrink-0 flex-col gap-9 self-start overflow-y-auto pt-10 pb-6 lg:flex">
      <VitalsBand profile={profile} />

      <ProfileList label="Medications" items={profile?.medications ?? []} />
      <ProfileList label="Living with" items={profile?.conditions ?? []} />

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
 * EVERY ROW IS RENDERED WHETHER OR NOT IT IS FILLED. "We do not know this person's allergies" is
 * itself one of the most important things on the page, and it is only visible if the label is.
 * Hiding the empty rows would turn a record with four unknowns into a record that looks complete,
 * which is the one way this band could do harm rather than none.
 *
 * "Baseline" rather than the prototype's "Her baseline": the care recipient is whoever the family
 * says they are, and the record should not guess a pronoun for them.
 */
interface VitalRow {
  label: string
  read: (facts: CareProfileFacts) => Vital | null
  /** Red while empty as well as while filled. Allergies only. */
  alert: boolean
}

/**
 * The order they are asked for, which is also the order `_profile_lines` renders them in on the
 * backend, so the brief and the band can be read side by side when one of them looks wrong.
 */
const VITALS: VitalRow[] = [
  { label: 'NHS number', read: (f) => f.nhs_number, alert: false },
  // Red even while empty. An unknown allergy list is the alarming case, not the neutral one.
  { label: 'Allergies', read: (f) => f.allergies, alert: true },
  { label: 'RESPECT form', read: (f) => f.respect_form, alert: false },
  // "Baseline" rather than the prototype's "Her baseline": the care recipient is whoever the
  // family says they are, and the record should not guess a pronoun for them.
  { label: 'Baseline', read: (f) => f.baseline, alert: false },
]

/**
 * The four facts a clinician asks for before anything else, at the top of the column.
 *
 * EVERY LABEL IS RENDERED WHETHER OR NOT IT HAS A VALUE. A row hidden because it is empty does
 * not read as "we have not been told this" — it reads as "there is nothing here to tell", and
 * for allergies those are opposite clinical statements. A record with four unknowns would look
 * like a complete record, which is the single most dangerous thing this page could do with a gap.
 *
 * That is the exact opposite of what `care_profile._render_vitals` does, which omits the vitals
 * nobody has stated. Both are right, because the readers are not the same one. The brief's reader
 * is a filter, and a filter cannot act on an absence. This page's reader is a person about to
 * decide something, and the absence is one of the most important things on the page.
 *
 * "NOT RECORDED", NOT A BLANK AND NOT "NONE". A blank cell cannot be told from a component that
 * failed to render, and "None" is a clinical claim nobody made.
 */
function VitalsBand({ profile }: { profile: CareProfileFacts | undefined }) {
  return (
    <dl className="space-y-3.5 rounded-lg bg-band px-4 py-4">
      {VITALS.map(({ label, read, alert }) => {
        const vital = profile ? read(profile) : null
        // Empty-after-trim counts as absent, the same rule `Vital._clean_value` applies
        // server-side, so a whitespace-only value cannot look filled in here while the brief
        // treats it as missing.
        const value = vital?.value?.trim()
        const confirmed = vital?.confirmed_at?.trim()
        return (
          <div key={label}>
            <dt
              className={cn(
                'text-[10.5px] font-semibold tracking-[0.1em]',
                alert ? 'text-symptom' : 'text-muted-foreground',
              )}
            >
              {label}
            </dt>
            <dd className="mt-1 text-[13.5px] leading-snug">
              {value || <span className="text-muted-foreground">Not recorded</span>}
              {value && (
                // Shown for every vital that HAS a value, including undated ones. A stale
                // allergies line is more dangerous than a missing one, and the date is the only
                // thing that lets a reader tell them apart — so "Not confirmed" has to be as
                // visible as a date. No staleness threshold: how old is too old is a clinical
                // judgement about this person, not one this component can make.
                <span className="mt-0.5 block text-[11.5px] text-muted-foreground">
                  {confirmed ? `Confirmed ${confirmed}` : 'Not confirmed'}
                </span>
              )}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}

/**
 * "Medications" and "Living with" — the family's own words, never expanded or tidied.
 *
 * OMITTED ENTIRELY WHEN EMPTY, which is the opposite rule to the vitals band above, and the
 * difference is what each silence means. Nobody is harmed by not knowing the list of conditions
 * is empty; a heading over nothing just says the record is thin, which the timeline already says
 * better. A blank ALLERGIES row is itself the clinical information.
 */
function ProfileList({ label, items }: { label: string; items: string[] }) {
  const values = items.map((item) => item.trim()).filter(Boolean)
  if (values.length === 0) return null
  return (
    <section>
      <PanelLabel>{label}</PanelLabel>
      <div className="mt-4 space-y-2.5">
        {values.map((value) => (
          <div key={value} className="text-[13.5px] leading-snug font-medium">
            {value}
          </div>
        ))}
      </div>
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
