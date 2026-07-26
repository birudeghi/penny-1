import { useEffect, useRef } from 'react'

import { useEventPhotos } from '@/api/queries'
import { formatTimelineDate } from '@/lib/datetime'
import type { Photo } from '@/types/api'

/**
 * One photograph, large enough to be evidence, with the exact claim we can make about it.
 *
 * WHY THIS EXISTS AT ALL. In the card a photograph is filed, not published — a 168px square, or
 * one of a strip when there are several. That is the right size for *finding* a picture and the
 * wrong size for *reading* one, and the whole reason a family photographs a swollen ankle is so
 * somebody can look closely at it. A thumbnail that cannot be opened is decoration.
 *
 * A DIALOG AND NOT A NEW TAB, and that is a privacy decision rather than a UI preference.
 * `routers/media.py` serves these bytes as `private, no-store` and its file header is explicit
 * that a photograph of an identifiable person must not linger on a shared family laptop.
 * `window.open('/api/media/…')` writes that URL into browser history, which quietly undoes it.
 * A dialog changes no URL, leaves no history entry, and reuses the image the browser already has.
 *
 * NO ENTRANCE ANIMATION, deliberately. `DESIGN.md` §5.2 says there is no motion system and that a
 * `prefers-reduced-motion` policy is required before any motion ships. The product's entire
 * vocabulary today is `transition-colors` on hover and one `rotate-90` chevron. A fade here would
 * answer an open design question by accident, inside a feature, with a Tailwind default. It
 * appears instantly, like everything else, until somebody designs the motion system on purpose.
 *
 * IT PAGES THROUGH EVERYTHING, NOT JUST WHAT THE CARD SHOWED. The feed caps a card at four
 * photographs so a 500-entry page has a knowable size, and the card says "+N more" rather than
 * silently dropping evidence. This is where those N live: `useEventPhotos` fetches the entry's
 * full set the moment the dialog opens, and until it arrives the photo that was clicked is
 * already on screen from the card's own data. Nobody waits to see the picture they tapped.
 */
export function PhotoLightbox({
  eventId,
  photos,
  index,
  timeZone,
  onIndexChange,
  onClose,
}: {
  /** The entry whose photographs these are. Null closes the dialog. */
  eventId: string | null
  /** What the card had — shown immediately, replaced by the full set when it lands. */
  photos: Photo[]
  index: number
  timeZone: string
  onIndexChange: (next: number) => void
  onClose: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const full = useEventPhotos(eventId)

  // The full set when it has arrived, the card's four until then. Never empty while open, so
  // there is no frame where the dialog is up and showing nothing.
  const all = full.data?.photos?.length ? full.data.photos : photos
  const current = all[Math.min(index, all.length - 1)]

  /* `showModal()` and not the `open` attribute: only the modal form gives the top layer, the
     backdrop, the focus trap and Escape-to-close for free. Doing this in an effect rather than
     in render is what keeps the dialog's open state owned by the caller's `eventId`. */
  useEffect(() => {
    const node = ref.current
    if (!node) return
    if (eventId !== null && !node.open) node.showModal()
    if (eventId === null && node.open) node.close()
  }, [eventId])

  /* Arrow keys, because a dialog full of photographs that only responds to clicking is a dialog
     nobody pages through. Escape is the browser's, already. */
  useEffect(() => {
    if (eventId === null) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'ArrowRight') onIndexChange(Math.min(index + 1, all.length - 1))
      if (event.key === 'ArrowLeft') onIndexChange(Math.max(index - 1, 0))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [eventId, index, all.length, onIndexChange])

  if (eventId === null || !current) return null

  return (
    <dialog
      ref={ref}
      /* `onClose` fires for Escape and for `close()` alike, so the caller's state can never be
         left thinking the dialog is open after the browser has shut it. `onClick` on the element
         itself is the backdrop: a click that lands on <dialog> rather than on its child content
         is by definition outside the content. */
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
      className="max-h-full max-w-full bg-transparent p-0 backdrop:bg-black/80"
      aria-label="Photograph"
    >
      <div className="flex max-h-[100dvh] w-[100dvw] flex-col items-center justify-center gap-4 p-4 sm:p-8">
        {/* `object-contain`, never `object-cover`. The card crops to a square because it is
            filing; here the whole point is to see the picture, and a crop can hide the edge of
            exactly the swelling the photograph was taken to show. */}
        <img
          src={`/api/media/${encodeURIComponent(current.message_id)}`}
          alt={altFor(current)}
          className="max-h-[70dvh] min-h-0 max-w-full flex-1 rounded-lg object-contain"
        />

        <figcaption className="w-full max-w-[68ch] shrink-0 text-[13px] leading-relaxed text-white/90">
          <p>{photoProvenance(current, timeZone)}</p>
          {current.description ? (
            /* NAMED AS PENNY'S, ALWAYS. On a stored photograph this sentence is never the
               family's caption — the webhook drops captions at the door and a message that
               already has text is never downloaded — so attributing it to a model is not a
               hedge, it is the truth. `docs/api-contract.md` carries the proof. */
            <p className="mt-1 text-white/60">Penny’s description: {current.description}</p>
          ) : null}
        </figcaption>

        <div className="flex shrink-0 items-center gap-5 text-[13px] text-white/70">
          <button
            type="button"
            onClick={() => onIndexChange(Math.max(index - 1, 0))}
            disabled={index === 0}
            className="min-h-[44px] px-3 hover:text-white disabled:opacity-30"
          >
            ← Previous
          </button>
          <span className="tabular-nums" aria-live="polite">
            {index + 1} of {all.length}
          </span>
          <button
            type="button"
            onClick={() => onIndexChange(Math.min(index + 1, all.length - 1))}
            disabled={index >= all.length - 1}
            className="min-h-[44px] px-3 hover:text-white disabled:opacity-30"
          >
            Next →
          </button>
          <button type="button" onClick={onClose} className="min-h-[44px] px-3 hover:text-white">
            Close
          </button>
        </div>
      </div>
    </dialog>
  )
}

/**
 * The one sentence we can honestly make about where this photograph came from.
 *
 * `cited` is the strong claim: the model read this picture and attached it to this entry. Uncited
 * is the weak one, and it has to SAY it is weak — a photograph shared the same day that Penny
 * judged relevant is not evidence for the entry it happens to sit next to, and rendering it as
 * though it were is how a holiday snap ends up looking like proof of a symptom.
 */
function photoProvenance(photo: Photo, timeZone: string): string {
  const who = photo.sender || 'Someone'
  const when = formatTimelineDate(photo.sent_at, timeZone)
  return photo.cited
    ? `${who} sent this with the messages behind this entry — ${when}.`
    : `${who} shared this around the same time — ${when}. Penny did not link it to this entry.`
}

/** Screen readers get the provenance too, and never the description as if a person wrote it. */
function altFor(photo: Photo): string {
  const who = photo.sender || 'someone'
  if (!photo.description) return `Photograph shared by ${who}. Penny has not described it.`
  return `Photograph shared by ${who}. Penny’s description: ${photo.description}`
}
