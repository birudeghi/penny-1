import { cn } from '@/lib/utils'
import type { Household } from '@/types/api'

/**
 * Who the record is about — the name, the four facts read before anything else, and how many
 * people feed it.
 *
 * It sits in the page header rather than in the context column because the identity is the frame
 * for everything below it, not another panel competing with medications.
 *
 * THE PROTOTYPE'S TOP BAR IS NOT PORTED. Its job — replacing the icon rail, which with one screen
 * to look at was four-fifths empty and pushed the record off-centre — is already done in this app
 * by the layout's own header, and that one has real navigation in it rather than a search box
 * wired to nothing. Two brand bars stacked on one page is not a port, it is a duplicate.
 *
 * THE VITALS BAND BELONGS HERE, under the name, because the four facts in it are the answer to
 * "who is this?" and not standing context like her medications — a reader who has to look away
 * from the name to find her allergies is one scroll away from not finding them. `context-column`
 * carried an all-blank placeholder of this band while there was no profile in the API; if it is
 * still there when you read this, this one is the real one and that one should go.
 */
export function PersonHeader({
  household,
  contributorCount,
}: {
  /**
   * `care_profile` is optional because `HouseholdOut` does not carry it yet. Typed as optional
   * rather than left out so this component needs no change on the day it lands and no call site
   * needs one either — and so that, until then, the band renders the honest thing: four labels
   * and four blanks, which is exactly what is known about her.
   */
  household: Household & { care_profile?: CareProfileFacts | null }
  /** Members of the WhatsApp group. `undefined` until `/api/members` lands, which is a moment
      after the feed. */
  contributorCount: number | undefined
}) {
  const name = household.care_recipient_name || household.name
  const profile = household.care_profile ?? undefined
  const description = describe(profile)

  return (
    <div className="pt-9 pb-6">
      <div className="flex items-start gap-5">
        <span className="grid size-[68px] shrink-0 place-items-center rounded-full bg-penny-wash text-[22px] font-semibold text-penny">
          {(name.trim().charAt(0) || '?').toUpperCase()}
        </span>

        <div className="min-w-0 pt-1.5">
          <h1 className="flex flex-wrap items-baseline gap-x-3 text-[30px] leading-none font-semibold tracking-tight">
            {name}
            {/* `tracking-normal` because this is the one small run of text that sits inside a
                tight heading and must not inherit its tracking. Omitted entirely when the
                profile has neither, rather than rendered as an empty "84 · " that reads like a
                bug — an absence here is not clinical information, unlike a blank in the band. */}
            {description && (
              <span className="text-[14.5px] font-normal tracking-normal text-muted-foreground">
                {description}
              </span>
            )}
          </h1>
          {/* Nothing rather than "0 people contribute to this record", which is true of a household
              whose WhatsApp group has not been linked yet and reads as an accusation. */}
          {contributorCount !== undefined && contributorCount > 0 && (
            <p className="mt-2.5 text-[14px] text-muted-foreground">
              {contributorCount === 1
                ? '1 person contributes to this record'
                : `${contributorCount} people contribute to this record`}
            </p>
          )}
        </div>
      </div>

      <VitalsBand profile={profile} />
    </div>
  )
}

/* ------------------------------------------------------------------------- the vitals band */

/**
 * Mirrors `Vital` in `app/care_profile.py`. `confirmed_at` is a bare `YYYY-MM-DD` and not an
 * instant, because the claim it records — "somebody said this was still true" — never honestly
 * had a time attached to it.
 */
export interface Vital {
  value: string
  confirmed_at: string | null
}

/** The part of `CareProfile` this header reads. Not the whole model: the rest is the extraction
 *  filter's business, and duplicating it here would be a second copy to keep in step. */
export interface CareProfileFacts {
  age?: number | null
  place?: string | null
  nhs_number?: Vital | null
  allergies?: Vital | null
  respect_form?: Vital | null
  baseline?: Vital | null
}

/**
 * The order they are asked for, which is also the order `_profile_lines` renders them in on the
 * backend, so the brief and the band can be read side by side when one of them looks wrong.
 *
 * `alert` is red-while-empty on allergies, and only on allergies. An unknown allergy list is the
 * alarming case, not the neutral one — it is the field whose blank most needs to be noticed.
 */
interface VitalRow {
  label: string
  read: (facts: CareProfileFacts) => Vital | null | undefined
  /** Red while empty as well as while filled. Allergies only. */
  alert: boolean
}

const VITALS: VitalRow[] = [
  { label: 'NHS number', read: (facts) => facts.nhs_number, alert: false },
  { label: 'Allergies', read: (facts) => facts.allergies, alert: true },
  { label: 'RESPECT form', read: (facts) => facts.respect_form, alert: false },
  // "Baseline" rather than the prototype's "Her baseline": the care recipient is whoever the
  // family says they are, and the record should not guess a pronoun for them.
  { label: 'Baseline', read: (facts) => facts.baseline, alert: false },
]

/**
 * The four facts a clinician asks for before anything else, under the name, never scrolled to.
 *
 * EVERY LABEL IS RENDERED WHETHER OR NOT IT HAS A VALUE, and that is the rule this component
 * exists to enforce. A row hidden because it is empty does not read as "we have not been told
 * this" — it reads as "there is nothing here to tell", and for allergies those are opposite
 * clinical statements. A record with four unknowns would look like a complete record, which is
 * the single most dangerous thing this page could do with a gap in it.
 *
 * That is the exact opposite of what `care_profile._render_vitals` does, which omits the vitals
 * nobody has stated. Both are right, because the readers are not the same one. The brief's reader
 * is a filter, and a filter cannot act on an absence — four lines of "unknown" only take room the
 * transcript wanted. This page's reader is a person about to decide something, and the absence is
 * one of the most important things on the page. Neither is a degraded copy of the other; if you
 * change one, read the other's docstring before you change it to match.
 *
 * "NOT RECORDED", NOT A BLANK AND NOT "NONE". A blank cell is ambiguous — a reader cannot tell it
 * from a component that failed to render — and "None" is a clinical claim nobody made. "Not
 * recorded" is the only one of the three that says what is actually true: the record has not been
 * told, and the reader should go and ask.
 */
function VitalsBand({ profile }: { profile: CareProfileFacts | undefined }) {
  const facts = profile ?? {}

  return (
    // `lg` radius and `--band`: not a card, and deliberately not white. Cards are the timeline's
    // object, and a white block above the first entry would read as the first entry.
    <dl className="mt-6 grid gap-x-8 gap-y-5 rounded-lg bg-band px-5 py-4 sm:grid-cols-2 lg:grid-cols-4">
      {VITALS.map(({ label, read, alert }) => {
        const vital = read(facts)
        // Trimmed, and empty-after-trim counts as absent — the same rule `Vital._clean_value`
        // applies server-side, so a whitespace-only value cannot show up here as a row that
        // looks filled in while the brief treats it as missing.
        const value = vital?.value?.trim()
        // Trimmed for the same reason, and to the same end: a date of `"  "` would otherwise
        // print "Confirmed" with nothing after it, which is the dangling-label failure this
        // band exists to avoid, on the one line whose job is to be doubted.
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
            </dd>
            {/* Shown for every vital that HAS a value, including the ones nobody dated. A stale
                allergies line is more dangerous than a missing one, and the date is the only
                thing that lets a reader tell the two apart — so "Not confirmed" has to be as
                visible as a date would be. No staleness threshold is applied and none should be:
                how old is too old is a clinical judgement about this person, and it is the
                reader's to make, not this component's. */}
            {value && (
              <dd className="mt-1 text-[12.5px] text-muted-foreground">
                {confirmed ? `Confirmed ${formatConfirmedOn(confirmed)}` : 'Not confirmed'}
              </dd>
            )}
          </div>
        )
      })}
    </dl>
  )
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/**
 * "2026-07-25" → "25 Jul 2026", without ever constructing a `Date`.
 *
 * Every other date in this app goes through `lib/datetime` and the household's timezone, and this
 * one deliberately must not. `confirmed_at` is a calendar date, not an instant: parsing it as one
 * makes it midnight UTC, and rendering midnight UTC in a household five hours west prints the day
 * before. Being a day out is invisible and, on the field whose entire job is to let a reader judge
 * how stale a vital is, it is exactly the wrong thing to be wrong about.
 *
 * Anything that is not an ISO date is passed through untouched. `Vital.confirmed_at` is a free
 * string on purpose — a family says "January 2026" as readily as they type a date, and the server
 * would rather store their words than lose the answer they were attached to (see `Vital`).
 */
function formatConfirmedOn(confirmedAt: string): string {
  const raw = confirmedAt.trim()
  const match = ISO_DATE.exec(raw)
  if (!match) return raw
  const month = MONTHS[Number(match[2]) - 1]
  if (!month) return raw
  return `${Number(match[3])} ${month} ${match[1]}`
}

/** "84 · Croydon" — as much of it as the profile actually has, and nothing where it has neither. */
function describe(profile: CareProfileFacts | undefined): string {
  if (!profile) return ''
  const parts: string[] = []
  if (typeof profile.age === 'number') parts.push(String(profile.age))
  const place = profile.place?.trim()
  if (place) parts.push(place)
  return parts.join(' · ')
}
