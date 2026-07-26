import type { CareProfileFacts, Household } from '@/types/api'

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
  household: Household
  /** Members of the WhatsApp group. `undefined` until `/api/members` lands, which is a moment
      after the feed. */
  contributorCount: number | undefined
}) {
  const name = household.care_recipient_name || household.name
  const profile = household.care_profile
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

    </div>
  )
}

function describe(profile: CareProfileFacts): string {
  const parts: string[] = []
  if (typeof profile.age === 'number') parts.push(String(profile.age))
  const place = profile.place?.trim()
  if (place) parts.push(place)
  return parts.join(' · ')
}
