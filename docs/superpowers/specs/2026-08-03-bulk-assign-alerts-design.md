# Bulk-assign alerts to a user

## Problem

Alerts can be multi-selected via checkboxes for bulk snooze, but there's no way to bulk-assign
selected alerts to a team member. A batch-assign UI already exists in the markup
(`#batchAssignInput`, `#batchAssignBtn`) but is dead code — forcibly hidden and wired to a
payload shape (`assigned_to` as a plain string) that predates the current array-based
`assigned_to` contract.

## Scope

Target: `__staging_multiassignee2` only (the multi-assignee trial). Production
(`frontend/`) is untouched — assignment stays feature-flagged off there.

## Behavior

- Selecting one or more alerts via the existing checkboxes (shared `selectedIds` Set, same
  mechanism used by Snooze) enables a batch-assign dropdown + button in the batch bar.
- The dropdown lists team members (from the existing `/team-members` endpoint /
  `teamMembers` array), single-select — one person per bulk action. Running the action again
  adds a second person.
- Clicking "Assign selected" **adds** the chosen person to each selected alert's `assigned_to`
  array, without removing whoever is already assigned to that alert. Duplicates are not added
  (if the alert already has that person, it's a no-op for that alert).
- `assigned_at` is set to the current timestamp on every alert touched by the action.
- Partial failure is tolerated: alerts that fail to update don't block the others. The user
  sees a summary count ("Assigned 5 of 6 — 1 failed, check console").

## Frontend changes (`__staging_multiassignee2/index.html`)

1. **Un-hide the batch assign controls.** Remove the unconditional
   `document.getElementById("assignControls").style.display = "none";` (line 471). Add a CSS
   rule `.assignment-off .assignField{ display:none; }` alongside the existing
   `.assignment-off .assignee-field{ display:none; }` (line 119), so visibility is driven by
   the `ASSIGNMENT_ENABLED` flag like the rest of the assignment UI. Since this file has
   `ASSIGNMENT_ENABLED = true`, the controls become visible.

2. **Populate the dropdown.** Add a function that fills `#batchAssignInput` with one
   `<option>` per entry in `teamMembers`, plus a disabled placeholder option ("Select a
   teammate…") selected by default. Call it once after `loadTeamMembers()` resolves in
   `init()`.

3. **Rewrite the `batchAssignBtn` click handler** (currently lines 835-864). For each id in
   `selectedIds`:
   - Look up the alert in `currentAlerts` to get its current `assigned_to` array.
   - Build the new array: existing assignees plus the chosen email, deduplicated
     (`Array.from(new Set([...(alert.assigned_to || []), assignee]))`).
   - `PATCH /alerts/{id}` with `{ assigned_to: newArray, assigned_at: now }`.
   - Use `Promise.allSettled` (not `Promise.all`) so one rejection doesn't prevent the rest
     from applying. After settling, reset the selection/dropdown and reload alerts, then show
     a summary popup reflecting how many succeeded vs. failed.

4. **Fix the modal assignee-picker bug** (line ~1026). The panel's checkbox `change` handler
   currently does `selectedAssignees = new Set([m])` when checking a new box — replacing the
   whole selection instead of adding to it. Change it to add into the existing set
   (`selectedAssignees.add(m)`), matching the chip-remove handler's already-correct
   `selectedAssignees.delete(...)`. Without this fix, opening the modal on an alert that
   bulk-assign just gave two assignees and toggling any checkbox would silently drop one.

## Backend changes (`__staging_multiassignee2/function_app.py`)

- Relax the `PATCH /alerts/{id}` validation at line 331. Currently:
  `if not isinstance(value, list) or not all(isinstance(v, str) for v in value) or len(value) > 1:`
  rejects any array longer than 1 with a 400. Remove the `len(value) > 1` clause so any-length
  string arrays are accepted (still validating it's a list of strings). Update the error
  message accordingly (no longer "at most one email string").
- No database migration needed — `alerts-db-staging.dbo.Alerts.assigned_to` is already
  widened to `NVARCHAR(1000)` (`sql/06_widen_assigned_to.sql`, already applied to staging).

## Explicitly out of scope

- Production (`frontend/`) — assignment remains feature-flagged off there.
- Bulk-unassign / bulk-remove-assignee action.
- Any cap on the number of assignees per alert.
- Multi-select (more than one person at a time) in the batch dropdown itself.

## Verification (manual — no test suite in this project)

1. Log into the staging app. Multi-select several alerts via checkboxes (mix of alerts with
   zero and with one existing assignee).
2. Pick a teammate from the batch dropdown, click "Assign selected".
3. Confirm: alerts that had no assignee now have exactly the chosen person; alerts that
   already had someone now have both people; the summary/count is correct.
4. Open the modal for a two-assignee alert and toggle a different teammate's checkbox on.
   Confirm both prior assignees remain plus the new one (not collapsed to one).
5. Re-run the bulk action with the same person on an already-assigned alert — confirm no
   duplicate entries are added.
