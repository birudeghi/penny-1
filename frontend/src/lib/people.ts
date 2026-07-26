/**
 * Who said what, and what colour they are.
 *
 * The design prototype hard-coded a five-person family with a colour each. Real households are an
 * open set — members arrive whenever a new number posts in the WhatsApp group, and they get
 * merged and renamed afterwards — so the colour has to be *derived* from the person rather than
 * looked up beside them.
 *
 * DERIVED FROM `member_id`, NOT FROM POSITION IN THE LIST. Assigning palette slots by index would
 * mean a member joining, or a merge collapsing two rows into one, silently repaints every person
 * below them: Liz is green today and orange tomorrow, and a reader who had learned to skim by
 * colour is quietly misled. Ids never change, so hashing one is stable across sessions, across
 * devices, and across every family member's screen at once.
 *
 * THE PENNY TOKEN IS RESERVED. `--penny` is the product's own voice — it marks the assistant, the
 * medication node and every link in the record. It is kept out of the rotation so no family
 * member can be handed it by the hash, and it is given to Penny herself when she appears as a
 * member of the group, which she does: the bridge posts as a participant like anybody else.
 */

import { createContext, use } from 'react'

import type { Member } from '../types/api'

/** The three Tailwind classes a person is drawn with. Static strings — Tailwind cannot see a
 *  class name that was assembled at runtime, so `bg-${hue}` would compile to nothing at all. */
export interface PersonTone {
  /** Their name, set in their own hue. */
  text: string
  /** A filled disc: avatars. */
  bg: string
  /** The tint behind their message bubbles. */
  wash: string
}

/**
 * Four hues, deliberately the same four the entry kinds use.
 *
 * Re-using them rather than inventing a second palette is what keeps the page to one family of
 * colours; nothing is ambiguous in practice because a person's hue only ever appears on a round
 * avatar or a message bubble, and a kind's only ever on the square-shouldered spine node.
 * `--admin` grey is excluded: on a person it reads as "unidentified" rather than as an identity.
 */
const PALETTE: PersonTone[] = [
  { text: 'text-appointment', bg: 'bg-appointment', wash: 'bg-appointment-wash' },
  { text: 'text-wellbeing', bg: 'bg-wellbeing', wash: 'bg-wellbeing-wash' },
  { text: 'text-owen', bg: 'bg-owen', wash: 'bg-owen-wash' },
  { text: 'text-symptom', bg: 'bg-symptom', wash: 'bg-symptom-wash' },
]

const PENNY_TONE: PersonTone = {
  text: 'text-penny',
  bg: 'bg-penny',
  wash: 'bg-penny-wash',
}

export interface Person {
  id: string
  name: string
  /** One letter. Two initials need a surname, and WhatsApp names are usually "Mum". */
  initials: string
  tone: PersonTone
}

export interface People {
  /** The member behind an id — from an event's `actor`. */
  byId(id: string): Person
  /**
   * The member behind the display name on a source excerpt.
   *
   * Excerpts carry `sender` as a name, not an id, so this resolves through the member list to get
   * back to the id the colour is keyed on — otherwise the same person would be one colour in the
   * evidence and another in the column beside it. An unresolved name (a member merged away since
   * the excerpt was written) falls back to hashing the name, which is stable too, just not shared.
   */
  bySender(sender: string): Person
  /** Everyone in the household, in the order the API returned them. */
  all: Person[]
}

/** FNV-1a, 32-bit. Any stable hash would do; this one is four lines and has no dependencies. */
function hash(value: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

function normalise(name: string): string {
  return name.trim().toLowerCase()
}

function isPenny(name: string): boolean {
  return normalise(name) === 'penny'
}

function initialsOf(name: string): string {
  return (name.trim().charAt(0) || '?').toUpperCase()
}

function toneFor(key: string, name: string): PersonTone {
  if (isPenny(name)) return PENNY_TONE
  // `noUncheckedIndexedAccess` is on and the modulo cannot go out of range, so the fallback is
  // unreachable — it is here to satisfy the compiler, not to handle a case.
  return PALETTE[hash(key) % PALETTE.length] ?? PENNY_TONE
}

function toPerson(id: string, name: string): Person {
  return { id, name, initials: initialsOf(name), tone: toneFor(id, name) }
}

export function createPeople(members: Member[]): People {
  const byId = new Map(members.map((member) => [member.id, member]))
  const byName = new Map(members.map((member) => [normalise(member.display_name), member]))

  return {
    byId(id) {
      const member = byId.get(id)
      // A name we cannot resolve is rendered as the id rather than as "Unknown": an id on screen
      // is something a developer can search for, and a blank is something nobody can.
      return toPerson(id, member?.display_name ?? id)
    },
    bySender(sender) {
      const member = byName.get(normalise(sender))
      return member ? toPerson(member.id, member.display_name) : toPerson(sender, sender)
    },
    all: members.map((member) => toPerson(member.id, member.display_name)),
  }
}

/**
 * The household with nobody in it yet.
 *
 * `GET /api/members` is a second request that lands after the feed, so the timeline renders once
 * with this. Names still show — they come from the events themselves — they are just briefly
 * coloured by their own text instead of by their member id.
 */
export const NO_PEOPLE: People = createPeople([])

const PeopleContext = createContext<People>(NO_PEOPLE)

export const PeopleProvider = PeopleContext.Provider

/** Read the household's people. Context rather than props: the deepest consumer is a chat bubble
 *  four levels down, and threading a member list through the timeline would put a parameter on
 *  every component in between that none of them has any use for. */
export function usePeople(): People {
  return use(PeopleContext)
}
