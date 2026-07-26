import { useRef, useState } from 'react'
import type { CSSProperties, MouseEvent } from 'react'

import styles from './landing.module.css'

/**
 * The marketing front door: one number, one button, nothing to install. Signed-out visitors to
 * `/` land here; the whole page exists to get Penny's number into the family's hands, so the
 * number scene and the CTA both copy it to the clipboard — adding her to a group chat happens
 * in WhatsApp itself, and a copied number pastes straight into the contact picker.
 *
 * The number is build-time config because the paired GOWA account can differ per environment;
 * the fallback is Penny's real number.
 */
const DIGITS = (import.meta.env.VITE_PENNY_WHATSAPP_NUMBER ?? '17473209317').replace(/\D/g, '')

/* E.164 — pastes cleanly into WhatsApp, contacts, and dialers alike. */
const COPY_TEXT = `+${DIGITS}`

function formatNumber(digits: string): string {
  if (digits.length === 11 && digits.startsWith('1')) {
    return `+1 (${digits.slice(1, 4)}) ${digits.slice(4, 7)}-${digits.slice(7)}`
  }
  return `+${digits}`
}

const DISPLAY = formatNumber(DIGITS)

const TILE_COLORS = [
  styles.coral,
  styles.marigold,
  styles.sky,
  styles.leaf,
  styles.lilac,
  styles.rose,
]

const TILE_TILTS = [-5, 4, -3, 6, -4, 3, -6, 5, -2, 4, -5]

/**
 * The display string, pre-split so the number wraps at readable boundaries — spaces and after
 * the dash — instead of mid-group: "+1 (415)" on one line, "555- 0199" flowing beneath. Digits
 * carry a running index for colour and tilt; everything else renders as a plain mark.
 */
const GROUPS: { char: string; digit: number | null }[][] = (() => {
  let digit = -1
  return DISPLAY.split(' ')
    .flatMap((group) => group.replace(/-/g, '- ').split(' '))
    .filter((group) => group !== '')
    .map((group) =>
      Array.from(group).map((char) => ({
        char,
        digit: /\d/.test(char) ? ++digit : null,
      })),
    )
})()

export function LandingRoute() {
  const [copied, setCopied] = useState(false)
  const [toast, setToast] = useState<{ x: number; y: number; key: number } | null>(null)
  const copiedTimer = useRef<number>(undefined)

  function copyNumber(click: MouseEvent<HTMLButtonElement>) {
    // Keyboard activation reports (0, 0); anchor the toast to the button instead.
    const rect = click.currentTarget.getBoundingClientRect()
    const x = click.clientX || rect.left + rect.width / 2
    const y = click.clientY || rect.top
    void navigator.clipboard.writeText(COPY_TEXT).then(() => {
      setCopied(true)
      setToast({ x, y, key: Date.now() })
      window.clearTimeout(copiedTimer.current)
      copiedTimer.current = window.setTimeout(() => {
        setCopied(false)
        setToast(null)
      }, 1800)
    })
  }

  return (
    <div className={styles.page}>
      <header className={styles.topBar}>
        <span className={styles.wordmark}>
          <img className={styles.coin} src="/favicon.svg" alt="" aria-hidden />
          Penny
        </span>
        <a href="https://app.pennyai.chat" className={styles.signIn}>
          Sign in
        </a>
      </header>

      <main className={styles.hero}>
        <button
          type="button"
          className={styles.scene}
          onClick={copyNumber}
          aria-label={`Copy Penny's WhatsApp number ${DISPLAY} to the clipboard`}
        >
          <span className={styles.number} aria-hidden>
            {GROUPS.map((group, g) => (
              <span className={styles.group} key={g}>
                {group.map(({ char, digit }, c) =>
                  digit === null ? (
                    <span key={c} className={styles.mark}>
                      {char}
                    </span>
                  ) : (
                    <span
                      key={c}
                      className={`${styles.tile} ${TILE_COLORS[digit % TILE_COLORS.length]}`}
                      style={
                        {
                          '--tilt': `${TILE_TILTS[digit % TILE_TILTS.length]}deg`,
                          '--delay': `${(digit % 5) * 0.4}s`,
                        } as CSSProperties
                      }
                    >
                      {char}
                    </span>
                  ),
                )}
              </span>
            ))}
          </span>
          <span className={styles.bubble} aria-hidden>
            add me to your group chat
            <span className={styles.bubbleMeta}>
              9:41
              <svg viewBox="0 0 18 12" width="1.35em" height="0.9em" aria-hidden>
                <path
                  d="M1 6.5 4.5 10 11 2.5M7 6.5 10.5 10 17 2.5"
                  fill="none"
                  stroke="#53bdeb"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
          </span>
        </button>

        <div className={styles.copy}>
          <h1 className={styles.headline}>
            care assistant
            <br />
            in your chats
          </h1>
          <button
            type="button"
            className={styles.cta}
            onClick={copyNumber}
            aria-label={`Copy Penny's WhatsApp number ${DISPLAY} to the clipboard`}
          >
            <svg className={styles.ctaLogo} viewBox="0 0 24 24" aria-hidden fill="currentColor">
                <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 0 1-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 0 1-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 0 1 2.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0 0 12.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 0 0 5.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 0 0-3.48-8.413Z" />
            </svg>
            {copied ? 'number copied!' : 'let Penny in'}
          </button>
        </div>
      </main>

      <footer className={styles.footer}>designed with love in London</footer>

      {toast ? (
        <span
          key={toast.key}
          className={styles.cursorToast}
          style={{ left: toast.x, top: toast.y }}
          aria-hidden
        >
          copied to clipboard
        </span>
      ) : null}
    </div>
  )
}
