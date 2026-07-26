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
 */

import { useMemo } from 'react'
import { Link } from 'react-router'

import { useFeed, useMembers, useSession, useUpcoming } from '../api/queries'
import { ErrorState, FeedSkeleton } from '../components'
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
  const events = feed.data?.pages.flatMap((page) => page.events) ?? []
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

              {feed.isPending && events.length === 0 ? (
                <FeedSkeleton />
              ) : (
                <Timeline events={events} timeZone={timeZone} />
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

            <ContextColumn upcoming={upcoming.data?.events} timeZone={timeZone} />
          </div>
        </div>
      </div>
    </PeopleProvider>
  )
}
