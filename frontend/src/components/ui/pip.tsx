/*
 * Pip — composable, multi-segment data badge. Vendored from sf-core
 * (`apps/platform/components/pip.tsx`); the only edits are the removal of the
 * `"use client"` directive and this header.
 *
 * Outer (`Pip`) is a transparent flex container — no bg, no border. It exists
 * only to layout the Segments: 1px padding all around (p-px) and 1px between
 * adjacent Segments (gap-px). The shape of the component is defined entirely
 * by the Segments themselves and the negative space between them.
 *
 * Inner (`Pip.Segment`) is the visible squircle. It carries its own bg + text
 * color and rounds itself with `rounded-md` — every segment shares this shape,
 * so single- and multi-segment Pips look like one family.
 *
 * Single-segment shorthand: pass `bg`/`color` directly on `Pip` and skip the
 * `Pip.Segment` wrapper. Children are rendered inside an implicit Segment.
 *
 *   <Pip bg="..." color="...">PGY1</Pip>
 *
 *   <Pip>
 *     <Pip.Segment bg="..." color="...">PGY1</Pip.Segment>
 *     <Pip.Segment bg="..." color="...">3</Pip.Segment>
 *   </Pip>
 *
 * Outer wrap color (the 1px negative-space frame and inter-segment dividers)
 * is set via `style` or `className` on `Pip` — separate concern from segment
 * color.
 */

import * as React from "react"
import { cn } from "@/lib/utils"

interface PipProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Shorthand for single-segment Pips: sets segment background. */
  bg?: string
  /** Shorthand for single-segment Pips: sets segment text color. */
  color?: string
}

const PipRoot = React.forwardRef<HTMLSpanElement, PipProps>(
  ({ className, children, bg, color, ...props }, ref) => {
    const shorthand = bg !== undefined || color !== undefined
    return (
      <span
        ref={ref}
        className={cn(
          "inline-flex items-center shrink-0 whitespace-nowrap",
          "text-xs font-medium leading-none",
          "rounded-[9px] p-px gap-px",
          className,
        )}
        {...props}
      >
        {shorthand ? (
          <PipSegment bg={bg} color={color}>{children}</PipSegment>
        ) : (
          children
        )}
      </span>
    )
  },
)
PipRoot.displayName = "Pip"

interface PipSegmentProps extends React.HTMLAttributes<HTMLSpanElement> {
  bg?: string
  color?: string
}

const PipSegment = React.forwardRef<HTMLSpanElement, PipSegmentProps>(
  ({ className, bg, color, style, children, ...props }, ref) => (
    <span
      ref={ref}
      className={cn(
        "inline-flex items-center px-2 py-1",
        // Inner-facing corners use a small radius; outer-facing corners
        // (first segment's left, last segment's right) keep the full squircle
        // so the Pip's overall shape mirrors the outer container.
        "rounded-[3px] first:rounded-l-md last:rounded-r-md",
        className,
      )}
      style={{ backgroundColor: bg, color, ...style }}
      {...props}
    >
      {children}
    </span>
  ),
)
PipSegment.displayName = "Pip.Segment"

export const Pip = Object.assign(PipRoot, { Segment: PipSegment })
