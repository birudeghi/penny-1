/*
 * Vendored shadcn/ui component ("radix-luma" style), copied verbatim from the design prototype.
 *
 * Copied rather than generated: `npx shadcn init` targets a Next.js/PostCSS project and rewrites
 * the design tokens it finds, which for this repo means overwriting the ported oklch system in
 * `src/design.css` with the default one. The only edit made to these files is the removal of the
 * `"use client"` directive, which has no meaning outside the App Router.
 *
 * Treat as third-party: keep it diffable against upstream and put project-specific decisions in
 * the components that wrap it, never in here.
 */
import * as React from "react"
import { Separator as SeparatorPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

function Separator({
  className,
  orientation = "horizontal",
  decorative = true,
  ...props
}: React.ComponentProps<typeof SeparatorPrimitive.Root>) {
  return (
    <SeparatorPrimitive.Root
      data-slot="separator"
      decorative={decorative}
      orientation={orientation}
      className={cn(
        "shrink-0 bg-border data-horizontal:h-px data-horizontal:w-full data-vertical:w-px data-vertical:self-stretch",
        className
      )}
      {...props}
    />
  )
}

export { Separator }
