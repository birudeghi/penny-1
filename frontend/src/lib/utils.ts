import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Joins class names and resolves Tailwind conflicts, last one winning.
 *
 * The conflict resolution is the whole point and is why this is not `cx()` from
 * `components/cx.ts`: every ported component takes a `className` from its caller and merges it
 * over its own defaults, so `<PersonAvatar className="size-5" />` has to beat the `size-7` baked
 * into the component. Plain concatenation would leave both classes in the list and let source
 * order in the generated style sheet decide — which is to say, at random.
 *
 * `cx()` stays for the CSS-Module screens, where there are no conflicting utilities to resolve.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
