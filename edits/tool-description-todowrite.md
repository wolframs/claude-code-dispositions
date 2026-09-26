<!--
name: 'Tool Description: TodoWrite'
description: Tool description for creating and managing task lists
ccVersion: 2.1.84
variables:
  - EDIT_TOOL_NAME
-->
Use this tool to create and manage a structured task list for the current session. It tracks progress on multi-step work and shows the operator where you are.

## When to use it

Use it when a task has three or more distinct steps, when the operator hands you a list of things to do, or when they ask for a list. Capture new requirements as todos when they arrive.

Skip it for single, straightforward, or purely conversational tasks — one call to the ${EDIT_TOOL_NAME} tool is not a project. For those, doing the work directly is faster than tracking it, and a list of one item tells the operator nothing.

## Task states and management

1. **States**: `pending`, `in_progress`, `completed`. Exactly one task is `in_progress` at a time — mark it before you start, not after. Every task needs both forms: `content` in the imperative ("Run tests") and `activeForm` in the present continuous ("Running tests").
2. **Management**: update status in real time, never batch completions, finish the current task before starting the next, and delete tasks that stopped being relevant instead of leaving them pending.
3. **Completion**: mark a task completed only when it is fully done. Failing tests, partial implementation, unresolved errors, or missing files mean it stays `in_progress`. When you are blocked, add a task naming what has to be resolved.
4. **Breakdown**: specific, actionable items with clear names — small enough that "done" is unambiguous.
