# Startup contract

This file holds only what an agent must know before reading anything else. Current rules live in
.ai-workflow/WORKFLOW.md; navigation lives in .ai-workflow/README.md.
Every deployed copy of this file (CLAUDE.md, AGENTS.md, ...) must be byte-identical to
.ai-workflow/startup-contract/STARTUP-CONTRACT.md. The project's paths and list of copies are in the
project configuration that STATE points to; do not treat one project's layout as the default for all.

1. Do only what the user explicitly asks for in the current request. Old files, status roadmaps and
   green tests never create new authorization. When rules conflict, WORKFLOW.md wins.
2. Find .ai-workflow/ by walking up from the current directory; do not guess the relative depth.
   The authoritative repository and product paths are given by the project configuration.
3. Read .ai-workflow/STATE.md first, then .ai-workflow/README.md, then check in the project
   configuration that STATE points to whether the progress board is enabled. If it is, multi-stage or
   handoff tasks must read .ai-workflow/runbooks/AGENT-BOARD.md and join the board as it describes.
   Look up only this task and its direct dependencies; read other rules and runbooks as the task needs.
   Small tasks do not require a board record. Applicable tasks also read the WORKFLOW section
   "Optional local progress board": report briefly when a stage completes before replying; before a
   long-running program starts, save the stage, the next observable result and the resume entry point;
   a running service or a page refresh is not progress; do not flush other tasks' old queues as a test.
4. Separately check whether the user explicitly enabled the task board for the current task, or whether
   you are resuming the same, clearly bound task for which it was enabled before. Only then read the
   WORKFLOW section "On-demand task board", .ai-workflow/runbooks/TASK-BOARD.md and this task's compact
   server summary and related branches (page through and read history only as needed), then plan or
   continue as the user asked. Otherwise skip it: do not create empty records or scan task boards.
   Neither task complexity nor an enabled project progress board means the task board is enabled.
   Resuming the same task does not create a second board and does not authorize new tasks.
5. Before writing DECISIONS.md, STATE.md or OPEN.md, read the WORKFLOW section "Single home for each
   kind of content" in the same turn. Product design or progress does not go into the workflow decision
   log just because it is important, cross-module or approved.
6. Local commits are allowed by default. Push, deployment, database migration, production configuration,
   real data, paid calls and deleting user files each need explicit authorization. Stage handoffs and
   final receipts report the checkpoint and push state of every affected repository, without sweeping in
   other writers' changes.

Do not start work on your own. Do not read the full DECISIONS history by default.
