import { useState } from 'react'

import { Evidence } from '@/components/evidence'
import { EventEditor } from '@/components/event-editor'
import { KindNode } from '@/components/kind-node'
import { useDeleteEvent, useUpdateEvent } from '@/api/queries'
import { formatTimelineDate } from '@/lib/datetime'
import { usePeople } from '@/lib/people'
import { entryTimeLabel, provenanceOf, timelineKind } from '@/lib/timeline'
import { cn } from '@/lib/utils'
import type { Event } from '@/types/api'

const WEEKDAY: Record<string, string> = {
  Mon: 'Monday',
  Tue: 'Tuesday',
  Wed: 'Wednesday',
  Thu: 'Thursday',
  Fri: 'Friday',
  Sat: 'Saturday',
  Sun: 'Sunday',
}

/**
 * Splits "Sat 25 Jul" into the two lines the gutter prints.
 *
 * Done here rather than in the data so each entry keeps one short date string — the presentation
 * decided how to break it, and the record should not have to be rewritten when that changes again.
 */
function splitDate(date: string): { day: string; weekday: string } {
  const [abbrev, ...rest] = date.split(' ')
  const full = abbrev && WEEKDAY[abbrev]
  if (!full) return { day: date, weekday: '' }
  // Wednesday and Thursday are the two longest weekday names — spelled out
  // under a short date, they wrap in the narrow gutter and stop it looking
  // quiet. Dropping just those two keeps the rest legible without the clutter.
  const weekday = abbrev === 'Wed' || abbrev === 'Thu' ? '' : full
  return { day: rest.join(' '), weekday }
}

/**
 * One entry in the record: a date in the margin, a node on the spine, and a card holding what
 * happened.
 *
 * The card is back because the entries now run to years rather than a week — at that length
 * continuous text stops reading as a list and starts reading as a wall. A white card on tinted
 * ground gives each entry an edge without a rule having to draw one.
 *
 * THE EDIT BUTTON IS NOT IN THE DESIGN, and it is here anyway. The prototype's entries were
 * fixtures; these were written by a language model reading somebody's family chat, and it gets
 * things wrong. A record a family cannot correct is a record they stop trusting the first time
 * they find a wrong dose in it. It stays out of the way — muted, and only on hover or keyboard
 * focus — because correcting is rare and reading is not.
 */
export function TimelineEntry({
  event,
  timeZone,
  image,
}: {
  event: Event
  timeZone: string
  /**
   * A photograph filed with the entry.
   *
   * A PROP AND NOT A FIELD ON `Event`, because it is not in the API contract and `types/api.ts`
   * is a mirror of that contract — adding a field there to make one component compile is how the
   * mirror stops being one. Nothing passes it today; see the render site below for why it exists.
   */
  image?: { src: string; alt: string }
}) {
  const [editing, setEditing] = useState(false)
  const people = usePeople()
  const update = useUpdateEvent()
  const remove = useDeleteEvent()

  const { day, weekday } = splitDate(formatTimelineDate(event.occurred_at, timeZone))
  const at = entryTimeLabel(event, timeZone)

  return (
    <article className="relative grid grid-cols-[104px_26px_minmax(0,1fr)] gap-x-5 pb-7">
      {/* The spine runs the full height of every entry, so consecutive nodes
          join into one unbroken line down the page. */}
      <span
        aria-hidden
        className="absolute inset-y-0 left-[137px] w-px -translate-x-1/2 bg-rule"
      />

      {/* Left-aligned, not right: it puts the date's first character on the
          same vertical as the care recipient's avatar and every year marker,
          so one straight line runs down the left edge of the whole page. */}
      <div className="pt-3.5">
        <div className="text-[14px] leading-none font-semibold tracking-tight">{day}</div>
        {weekday && (
          <div className="mt-1.5 text-[13px] leading-none text-muted-foreground">{weekday}</div>
        )}
      </div>

      <span className="relative z-10 mt-3">
        <KindNode kind={timelineKind(event)} onSpine />
      </span>

      {editing ? (
        <EventEditor
          event={event}
          timeZone={timeZone}
          isSaving={update.isPending}
          error={update.error}
          onCancel={() => {
            update.reset()
            setEditing(false)
          }}
          onSave={(patch) => {
            // Nothing changed: close rather than send an empty PATCH that would still stamp
            // `edited_at` and permanently freeze the row against re-extraction.
            if (Object.keys(patch).length === 0) {
              setEditing(false)
              return
            }
            update.mutate({ id: event.id, patch }, { onSuccess: () => setEditing(false) })
          }}
          onDelete={() => {
            setEditing(false)
            remove.mutate(event.id)
          }}
        />
      ) : (
        /* The border is transparent until hover rather than absent, so the card
           does not shift by a pixel when the outline arrives. At rest the white
           against the tinted ground is edge enough. */
        <div
          className={cn(
            'group min-w-0 rounded-xl border border-transparent bg-card p-5',
            'shadow-[0_1px_2px_rgb(0_0_0/0.03)] transition-colors hover:border-rule',
          )}
        >
          <div className="flex gap-5">
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-3">
                <span className="text-[13px] text-muted-foreground tabular-nums">{at}</span>
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  // Twenty identical "Edit" buttons are indistinguishable to a screen reader, so
                  // the entry's title goes in the accessible name rather than in a tooltip.
                  aria-label={`Edit “${event.title}”`}
                  className={cn(
                    'ml-auto text-[12.5px] text-muted-foreground opacity-0 transition-opacity',
                    'group-hover:opacity-100 hover:text-foreground focus-visible:opacity-100',
                  )}
                >
                  Edit
                </button>
              </div>

              <h3 className="mt-1.5 text-[17.5px] leading-snug font-semibold tracking-tight text-balance">
                {event.title}
              </h3>

              {event.body && (
                <p className="mt-1.5 max-w-[68ch] text-[14px] leading-relaxed text-muted-foreground">
                  {event.body}
                </p>
              )}

              <p className="mt-2.5 text-[12.5px] text-muted-foreground">
                {provenanceOf(event, timeZone, people)}
              </p>

              <div className="mt-3.5">
                <Evidence excerpts={event.source_excerpts} timeZone={timeZone} />
              </div>
            </div>

            {/*
              Inside the card, and square: the photograph is filed with the entry, not published
              under it.

              NOTHING SUPPLIES THIS YET. There is no media storage behind the API — a photo of a
              rash arrives in WhatsApp as a `<Media omitted>` placeholder and stops there — so the
              prop is always absent today. It is kept because the decision it encodes was made
              (filed, not published) and because the day the media path lands, this is one field
              on the event and not a redesign of the card.
            */}
            {image && (
              <div className="hidden size-[168px] shrink-0 overflow-hidden rounded-lg bg-band sm:block">
                <img src={image.src} alt={image.alt} className="size-full object-cover" />
              </div>
            )}
          </div>
        </div>
      )}
    </article>
  )
}
