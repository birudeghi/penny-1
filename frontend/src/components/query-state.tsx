/**
 * THE FEED'S STATE TABLE. What is on screen while a query is in flight, when it fails, and when
 * the record is incomplete in a way the events themselves cannot show.
 *
 * | state   | what renders                                                                     |
 * |---------|----------------------------------------------------------------------------------|
 * | loading | `FeedSkeleton`, and only on a COLD load — never over a record already on screen.  |
 * | empty   | one sentence, drawn by `timeline.tsx`, not from this file.                        |
 * | error   | `ErrorState`, in place, above data that never left the screen.                    |
 * | partial | `RereadBanner` — what Penny has ingested and has not read yet.                    |
 *
 * "Partial" is the one that is an addition rather than a refusal, and it is the reason this file
 * matters: the list is a success, it is complete as far as it goes, and it does not go as far as
 * the family will assume. It is the only one of the four that says something the events cannot.
 *
 * WHY LOADING NEVER FIRES ON A REFRESH. This feed polls every sixty seconds and refetches on
 * every window focus. A skeleton keyed on "a request is in flight" therefore fires on a screen
 * that is already showing the right answer — so a daughter who opens the laptop at 3am to check
 * whether anyone logged her mother's fall watches the record she came for blink out and get
 * replaced by grey rectangles. She did not ask for anything; nothing was wrong. `feed.tsx` gates
 * the skeleton on there being no answer YET, which is why the gate lives in the route and not
 * here: only the route knows whether the server has ever replied.
 *
 * WHY THE ERROR STAYS ON SCREEN. An earlier draft of this file made the background error a
 * self-dismissing toast, on the reasoning that a failed poll is an event rather than a state. The
 * ported design renders `ErrorState` in place instead, and it wins: the retry it offers is the
 * one thing a family can actually do, and on a cold failure there is no last-good record beneath
 * it for a toast to be gentle about. The toast and its "No entries yet." twin were removed rather
 * than left exported, because a second unused way to say the same thing is how two screens start
 * disagreeing about what a failure looks like.
 */

import { ApiError } from '../api/client'
import { formatDate } from '../lib/datetime'
import type { PendingReread } from '../types/api'
import styles from './states.module.css'

export interface ErrorStateProps {
  error: unknown
  title?: string
  onRetry?: () => void
}

/**
 * A SETTLED failure, rendered in place, next to whatever the person just asked for.
 *
 * The retry button is offered for 5xx and transport failures only. A 4xx is a settled answer —
 * retrying a 422 produces the identical 422, and retrying a 401 just delays the login redirect
 * behind another spinner — so the button is absent rather than present and useless. Same rule the
 * query client enforces automatically, made visible here.
 *
 * Not for background queries: the module docstring explains why the feed uses this too.
 */
export function ErrorState({ error, title = 'That did not load', onRetry }: ErrorStateProps) {
  const apiError = error instanceof ApiError ? error : null
  const retryable = apiError === null || apiError.status >= 500
  const detail =
    error instanceof Error ? error.message : 'Something went wrong. Try again in a moment.'

  return (
    <div className={styles.error} role="alert">
      <h3 className={styles.errorTitle}>{title}</h3>
      <span className={styles.errorDetail}>{detail}</span>
      {retryable && onRetry ? (
        <button type="button" className={styles.retry} onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  )
}

/** Grouped digits, so "412" and "12,480" are told apart at a glance rather than read. */
const COUNT = new Intl.NumberFormat('en-GB')

/**
 * "Penny has not read 412 messages yet, from 4 March to 19 July." The PARTIAL state.
 *
 * THE FAILURE THIS PREVENTS. A stretch Penny has not read yet renders exactly like a stretch
 * where nothing happened: both are an absence of cards. Re-extraction is started by clearing the
 * message cursor — the onboarding interview clears the whole household's, so a corrected care
 * filter reaches history that was read under the wrong one — and the monthly budget aborts that
 * re-read mid-flight by design. So a family can arrive at a feed whose older half has simply not
 * been looked at, read the silence as "nothing happened in March", and lose the thread through
 * the very mechanism built to keep it. Nothing in the list can correct that impression, because
 * the missing entries are missing. Only a sentence about the cursor can.
 *
 * WHAT IT REFUSES TO SAY, which is most of the work. Nothing about what happens next: no
 * progress bar, no percentage, no "this should be done within the hour". The re-read resumes on
 * a schedule governed by a budget that is allowed to abort it again, so every promise about
 * timing is one the product cannot keep, and a broken promise here costs more than the silence
 * it replaced. It also never says "everything after 19 July has been read" — the unread set can
 * have holes in it, and a reassurance the server cannot support is worse in a medical record
 * than no reassurance at all.
 *
 * It has no dismiss button and needs none. The value is recomputed from the live cursor on every
 * poll, so when the last message is read the banner is gone at the next sixty-second tick, with
 * nothing to clear and nothing to remember. A dismiss control would let somebody permanently
 * hide the one sentence on the screen that says the record is incomplete.
 */
export function RereadBanner({
  pending,
  timeZone,
}: {
  pending: PendingReread
  timeZone: string
}) {
  const from = formatDate(pending.first_sent_at, timeZone)
  const to = formatDate(pending.last_sent_at, timeZone)
  const messages = pending.message_count === 1 ? 'message' : 'messages'

  return (
    <aside className={styles.banner}>
      Penny has not read{' '}
      <span className={styles.bannerFact}>
        {COUNT.format(pending.message_count)} {messages}
      </span>{' '}
      yet
      {/*
        One date when the unread messages all fall on the same day, which is the ordinary case
        for a household that is merely a few minutes behind. "from 19 July to 19 July" reads as
        a rendering bug, and it undermines a sentence whose only job is to be believed.
      */}
      {from === to ? (
        <>
          {' '}
          from <span className={styles.bannerFact}>{from}</span>
        </>
      ) : (
        <>
          {' '}
          from <span className={styles.bannerFact}>{from}</span> to{' '}
          <span className={styles.bannerFact}>{to}</span>
        </>
      )}
      . Entries from that stretch may still be missing here.
    </aside>
  )
}

/** Three, because it reads as "a list is coming" without pretending to predict how long one is. */
const SKELETON_ROWS = [1, 2, 3]

/**
 * A COLD load only. `feed.tsx` is what enforces that, and the enforcement is the whole design.
 *
 * An earlier draft of this component returned `null` — loading renders nothing, the last-good
 * record stays put. That is right for the ninety-nine refreshes out of a hundred that happen over
 * a screen which is already correct, and wrong for the one case this component is actually for:
 * the first paint, where there is no record underneath and "nothing" means a blank white page
 * while a family waits to find out whether their mother's history loaded. The route decides which
 * of the two it is looking at; this file just draws the one it is handed.
 */
export function FeedSkeleton({ label = 'Loading the feed' }: { label?: string }) {
  return (
    <div className={styles.skeleton} aria-busy="true">
      <span className={styles.srOnly}>{label}</span>
      {SKELETON_ROWS.map((row) => (
        <div key={row} className={styles.skeletonCard} aria-hidden="true" />
      ))}
    </div>
  )
}
