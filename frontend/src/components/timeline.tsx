import { TimelineEntry } from '@/components/timeline-entry'
import { groupByYear } from '@/lib/timeline'
import type { Event } from '@/types/api'

/**
 * The care timeline.
 *
 * No filters and no scope tabs: a row of controls above the first entry is the surest way to make
 * a record look like a dashboard. Search is the escape hatch now that the history runs to years
 * rather than days, and it belongs in the chrome, not on the record.
 *
 * NO COMPOSER AT THE HEAD, unlike the prototype. The design's "Add an update" panel is a good
 * one, but there is no `POST /api/events` behind it yet, and a composer that takes a carer's
 * account of a fall and then quietly drops it is the single worst thing this product could do.
 * It comes back in the same diff as the endpoint.
 */
export function Timeline({ events, timeZone }: { events: Event[]; timeZone: string }) {
  const years = groupByYear(events, timeZone)

  /*
   * An empty record says one sentence and stops.
   *
   * No illustration, no "get started", no button. A family arrives here in the middle of
   * something — a bad week, a discharge, a phone call from a neighbour — and a cheerful empty
   * state is the product talking about itself at the exact moment nobody wants to hear from it.
   * The sentence is enough: it says the record is working and has nothing in it yet.
   */
  if (events.length === 0) {
    return (
      <div className="pt-10">
        <p className="text-[14px] text-muted-foreground">No entries yet.</p>
      </div>
    )
  }

  // A `<div>`, not the prototype's `<main>`: the app's layout already owns the page's one
  // landmark, and a second `<main>` inside it is invalid and announces twice.
  return (
    <div className="pt-4">
      {years.map(({ year, items }) => (
        <section key={year}>
          <YearMarker year={year} />
          {items.map((event) => (
            <TimelineEntry key={event.id} event={event} timeZone={timeZone} />
          ))}
        </section>
      ))}
    </div>
  )
}

/**
 * The year, printed once where it changes.
 *
 * Kept quiet on purpose: it is orientation, not a headline, and it is what lets every date below
 * it stay short — "Sat 25 Jul", never "Sat 25 Jul 2026".
 */
function YearMarker({ year }: { year: string }) {
  return (
    // Same three columns as an entry, so the year sits in the date gutter and
    // its dot lands dead on the spine that runs through every node below it.
    <div className="relative grid grid-cols-[104px_26px_minmax(0,1fr)] gap-x-5 pt-4 pb-6">
      <span
        aria-hidden
        className="absolute top-[22px] bottom-0 left-[137px] w-px -translate-x-1/2 bg-rule"
      />
      <div className="text-[12px] font-semibold tracking-[0.06em] text-muted-foreground">
        {year}
      </div>
      <span className="relative z-10 mt-1 justify-self-center">
        <span className="block size-[7px] rounded-full bg-rule ring-4 ring-ground" />
      </span>
    </div>
  )
}
