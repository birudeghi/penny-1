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
 * focus — because correcting is rare and reading is not. It is out of the way ONLY where hovering
 * is possible: `sm:pointer-fine:opacity-0` means a touchscreen shows it at rest, because a control
 * revealed by a hover that a finger cannot perform is a control that does not exist, and the
 * failure it causes is the specific one above — a wrong dose nobody could reach the button to fix.
 *
 * ── BELOW `sm` THE SPINE AND THE DATE GUTTER ARE GONE, DELIBERATELY ───────────────────────────
 *
 * The three-column grid costs 170px before the first character of content: a 104px date gutter, a
 * 26px node column, and two 20px gaps. Inside a 375px phone with the shell's own 32px margins that
 * leaves 141px for the record itself — a headline wrapping every three words, which is not a
 * degraded reading experience but an unreadable one. This is the record a family opens at 3am in a
 * hospital corridor, on a phone, so `lg` and up cannot be the only layout that works.
 *
 * The design's rules for this row, and what each becomes:
 *
 * 1. THE DATE GUTTER IS LEFT-ALIGNED so the date's first character sits on the same vertical as
 *    the care recipient's name and every year marker — one straight line down the left edge of the
 *    whole page. Below `sm` the gutter is dropped and the date moves inside the card, above the
 *    headline. That rule is *preserved by dropping it*: with no gutter, the card itself starts on
 *    the page's left edge, so the one straight line is still there and now the record is on it.
 *    Keeping a 104px gutter on a phone would have been the one thing that broke the rule it exists
 *    to serve.
 *
 * 2. THE SPINE RUNS UNBROKEN THROUGH EVERY NODE. It goes, and losing it is the decision rather
 *    than an oversight. Its geometry is not decoration: `left-[137px]` is derived from
 *    `104 + 20 + 13`, so a spine on a phone would be a line with nothing left of it, drawn 137px
 *    into a 311px measure. Its job is continuity across a history that runs for pages, and at a
 *    phone's two-and-a-half entries per screen there is almost no distance for it to carry
 *    continuity over. So it costs 12% of the measure to do a job the screen has already done.
 *
 * 3. THE KIND NODE SURVIVES, because it is not the spine's ornament — it is the only thing that
 *    says what kind of entry this is (see `kind-node.tsx`). It moves inside the card at the small
 *    18px disc, beside the date, and WITHOUT its ring: `ring-4 ring-ground` is a mask that only
 *    works on the ground colour, and on a white card it would read as a visible grey halo.
 *
 * 4. THE CARD'S BORDER IS TRANSPARENT UNTIL HOVER so nothing shifts by a pixel. Unchanged at every
 *    width — the border is still reserved, never added — even though a phone will rarely fire the
 *    hover that colours it.
 *
 * 5. THE PHOTOGRAPH IS FILED, NOT PUBLISHED — a square in the card's right margin. It stops being
 *    a margin object and becomes a capped-width block under the text, and it stops being deleted
 *    outright below `sm`, which is what it used to be. The comment on the card's inner grid has
 *    the argument, including why that one switches a breakpoint later than everything here.
 *
 * The breakpoint is `sm` (640px), not `lg`. Between 640 and 1024 the grid costs 170px of a 576px
 * measure, which is tight but honest; below 640 it is most of the screen. `lg` is where the context
 * column arrives, which is a separate question about a separate column.
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
   * mirror stops being one. Nothing passes it today; see `EntryPhoto` for the contract it is
   * shaped against and for what happens when the bytes do not arrive.
   */
  image?: { messageId: string; description: string | null }
}) {
  const [editing, setEditing] = useState(false)
  const people = usePeople()
  const update = useUpdateEvent()
  const remove = useDeleteEvent()

  const date = formatTimelineDate(event.occurred_at, timeZone)
  const { day, weekday } = splitDate(date)
  const at = entryTimeLabel(event, timeZone)
  const kind = timelineKind(event)

  return (
    <article className="relative pb-7 sm:grid sm:grid-cols-[104px_26px_minmax(0,1fr)] sm:gap-x-5">
      {/* The spine runs the full height of every entry, so consecutive nodes
          join into one unbroken line down the page. Gone below `sm`; the
          component docstring argues why that is a decision and not a loss. */}
      <span
        aria-hidden
        className="absolute inset-y-0 left-[137px] hidden w-px -translate-x-1/2 bg-rule sm:block"
      />

      {/* Left-aligned, not right: it puts the date's first character on the
          same vertical as the care recipient's avatar and every year marker,
          so one straight line runs down the left edge of the whole page. */}
      <div className="hidden pt-3.5 sm:block">
        <div className="text-[14px] leading-none font-semibold tracking-tight">{day}</div>
        {weekday && (
          <div className="mt-1.5 text-[13px] leading-none text-muted-foreground">{weekday}</div>
        )}
      </div>

      <span className="relative z-10 mt-3 hidden sm:block">
        <KindNode kind={kind} onSpine />
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
          {/*
            A grid at `md`, and plain block flow below it, because the three pieces want two
            different orders and only one of them may be the DOM's.

            Reading down a phone the order is the entry, then the photograph filed with it, then
            the control that opens the messages behind it. On a wide screen the photograph moves
            into a column of its own on the right and the evidence trigger sits directly under the
            provenance line, where the design puts it — time, headline, summary, provenance,
            trigger, in one unbroken run. Placing the two text blocks in column one and spanning
            the photograph down both rows gets that second order out of the first, so nothing is
            duplicated and no reader is handed a sequence that does not match what they see.

            AND IT SWITCHES AT `md`, NOT AT `sm` LIKE THE ROW AROUND IT. The two breakpoints are
            measuring different things. The row's is about the 170px margin: at 640px that margin
            has stopped being most of the screen, so the gutter comes back. The photograph's is
            about what is left after it: at 640px, 170px of margin plus a 168px square plus the
            gaps leaves under 200px for the headline, which is a worse card than one with a wide
            photograph under the text. So the square waits for 768px and is full width until then.
          */}
          <div className="md:grid md:grid-cols-[minmax(0,1fr)_168px] md:gap-x-5">
            <div className="min-w-0 md:col-start-1 md:row-start-1">
              <div className="flex items-center gap-2.5 sm:items-baseline sm:gap-3">
                {/* Below `sm` this row is the date gutter, folded into the card: the node the
                    spine used to carry, then the date the margin used to print. Two elements
                    rendered here and hidden at `sm` rather than moved by CSS — `display: none`
                    keeps the hidden copy out of the accessibility tree too, so the kind is
                    announced exactly once at every width. */}
                <span className="sm:hidden">
                  <KindNode kind={kind} size="sm" />
                </span>
                <span className="text-[13px] font-semibold tracking-tight tabular-nums sm:hidden">
                  {date}
                </span>

                {at && (
                  <span className="text-[13px] text-muted-foreground tabular-nums">{at}</span>
                )}
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  // Twenty identical "Edit" buttons are indistinguishable to a screen reader, so
                  // the entry's title goes in the accessible name rather than in a tooltip.
                  aria-label={`Edit “${event.title}”`}
                  className={cn(
                    'ml-auto text-[12.5px] text-muted-foreground transition-opacity',
                    'sm:pointer-fine:opacity-0 group-hover:opacity-100 hover:text-foreground',
                    'focus-visible:opacity-100',
                  )}
                >
                  Edit
                </button>
              </div>

              <h3 className="mt-1.5 text-[17.5px] leading-snug font-semibold tracking-tight text-balance">
                {event.title}
              </h3>

              {event.body && (
                <p className="mt-1.5 max-w-[68ch] text-[14px] leading-relaxed break-words text-muted-foreground">
                  {event.body}
                </p>
              )}

              <p className="mt-2.5 text-[12.5px] text-muted-foreground">
                {provenanceOf(event, timeZone, people)}
              </p>
            </div>

            {/*
              Inside the card, and square: the photograph is filed with the entry, not published
              under it. Narrower than `md` it stops being a square in the margin and becomes the
              width of the card — but capped at 280px, so that on a phone it is as big as the
              screen can make it and on a tablet it is a photograph filed under an entry rather
              than a photograph the entry has been reduced to captioning. It used to be
              `hidden sm:block`, which on the one screen that matters most deleted the best
              evidence in the record — a photo of a rash beats every description of one, and the
              phone is where it will actually be looked at.

              `key` is the message id so a re-extraction that files a different photo under the
              same entry gets a fresh element, and does not inherit the previous one's failure.
            */}
            {image && (
              <div className="mt-4 w-full max-w-[280px] md:col-start-2 md:row-span-2 md:row-start-1 md:mt-0 md:w-[168px] md:self-start">
                <EntryPhoto
                  key={image.messageId}
                  messageId={image.messageId}
                  description={image.description}
                />
              </div>
            )}

            {/* `empty:hidden` because `Evidence` renders nothing for a hand-written entry, and a
                margin under nothing is still a margin — a card with an invisible 14px of extra
                foot is exactly the kind of drift that makes a list of cards look hand-spaced. */}
            <div className="mt-3.5 empty:hidden md:col-start-1 md:row-start-2">
              <Evidence excerpts={event.source_excerpts} timeZone={timeZone} />
            </div>
          </div>
        </div>
      )}
    </article>
  )
}

/**
 * The path the media endpoint serves a message's photo on.
 *
 * SAME ORIGIN, and that is the whole authentication story: the session cookie rides along
 * automatically, so there is no signed URL, no token in a query string, and nothing to end up in a
 * browser history, a referrer header or a screenshot of a URL bar. A care record's photographs are
 * the most identifying thing in it.
 */
function mediaSrc(messageId: string): string {
  return `/api/media/${encodeURIComponent(messageId)}`
}

/**
 * A photograph the family shared, and Penny's reading of it, kept visibly apart.
 *
 * WHOSE WORDS ARE WHOSE IS THE ENTIRE POINT OF THIS COMPONENT. The photo is the family's: someone
 * held a phone over a swollen ankle and pressed send, and that image is a primary artefact of the
 * record. The description is not — it is a model's account of what it thinks it can see, and a
 * model that says "a mild rash on the left forearm" about a photograph of a pressure sore has
 * produced a sentence a GP could act on. So the description is never printed as a caption, which
 * is the form a reader takes to be the family's own words; it is printed as Penny's description,
 * named as such, every time it appears.
 *
 * THAT RULE HOLDS FOR A SCREEN READER TOO, which is why the `alt` is prefixed rather than bare.
 * A sighted reader receives two things — the photograph, and a caption saying where it came from.
 * A screen-reader user receives one string, and if that string is the bare derived text then the
 * model's guess arrives with exactly the authority of a human caption, indistinguishable from
 * something the family wrote. The prefix costs four words and puts both facts in the one channel
 * that reader has.
 *
 * A PHOTO THAT DOES NOT LOAD DEGRADES TO THE TEXT, never to a broken-image glyph. The media
 * endpoint can 404 on a message whose bytes WhatsApp has since dropped, 401 on an expired session,
 * or 500 on a bad day, and the browser's default answer to all three is a torn-page icon and the
 * alt text rendered as if it were an error message. What is left when a photo fails is still the
 * most useful thing available — Penny's description of it — so that is what is shown, in a band
 * block that holds roughly the space the photo held, with a caption that says plainly that the
 * photo could not be loaded and that these are Penny's words about it.
 *
 * IT DOES NOT DECIDE ITS OWN WIDTH. The caller places it — 168px in a column of its own on a wide
 * screen, the full width of the card on a phone — and everything here is relative to that. A
 * component that hard-codes the one measure it was first drawn at is the reason the old version
 * was `hidden sm:block`: it could not be any other size, so below `sm` it was deleted instead.
 *
 * Exported so its contract can be tested without standing up a query client and a people
 * provider around a whole entry.
 */
export function EntryPhoto({
  messageId,
  description,
}: {
  /** The message the photo arrived on. `/api/media/{message_id}` serves the bytes. */
  messageId: string
  /** Penny's reading of the photo. Null when she has not described it — an empty string is the
   *  same absence, and is treated as one rather than rendered as a blank caption. */
  description: string | null
}) {
  const [failed, setFailed] = useState(false)
  const derived = description?.trim() || null

  return (
    // `lg` (10px) is the radius for a block that is not a card — DESIGN.md §1.5 — and `--band` is
    // the surface a photograph sits on, so the block behind a photo that has not arrived is the
    // same colour as the block behind one that has.
    <figure className="w-full">
      {failed ? (
        derived && (
          <p className="rounded-lg bg-band px-3.5 py-3 text-[12.5px] leading-relaxed break-words">
            {derived}
          </p>
        )
      ) : (
        <div className="aspect-[4/3] w-full overflow-hidden rounded-lg bg-band md:aspect-square">
          <img
            src={mediaSrc(messageId)}
            alt={altFor(derived)}
            loading="lazy"
            decoding="async"
            onError={() => setFailed(true)}
            className="size-full object-cover"
          />
        </div>
      )}

      {/* The photograph's provenance line, in the same 12.5px muted the entry's own provenance
          line uses, because that is exactly what it is. */}
      <figcaption className="mt-1.5 text-[12.5px] leading-snug text-muted-foreground">
        {captionFor(failed, derived)}
      </figcaption>
    </figure>
  )
}

/** See `EntryPhoto`: the prefix is what stops a model's description arriving as a human caption. */
function altFor(derived: string | null): string {
  if (!derived) return 'Photo shared in the chat. Penny has not described it.'
  return `Photo shared in the chat. Penny's description: ${derived}`
}

function captionFor(failed: boolean, derived: string | null): string {
  if (!failed) return 'Photo shared in the chat'
  if (derived) return "Penny's description — the photo itself could not be loaded."
  return 'A photo was shared in the chat. It could not be loaded, and Penny has not described it.'
}
