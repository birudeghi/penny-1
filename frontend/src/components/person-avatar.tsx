import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'
import type { Person } from '@/lib/people'

const sizes = {
  xs: 'size-5 text-[9px]',
  sm: 'size-7 text-[11px]',
  md: 'size-9 text-xs',
  lg: 'size-11 text-sm',
} as const

/**
 * A person, as a filled disc with their initial in it.
 *
 * Takes a resolved `Person` rather than an id: the colour is derived from the member id by
 * `lib/people`, and letting every call site do that lookup itself is how one component ends up
 * hashing the display name while the one beside it hashes the id, and the same person comes out
 * two different colours on the same screen.
 */
export function PersonAvatar({
  person,
  size = 'sm',
  className,
}: {
  person: Person
  size?: keyof typeof sizes
  className?: string
}) {
  return (
    <Avatar className={cn(sizes[size], className)}>
      <AvatarFallback
        className={cn(person.tone.bg, 'font-semibold text-white dark:text-background')}
      >
        {person.initials}
      </AvatarFallback>
    </Avatar>
  )
}
