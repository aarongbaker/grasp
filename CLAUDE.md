# grasp — UI Design Guidelines for Claude

**grasp** is a web-based dinner party planning tool for private chefs and home cooks. It wraps LLM-driven menu generation, RAG-backed recipe retrieval, DAG-based schedule optimization, and a food costing engine into a single interface. The UI must reflect the craft and intentionality of the people using it.

---

## Aesthetic Direction

**Tone**: Warm editorial — like a high-end cookbook crossed with a chef's working notebook. Think: tactile paper textures, ink-stained margins, the kind of interface that feels like it was designed *in* a kitchen, not a design agency.

**What this is NOT**: A food delivery app. A recipe blog. A SaaS dashboard. A purple-gradient AI tool.

**What this IS**: A professional instrument. Calm but opinionated. Built for flow state — the kind a chef is in during prep.

---

## Typography

- **Display / Headings**: A serif with character — Playfair Display, Cormorant Garamond, or Freight Display. Something that would look at home on a menu card.
- **Body / UI text**: A humanist sans — DM Sans, Lato, or Source Sans 3. Legible at small sizes under kitchen-bright screens.
- **Monospace / Data** (costs, times, quantities): JetBrains Mono or iA Writer Mono. Make numbers feel precise and trustworthy.
- **NEVER use**: Inter, Roboto, Arial, or any system font stack as a design choice.

---

## Color & Theme

Use CSS variables throughout. Default to **dark warm theme** (this is a tool used in low-light prep kitchens).

```css
:root {
  --bg-base:        #1a1612;   /* deep espresso */
  --bg-surface:     #231f1a;   /* warm charcoal */
  --bg-raised:      #2e2822;   /* lifted surface */
  --border:         #3d3530;   /* subtle warm divide */

  --text-primary:   #f0e8dc;   /* warm off-white */
  --text-secondary: #9e8f80;   /* muted warm gray */
  --text-muted:     #5c5248;   /* ghost text */

  --accent-primary: #c9813a;   /* burnished copper — the hero colour */
  --accent-warm:    #d4956a;   /* softer terracotta for hover states */
  --accent-cool:    #6a8fa3;   /* slate blue — used sparingly for info states */

  --cost-positive:  #7aad7a;   /* under budget */
  --cost-warning:   #d4a24e;   /* approaching budget */
  --cost-negative:  #c46b6b;   /* over budget */
}
```

**Palette rules**:
- Copper (`--accent-primary`) is reserved for primary CTAs and active states only. Don't dilute it.
- Never use pure white (`#ffffff`) — always warm off-white.
- Background layers should feel like stacked paper, not flat planes.

---

## Spatial Composition

- **Grid**: 12-column CSS grid with generous gutters (24–32px). Let content breathe.
- **Hierarchy through size and weight**, not color. Important things are bigger. Not louder.
- **Asymmetry is fine** — a timeline running left while recipe cards stack right feels natural to the planning metaphor.
- **Data density**: grasp surfaces a lot of information (ingredients, timings, costs). Prefer **progressive disclosure** — surface summaries, expand on demand. Never dump everything at once.
- **Negative space** is not wasted space. A recipe card with room around it communicates calm.

---

## Motion

- Prefer **CSS transitions** for simple state changes (hover, focus, open/close).
- Use **staggered entrance animations** when surfacing a generated menu — items should appear sequentially, like pages turning.
- Schedule/timeline view: use **smooth horizontal scroll** with subtle parallax between time labels and blocks.
- **Always** respect `prefers-reduced-motion`:
  ```css
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      transition-duration: 0.01ms !important;
    }
  }
  ```

---

## Core UI Components

### Menu Card
Displays a generated dish. Should feel like a recipe index card — slightly textured background, dish name in display serif, key metadata (course, dietary flags, cost) in a clean row beneath.

### Schedule Block (DAG view)
Timeline blocks representing recipe prep steps. Color-coded by recipe, not by status. Overlap zones (parallelizable tasks) should be visually distinct but calm — not alarming.

### Cost Engine Panel
Numbers are the hero here. Large, monospace, clearly labeled. Use `--cost-positive/warning/negative` sparingly and only for budget comparison rows. Never use red/green for decorative purposes.

### RAG Source Citation
When a recipe is retrieved from the vector store, show a subtle provenance indicator — a small "from library" tag in muted text. Users should know when Claude is generating vs. retrieving.

---

## Interaction Patterns

- **Hover states**: Copper border-left accent (`border-left: 2px solid var(--accent-primary)`) on cards. Subtle, not shouty.
- **Active/Selected**: Background lifts to `--bg-raised` with a copper top-border.
- **Loading states**: Skeleton loaders that match the component shape. Never spinners in the centre of the screen.
- **Error states**: Calm, inline, in `--cost-negative`. No modal interruptions for recoverable errors.
- **Empty states**: Illustrated — a hand-drawn-style empty plate or a blank menu card outline. Not "No data found."

---

## Accessibility

- Minimum contrast ratio: **4.5:1** for all body text against backgrounds.
- All interactive elements must have visible `:focus-visible` states (copper ring, `outline: 2px solid var(--accent-primary); outline-offset: 2px`).
- Icon buttons must have `aria-label`. Never rely on icon alone to communicate meaning.
- Keyboard navigation must be fully functional in the schedule/timeline view.
- Cost figures must be accompanied by text labels — never color alone for budget status.

---

## Tech Stack Assumptions

- **Frontend**: React (component-based, hooks)
- **Styling**: CSS Modules or Tailwind (use CSS variables regardless)
- **Data**: PostgreSQL + pgvector backend; surfaces via REST or tRPC
- **LLM outputs**: Streamed — UI must handle partial/streaming states gracefully

When Claude generates a component, it should:
1. Define the CSS variables it relies on at the top (or reference this file's palette)
2. Include hover, focus, and empty states
3. Handle loading and error conditions
4. Be production-ready — no placeholder `TODO` styling

---

## What to Avoid

| ❌ Don't | ✅ Do instead |
|---|---|
| Purple gradients | Warm dark backgrounds with copper accents |
| Inter / Roboto | Cormorant Garamond + DM Sans |
| Flat white cards | Layered warm surfaces with subtle borders |
| Bright status colors everywhere | Restrained use of cost status colors only |
| Spinner loaders | Shape-matched skeleton loaders |
| Dense data tables | Progressive disclosure with expandable rows |
| Modal-heavy flows | Inline editing, slide-over panels |

---

## Invocation

When building UI for grasp, Claude should read this file first and commit to the aesthetic direction before writing any code. Reference the palette, typography, and component guidelines above. Every component should feel like it belongs in the same kitchen.
