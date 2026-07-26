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
 *
 * ONE ADDITION: the `React` import. Upstream reaches for the `React.ComponentProps` namespace
 * without importing it, which only compiles under Next's ambient types; here it is a module and
 * `tsc -b` rejects the UMD global.
 */
import * as React from "react"
import { Collapsible as CollapsiblePrimitive } from "radix-ui"

function Collapsible({
  ...props
}: React.ComponentProps<typeof CollapsiblePrimitive.Root>) {
  return <CollapsiblePrimitive.Root data-slot="collapsible" {...props} />
}

function CollapsibleTrigger({
  ...props
}: React.ComponentProps<typeof CollapsiblePrimitive.CollapsibleTrigger>) {
  return (
    <CollapsiblePrimitive.CollapsibleTrigger
      data-slot="collapsible-trigger"
      {...props}
    />
  )
}

function CollapsibleContent({
  ...props
}: React.ComponentProps<typeof CollapsiblePrimitive.CollapsibleContent>) {
  return (
    <CollapsiblePrimitive.CollapsibleContent
      data-slot="collapsible-content"
      {...props}
    />
  )
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent }
