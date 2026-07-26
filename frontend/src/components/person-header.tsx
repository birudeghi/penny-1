import type { Household } from '@/types/api'

/**
 * Who the record is about — the name, and how many people feed it.
 *
 * It sits in the page header rather than in the context column because the identity is the frame
 * for everything below it, not another panel competing with medications.
 *
 * THE PROTOTYPE'S TOP BAR IS NOT PORTED. Its job — replacing the icon rail, which with one screen
 * to look at was four-fifths empty and pushed the record off-centre — is already done in this app
 * by the layout's own header, and that one has real navigation in it rather than a search box
 * wired to nothing. Two brand bars stacked on one page is not a port, it is a duplicate.
 *
 * AGE AND PLACE ARE MISSING, not dropped. `Household` carries a care recipient's name, a
 * household name and a timezone; there is no date of birth and no address anywhere in the API
 * yet. They are omitted rather than rendered as an empty "84 · " that reads like a bug.
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

  return (
    <div className="flex items-start gap-5 pt-9 pb-6">
      <span className="grid size-[68px] shrink-0 place-items-center rounded-full bg-penny-wash text-[22px] font-semibold text-penny">
        {(name.trim().charAt(0) || '?').toUpperCase()}
      </span>

      <div className="min-w-0 pt-1.5">
        <h1 className="flex flex-wrap items-baseline gap-x-3 text-[30px] leading-none font-semibold tracking-tight">
          {name}
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
  )
}
