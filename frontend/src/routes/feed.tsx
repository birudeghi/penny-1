/**
 * The care record: who it is about, everything that has happened, and the standing facts beside
 * it. Newest first.
 *
 * ONE MEASURE FOR THE WHOLE DOCUMENT. The name and the timeline start on the same left edge,
 * which is what makes the page read as a record rather than as stacked widgets. That measure is
 * 1400px and the rest of the app's is 880px, so this is the one screen that breaks out of the
 * layout's reading column — see `.bleed` in `routes.module.css` for how, and why the ground has
 * to reach the window edges for the design to hold together at all.
 *
 * Everything renders in the HOUSEHOLD's timezone, taken from the session rather than from the
 * browser, so two siblings in two countries see the same day over the same events.
 *
 * `.record` is not decoration: it scopes the ported Tailwind reset, because the other five
 * screens are still CSS Modules and would be restyled by a global one. `design.css` explains it.
 *
 * "NO ENTRIES YET." IS A CLAIM ABOUT A FAMILY'S MEDICAL RECORD, and this route decides when it is
 * safe to make. `Timeline` draws that sentence whenever it is handed an empty list, so it must not
 * be rendered until the server has actually answered — otherwise a failed cold load tells a family
 * their record is empty when the truth is that we could not read it, which is the single most
 * alarming thing this screen could do. `answered` below is that gate.
 */

import { useMemo } from 'react'
import { Link } from 'react-router'

import { useFeed, useMembers, useSession, useUpcoming } from '../api/queries'
import { ErrorState, FeedSkeleton, RereadBanner } from '../components'
import { ContextColumn } from '../components/context-column'
import { PersonHeader } from '../components/person-header'
import { Timeline } from '../components/timeline'
import { createPeople, PeopleProvider } from '../lib/people'
import styles from './routes.module.css'

/** Only used before the session lands; every real render uses the household's own zone. */
const FALLBACK_TIMEZONE = 'Europe/London'

export function FeedRoute() {
  const session = useSession()
  const feed = useFeed()
  const upcoming = useUpcoming()
  const members = useMembers()

  const household = session.data?.household
  const timeZone = household?.timezone ?? FALLBACK_TIMEZONE
  const pages = feed.data?.pages
  const events = pages?.flatMap((page) => page.events) ?? []

  // `pages` is undefined until the FIRST response lands and stays defined through every later
  // refetch, failed ones included. That is exactly the "do we know anything yet?" question the
  // empty state needs, and it is why this is not `!feed.isPending`: a household whose cold load
  // failed is not pending and has no data, and that combination must not draw an empty record.
  const answered = pages !== undefined

  // Page 0 is the newest page and every page carries the same freshly computed answer, so this is
  // simply the most recently refreshed copy. `?? null` collapses the two absences — a caught-up
  // household and a backend that predates the field — which is right, because neither is a
  // statement that anything is missing and only a statement of that kind may raise the banner.
  const pendingReread = pages?.[0]?.pending_reread ?? null

  // Rebuilt only when the member list changes. It goes into context, so a fresh object on every
  // render would re-render every avatar and every chat bubble on the page each time the feed
  // polls — which it does, every sixty seconds.
  const people = useMemo(() => createPeople(members.data ?? []), [members.data])

  return (
    <PeopleProvider value={people}>
      <div className={`${styles.bleed} record bg-ground text-foreground`}>
        <div className="mx-auto w-full max-w-[1400px] px-8 pb-28 xl:px-12">
          {household && (
            <PersonHeader
              household={household}
              contributorCount={members.data ? members.data.length : undefined}
            />
          )}

          <div className="flex min-w-0 gap-14">
            <div className="min-w-0 flex-1">
              {/* A failed refetch renders above whatever is already on screen rather than
                  replacing it: a family reading last week's appointments should not lose them
                  because a poll timed out. */}
              {feed.error ? (
                <ErrorState
                  error={feed.error}
                  title="Couldn't load the history"
                  onRetry={() => void feed.refetch()}
                />
              ) : null}

              {pendingReread ? (
                <RereadBanner pending={pendingReread} timeZone={timeZone} />
              ) : null}

              {/* Three outcomes, and the third is the point: answered → the record (which draws
                  its own one-sentence empty state); not answered and still trying → the skeleton;
                  not answered because it failed → nothing, because the ErrorState above is the
                  whole truth we have. */}
              {answered ? (
                <Timeline events={events} timeZone={timeZone} />
              ) : feed.error ? null : (
                <FeedSkeleton />
              )}

              {/* 170px is where the card column starts: the 104px date gutter, the 26px spine
                  column, and the two 20px gaps between them. Anything under the timeline lines up
                  with the cards, not with the dates. */}
              {feed.hasNextPage ? (
                <div className="pt-1 pb-6 pl-[170px]">
                  <button
                    type="button"
                    disabled={feed.isFetchingNextPage}
                    onClick={() => void feed.fetchNextPage()}
                    className="rounded-full border border-rule px-4 py-2 text-[13px] font-medium text-penny transition-colors hover:bg-band disabled:opacity-50"
                  >
                    {feed.isFetchingNextPage ? 'Loading…' : 'Load older entries'}
                  </button>
                </div>
              ) : null}

              {/* `next_before: null` is the server saying there is nothing older — say so, don't
                  just stop. */}
              {feed.isSuccess && !feed.hasNextPage && events.length > 0 ? (
                <p className="pl-[170px] text-[13px] text-muted-foreground">
                  That is the whole history Penny has.{' '}
                  <Link to="/import" className="text-penny hover:underline">
                    Import an older export
                  </Link>{' '}
                  to go further back.
                </p>
              ) : null}
            </div>

            <ContextColumn
              upcoming={upcoming.data?.events}
              vitals={household?.vitals}
              timeZone={timeZone}
            />
          </div>
        </div>
      </div>
    </PeopleProvider>
  )
}
