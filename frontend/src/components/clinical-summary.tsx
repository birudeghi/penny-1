/**
 * What Penny hands the GP.
 *
 * CLOSED UNTIL SOMEBODY ASKS, and that is the design, not a space saving. A summary is a reading
 * of the record at a moment, not part of it — leaving one open above the timeline would put a
 * stale interpretation between the family and the thing it interpreted, and the interpretation
 * is the part that is allowed to be wrong.
 *
 * It is also the only control in the app that spends money on purpose. One press is one LLM call
 * over a year of the record, which is why generating is a mutation and never a query: a query
 * fires on mount, on remount, on window focus and on a poll, and the server's one-a-day limit
 * would turn each of those into a 429 the family did not cause. The button, once, and nothing
 * else.
 *
 * FOUR STATES, AND THE TWO IN THE MIDDLE ARE THE ONES THAT MATTER
 *
 *   nothing yet   the trigger, and only the trigger
 *   in progress   a real in-progress state — this takes tens of seconds, so "nothing happened"
 *                 is the wrong answer for most of the time the family spends looking at it
 *   too thin      the record cannot support a summary yet. Says what will fix it, and does not
 *                 pretend a spinner is going to end in a document
 *   done          the page itself
 *
 * The panel adopts the household's most recent report on mount, so reopening it does not
 * regenerate — and so a family who navigate away while one is being written come back to it
 * still being written, rather than to a button that would charge them again.
 *
 * EVERY CLAIM CARRIES ITS CITATIONS. That is the same promise the feed's evidence disclosure
 * makes, one level up: a claim in a clinical summary is worth exactly as much as the ability to
 * find the message behind it. The server has already dropped any citation that did not resolve
 * and stripped its marker out of the prose, so everything rendered here points at a real entry.
 */

import { useState } from 'react'

import { ApiError } from '../api/client'
import { useGenerateReport, useHousehold, useReport, useReports } from '../api/queries'
import { formatDate } from '../lib/datetime'
import type { Report, ReportSection } from '../types/api'
import { cx } from './cx'
import styles from './clinical-summary.module.css'

/**
 * 422 is the server's "there is not enough in the record yet", and it is the one 4xx that gets
 * its own screen rather than an error box — it is a fact about the family's record, not a
 * failure, and the sentence it carries tells them what makes it go away.
 */
const TOO_THIN = 422

export function ClinicalSummary() {
  const [open, setOpen] = useState(false)
  const [startedId, setStartedId] = useState<string | null>(null)
  const household = useHousehold()
  const timeZone = household?.timezone ?? 'Europe/London'

  // The list is already cached by the reports screen; this is a read of it, not a second fetch.
  const reports = useReports()
  const mostRecent = reports.data?.[0]?.id ?? null
  const activeId = startedId ?? mostRecent
  const report = useReport(open ? activeId : null)
  const generate = useGenerateReport()

  const generating = generate.isPending || isWriting(report.data)

  function onTrigger() {
    if (open) {
      setOpen(false)
      return
    }
    setOpen(true)
    // Only when there is nothing to show. Reopening a summary must never buy another one.
    if (activeId === null) start()
  }

  function start() {
    generate.mutate(undefined, { onSuccess: (accepted) => setStartedId(accepted.report_id) })
  }

  return (
    <>
      <button type="button" className={styles.trigger} onClick={onTrigger} aria-expanded={open}>
        {triggerLabel({ open, generating, hasReport: activeId !== null })}
      </button>

      {open ? (
        <Body
          report={report.data}
          error={generate.error ?? report.error}
          generating={generating}
          timeZone={timeZone}
          onRegenerate={start}
        />
      ) : null}
    </>
  )
}

function triggerLabel({
  open,
  generating,
  hasReport,
}: {
  open: boolean
  generating: boolean
  hasReport: boolean
}): string {
  if (open) return 'Hide clinical summary ↑'
  if (generating) return 'Writing clinical summary…'
  return hasReport ? 'Show clinical summary ↧' : 'Generate clinical summary ↧'
}

function isWriting(report: Report | undefined): boolean {
  return report !== undefined && (report.status === 'pending' || report.status === 'running')
}

interface BodyProps {
  report: Report | undefined
  error: Error | null
  generating: boolean
  timeZone: string
  onRegenerate: () => void
}

function Body({ report, error, generating, timeZone, onRegenerate }: BodyProps) {
  if (error instanceof ApiError && error.status === TOO_THIN) {
    return (
      <State title="Not enough recorded yet">
        {/* The server's own sentence, rendered as written: it names what will fix this. */}
        {error.message}
      </State>
    )
  }
  if (generating) {
    return (
      <State title="Reading the record" busy>
        Penny is reading the last twelve months and writing them up. This takes about a minute —
        you can carry on scrolling, it will appear here when it is done.
      </State>
    )
  }
  if (error) {
    return (
      <State title="The summary could not be written" tone="failed" onRetry={onRegenerate}>
        {error.message}
      </State>
    )
  }
  if (report === undefined) {
    return (
      <State title="Reading the record" busy>
        Starting.
      </State>
    )
  }
  if (report.status === 'failed') {
    return (
      <State title="The summary could not be written" tone="failed" onRetry={onRegenerate}>
        {report.error ?? 'Something went wrong while writing it.'}
      </State>
    )
  }
  return <Panel report={report} timeZone={timeZone} onRegenerate={onRegenerate} />
}

function Panel({
  report,
  timeZone,
  onRegenerate,
}: {
  report: Report
  timeZone: string
  onRegenerate: () => void
}) {
  const cited = new Set(
    report.sections.flatMap((section) => section.citations.map((citation) => citation.event_id)),
  )

  return (
    <article className={styles.panel}>
      <div className={styles.head}>
        <h3 className={styles.title}>{report.title}</h3>
        <p className={styles.meta}>
          {report.generated_at
            ? `Generated ${formatDate(report.generated_at, timeZone)}`
            : 'Just written'}
          {' · '}
          {/* Counted, never hardcoded: it says how much of the record this page traces to, and
              a number typed by hand stops being true the next time a section is dropped. */}
          {cited.size === 1 ? 'traces to 1 entry' : `traces to ${cited.size} entries`}
        </p>
        <span className={styles.actions}>
          <CopyButton report={report} timeZone={timeZone} />
          {/*
            Offered rather than automatic. A summary written this morning is not stale by
            lunchtime, and a panel that regenerated on open would spend a family's daily
            allowance every time somebody looked at it. The server refuses the second one a day
            with a sentence, which is exactly what the error state above renders.
          */}
          <button type="button" className={styles.trigger} onClick={onRegenerate}>
            Write a new one
          </button>
        </span>
      </div>

      {report.urgent_flag && report.urgent_reason ? (
        <p className={styles.urgent} role="alert">
          <strong>Urgent — </strong>
          {report.urgent_reason}
        </p>
      ) : null}

      {report.summary ? <p className={styles.lead}>{report.summary}</p> : null}

      <div className={styles.sections}>
        {report.sections.map((section) => (
          <Section key={section.heading} section={section} timeZone={timeZone} />
        ))}
      </div>

      <div className={styles.lists}>
        <List title="Questions for the doctor" items={report.questions_for_the_doctor} />
        <List title="Worth watching" items={report.watch_items} />
        <List title="Not in the record" items={report.data_gaps} muted />
      </div>

      {/*
        Said once, at the bottom, and never omitted. This is the sentence that tells a doctor
        what they are holding: a family's own account, written up by a machine, not a clinical
        note. Everything above it is only safe to read that way.
      */}
      <p className={styles.footnote}>
        <span className={styles.footnoteText}>
          Written by Penny from the family&rsquo;s own WhatsApp messages. Every line above traces
          to an entry in the record below it. Penny reports what was said and when — it does not
          diagnose, it does not advise, and it does not decide what is connected to what.
        </span>
      </p>
    </article>
  )
}

function Section({ section, timeZone }: { section: ReportSection; timeZone: string }) {
  return (
    <section>
      <h4 className={styles.heading}>{section.heading}</h4>
      {/*
        Rendered as plain text, and the field name is the only thing markdown about it: the
        prompt forbids headings, lists and bold, so the body is prose carrying `[E1]` footnote
        markers. Running it through a markdown renderer would buy nothing and would put an
        HTML-injection surface between a model and a family's screen.
      */}
      <p className={styles.body}>{section.body_markdown}</p>
      {section.citations.length > 0 ? (
        <ul className={styles.cites}>
          {section.citations.map((citation) => (
            <li key={citation.handle} className={styles.cite}>
              {/*
                Labelled with the handle as well as the date, because the prose above carries
                `[E1]` markers and four chips with three markers is otherwise a puzzle. Not a
                link: there is no per-entry anchor in the feed yet, and a button that does
                nothing when pressed is a worse promise than a label that can be read.
              */}
              <span className={styles.citeHandle}>{citation.handle}</span>{' '}
              {formatDate(citation.occurred_at, timeZone)} · {citation.title}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

function List({ title, items, muted }: { title: string; items: string[]; muted?: boolean }) {
  if (items.length === 0) return null
  return (
    <div className={cx(muted && styles.listMuted)}>
      <h4 className={styles.heading}>{title}</h4>
      <ul className={styles.list}>
        {items.map((item) => (
          <li key={item} className={styles.listItem}>
            {item}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * Copy the whole thing as plain text.
 *
 * This is the affordance the panel exists for: the family paste it into an email to the surgery,
 * or into a notes app to read off in the room. Plain text rather than rich markup because every
 * destination that matters — an SMS, an email body, a receptionist's form — accepts nothing else.
 */
function CopyButton({ report, timeZone }: { report: Report; timeZone: string }) {
  const [copied, setCopied] = useState(false)

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(asPlainText(report, timeZone))
      setCopied(true)
      setTimeout(() => setCopied(false), 2_000)
    } catch {
      // A clipboard the browser refuses (no permission, insecure origin) is not an error worth
      // a dialog: the text is on screen and can be selected. Say nothing rather than alarm.
    }
  }

  return (
    <button type="button" className={styles.trigger} onClick={() => void onCopy()}>
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}

function asPlainText(report: Report, timeZone: string): string {
  const lines: string[] = [report.title]
  if (report.generated_at) {
    lines.push(`Generated ${formatDate(report.generated_at, timeZone)}`)
  }
  if (report.urgent_flag && report.urgent_reason) lines.push('', `URGENT: ${report.urgent_reason}`)
  if (report.summary) lines.push('', report.summary)

  for (const section of report.sections) {
    lines.push('', section.heading.toUpperCase(), section.body_markdown)
    for (const citation of section.citations) {
      lines.push(`  [${citation.handle}] ${formatDate(citation.occurred_at, timeZone)} — ${citation.title}`)
    }
  }
  appendList(lines, 'QUESTIONS FOR THE DOCTOR', report.questions_for_the_doctor)
  appendList(lines, 'WORTH WATCHING', report.watch_items)
  appendList(lines, 'NOT IN THE RECORD', report.data_gaps)

  lines.push(
    '',
    'Written by Penny from the family’s own WhatsApp messages. Penny reports what was said and',
    'when — it does not diagnose and it does not advise.',
  )
  return lines.join('\n')
}

function appendList(lines: string[], title: string, items: string[]) {
  if (items.length === 0) return
  lines.push('', title)
  for (const item of items) lines.push(`  - ${item}`)
}

interface StateProps {
  title: string
  children: React.ReactNode
  busy?: boolean
  tone?: 'failed'
  onRetry?: () => void
}

function State({ title, children, busy, tone, onRetry }: StateProps) {
  return (
    <div
      className={cx(styles.state, tone === 'failed' && styles.failed)}
      role={tone === 'failed' ? 'alert' : undefined}
      aria-busy={busy || undefined}
    >
      <h3 className={styles.stateTitle}>{title}</h3>
      <p className={styles.stateBody}>{children}</p>
      {busy ? (
        <div className={styles.progress} aria-hidden="true">
          <div className={styles.progressBar} />
        </div>
      ) : null}
      {onRetry ? (
        <button type="button" className={cx(styles.trigger, styles.again)} onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  )
}
