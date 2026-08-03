# Bulk-Assign Alerts to a User — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user multi-select alerts via the existing checkboxes and bulk-assign them to a team member, adding that person to each alert's `assigned_to` array without removing whoever is already assigned.

**Architecture:** Two files change, both in `__staging_multiassignee2/`: `function_app.py` (relax a backend validation rule) and `index.html` (wire up dead batch-assign markup that already exists but is hidden and broken). No new files, no DB migration, no new dependencies.

**Tech Stack:** Vanilla JS/HTML/CSS single-page app (`index.html`), Python Azure Functions backend (`function_app.py`) with `pyodbc` against Azure SQL.

## Global Constraints

- Scope is `__staging_multiassignee2/` only. Do not touch `frontend/` (production) — assignment stays feature-flagged off there.
- Bulk-assign **adds** the chosen person to each selected alert's `assigned_to` array; it never removes an existing assignee. Duplicates are not added.
- `assigned_at` is set to the current timestamp on every alert touched by a bulk-assign action.
- The batch dropdown is single-select: one person assigned per click of "Assign selected."
- Partial failure must not block the rest: use `Promise.allSettled`, then show a success/fail summary count.
- No DB migration needed — `alerts-db-staging.dbo.Alerts.assigned_to` is already `NVARCHAR(1000)`.
- Out of scope: bulk-unassign, capping the number of assignees, multi-select in the batch dropdown itself.
- This project has no automated test suite. Each task below verifies with the strongest check actually available (a runnable Python/Node syntax or logic check) and defers full functional confirmation to the final live-verification task, matching the project's existing manual-testing convention (this whole checkout is itself a manual trial environment).

---

### Task 1: Backend — allow more than one assignee in `PATCH /alerts/{id}`

**Files:**
- Modify: `__staging_multiassignee2/function_app.py:328-337`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `PATCH /alerts/{id}` now accepts `assigned_to` as a JSON array of any length (previously capped at length 1). Task 3's frontend code will send arrays of length 2+.

- [ ] **Step 1: Read the current validation block**

Confirm the file still reads exactly this at line 328-337 (if it doesn't, stop and re-read the surrounding function before editing):

```python
    update_fields = {k: v for k, v in body.items() if k in ALLOWED_PATCH_FIELDS}
    if "assigned_to" in update_fields:
        value = update_fields["assigned_to"] or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value) or len(value) > 1:
            return func.HttpResponse(
                json.dumps({"error": "assigned_to must be an array of at most one email string"}),
                status_code=400,
                mimetype="application/json"
            )
        update_fields["assigned_to"] = json.dumps(value)
```

- [ ] **Step 2: Remove the one-assignee cap**

Replace it with:

```python
    update_fields = {k: v for k, v in body.items() if k in ALLOWED_PATCH_FIELDS}
    if "assigned_to" in update_fields:
        value = update_fields["assigned_to"] or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return func.HttpResponse(
                json.dumps({"error": "assigned_to must be an array of email strings"}),
                status_code=400,
                mimetype="application/json"
            )
        update_fields["assigned_to"] = json.dumps(value)
```

- [ ] **Step 3: Verify the new validation logic**

The check can't be exercised end-to-end without a live DB connection, but the boolean logic itself can be verified directly. Run:

```bash
python -c "
def validate(value):
    return isinstance(value, list) and all(isinstance(v, str) for v in value)

print(validate([]))                                     # expect True
print(validate(['a@muc-corp.com']))                      # expect True
print(validate(['a@muc-corp.com', 'b@muc-corp.com']))    # expect True (was False before this fix)
print(validate('not-a-list'))                            # expect False
print(validate([1, 2]))                                  # expect False
"
```

Expected output: `True`, `True`, `True`, `False`, `False`.

- [ ] **Step 4: Confirm the file still compiles**

```bash
python -m py_compile "__staging_multiassignee2/function_app.py"
```

Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
cd "__staging_multiassignee2"
git add function_app.py
git commit -m "Allow multiple assignees in PATCH /alerts/{id} validation"
```

---

### Task 2: Frontend — show and populate the batch-assign dropdown

**Files:**
- Modify: `__staging_multiassignee2/index.html` (CSS block near line 118, flag block near line 466, `loadTeamMembers`/init block near lines 742 and 1164)

**Interfaces:**
- Consumes: existing global `teamMembers` array (populated by existing `loadTeamMembers()`), existing DOM elements `#assignControls`, `#batchAssignInput`.
- Produces: `populateBatchAssignSelect()` — a new global function, called once during `init()`, that fills `#batchAssignInput` with `<option>`s. Task 3 relies on `#batchAssignInput` already being populated when the user clicks "Assign selected."

- [ ] **Step 1: Tie the batch-assign controls' visibility to the existing `assignment-off` class instead of hiding them unconditionally**

Find this CSS (around line 118-119):

```css
  .assignment-off .col-assigned{ display:none; }
  .assignment-off .assignee-field{ display:none; }
```

Add a third rule right after it:

```css
  .assignment-off .col-assigned{ display:none; }
  .assignment-off .assignee-field{ display:none; }
  .assignment-off .assignField{ display:none; }
```

- [ ] **Step 2: Remove the unconditional inline hide**

Find this block (around line 466-472):

```javascript
  const ASSIGNMENT_ENABLED = true;
  // Batch-assign (multiple alerts -> one person) is out of scope for the
  // multi-assignee trial — only the per-alert modal picker is gated by the
  // flag below. This stays hidden unconditionally until batch-assign is
  // redesigned for the array-based assigned_to contract.
  document.getElementById("assignControls").style.display = "none";
  document.body.classList.toggle("assignment-off", !ASSIGNMENT_ENABLED);
```

Replace it with:

```javascript
  const ASSIGNMENT_ENABLED = true;
  document.body.classList.toggle("assignment-off", !ASSIGNMENT_ENABLED);
```

(Visibility is now driven entirely by the CSS rule from Step 1, consistent with how `.assignee-field` is already handled.)

- [ ] **Step 3: Add the dropdown-population function**

Find `loadTeamMembers` (around line 742-748):

```javascript
  async function loadTeamMembers() {
    try {
      teamMembers = await callApi("/team-members");
    } catch (e) {
      console.error("team-members failed", e);
    }
  }
```

Add a new function directly after it:

```javascript
  async function loadTeamMembers() {
    try {
      teamMembers = await callApi("/team-members");
    } catch (e) {
      console.error("team-members failed", e);
    }
  }

  function populateBatchAssignSelect() {
    const select = document.getElementById("batchAssignInput");
    if (!select) return;
    select.innerHTML = '<option value="" disabled selected>Select a teammate…</option>' +
      teamMembers.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
  }
```

- [ ] **Step 4: Call it from `init()`**

Find (around line 1164-1165):

```javascript
    await Promise.all([loadMetrics(), loadVendors(), ...(ASSIGNMENT_ENABLED ? [loadTeamMembers()] : [])]);
    await loadAlerts();
```

Replace with:

```javascript
    await Promise.all([loadMetrics(), loadVendors(), ...(ASSIGNMENT_ENABLED ? [loadTeamMembers()] : [])]);
    if (ASSIGNMENT_ENABLED) populateBatchAssignSelect();
    await loadAlerts();
```

- [ ] **Step 5: Verify the embedded script still parses**

The file has no build step, so check the `<script>` block's syntax directly with Node:

```bash
cd "__staging_multiassignee2"
awk '/<script>/{flag=1;next}/<\/script>/{flag=0}flag' index.html > check_tmp.js
node --check check_tmp.js
rm check_tmp.js
```

Expected: no output, exit code 0 (a syntax error would print a `SyntaxError` with a line number).

- [ ] **Step 6: Confirm the old unconditional hide is gone**

```bash
grep -n 'assignControls.*style.display' "__staging_multiassignee2/index.html"
```

Expected: no matches.

- [ ] **Step 7: Commit**

```bash
cd "__staging_multiassignee2"
git add index.html
git commit -m "Show and populate the batch-assign dropdown"
```

---

### Task 3: Frontend — rewrite the batch-assign click handler (add-not-replace, partial-failure tolerant)

**Files:**
- Modify: `__staging_multiassignee2/index.html` (`batchAssignBtn` click handler, around line 835-864)

**Interfaces:**
- Consumes: `selectedIds` (global `Set`, shared with Snooze), `currentAlerts` (global array of alert objects with `.id` and `.assigned_to`), `#batchAssignInput` populated by Task 2, `callApi(path, opts)` (existing helper), `showPopup(message)` (existing helper).
- Produces: on click, PATCHes every selected alert with `assigned_to` = existing array + the chosen email (deduped), tolerating individual failures.

- [ ] **Step 1: Read the current handler**

Confirm it still reads (around line 835-864):

```javascript
  document.getElementById("batchAssignBtn").addEventListener("click", async () => {
    const assignee = document.getElementById("batchAssignInput").value.trim();
    if (!assignee || selectedIds.size === 0) return;

    const btn = document.getElementById("batchAssignBtn");
    btn.disabled = true;
    btn.textContent = "Assigning…";

    const now = new Date().toISOString();
    try {
      await Promise.all(
        Array.from(selectedIds).map(id =>
          callApi(`/alerts/${id}`, {
            method: "PATCH",
            body: JSON.stringify({ assigned_to: assignee, assigned_at: now })
          })
        )
      );
      selectedIds.clear();
      document.getElementById("batchAssignInput").value = "";
      updateBatchBar();
      btn.textContent = "Assign selected";
      await loadAlerts();
    } catch (err) {
      btn.textContent = "Assign selected";
      btn.disabled = false;
      console.error("Batch assign failed", err);
      showPopup("Some assignments may have failed. Check console for details.");
    }
  });
```

- [ ] **Step 2: Replace it**

```javascript
  document.getElementById("batchAssignBtn").addEventListener("click", async () => {
    const assignee = document.getElementById("batchAssignInput").value.trim();
    if (!assignee || selectedIds.size === 0) return;

    const btn = document.getElementById("batchAssignBtn");
    btn.disabled = true;
    btn.textContent = "Assigning…";

    const now = new Date().toISOString();
    const ids = Array.from(selectedIds);
    const results = await Promise.allSettled(
      ids.map(id => {
        const alert = currentAlerts.find(a => String(a.id) === String(id));
        const nextAssignedTo = Array.from(new Set([...(alert?.assigned_to || []), assignee]));
        return callApi(`/alerts/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ assigned_to: nextAssignedTo, assigned_at: now })
        });
      })
    );

    const failed = results.filter(r => r.status === "rejected").length;
    selectedIds.clear();
    document.getElementById("batchAssignInput").value = "";
    updateBatchBar();
    btn.textContent = "Assign selected";
    await loadAlerts();

    if (failed > 0) {
      console.error("Batch assign: failures", results.filter(r => r.status === "rejected"));
      showPopup(`Assigned ${ids.length - failed} of ${ids.length} — ${failed} failed. Check console for details.`);
    }
  });
```

Note: `btn.disabled` is left `true` after this runs — `updateBatchBar()` (called just above) sets it based on `selectedIds.size`, which is now `0`, so it stays disabled until the user selects alerts again. This matches the pre-existing behavior on the success path.

- [ ] **Step 3: Verify the embedded script still parses**

```bash
cd "__staging_multiassignee2"
awk '/<script>/{flag=1;next}/<\/script>/{flag=0}flag' index.html > check_tmp.js
node --check check_tmp.js
rm check_tmp.js
```

Expected: no output, exit code 0.

- [ ] **Step 4: Confirm the dedup/add logic in isolation**

This is the one piece of new logic worth exercising directly (mirrors exactly what Step 2 does):

```bash
node -e "
const alert = { assigned_to: ['existing@muc-corp.com'] };
const assignee = 'new@muc-corp.com';
const result = Array.from(new Set([...(alert.assigned_to || []), assignee]));
console.log(JSON.stringify(result));                 // expect [\"existing@muc-corp.com\",\"new@muc-corp.com\"]

const alertNoAssignee = { assigned_to: [] };
console.log(JSON.stringify(Array.from(new Set([...(alertNoAssignee.assigned_to || []), assignee]))));  // expect [\"new@muc-corp.com\"]

const alertDup = { assigned_to: ['new@muc-corp.com'] };
console.log(JSON.stringify(Array.from(new Set([...(alertDup.assigned_to || []), assignee]))));  // expect [\"new@muc-corp.com\"] (no duplicate)
"
```

Expected output (three lines):
```
["existing@muc-corp.com","new@muc-corp.com"]
["new@muc-corp.com"]
["new@muc-corp.com"]
```

- [ ] **Step 5: Commit**

```bash
cd "__staging_multiassignee2"
git add index.html
git commit -m "Bulk-assign adds to existing assignees instead of replacing them"
```

---

### Task 4: Frontend — fix the modal assignee picker's replace-instead-of-add bug

**Files:**
- Modify: `__staging_multiassignee2/index.html` (`assigneePanel` change handler inside `openModal`, around line 1023-1029)

**Interfaces:**
- Consumes: `selectedAssignees` (global `Set`, set up in `openModal`).
- Produces: checking a new teammate in the modal's assignee panel now adds to `selectedAssignees` instead of replacing it — required so alerts that Task 3's bulk-assign gave 2+ assignees don't get silently collapsed back to 1 the next time someone edits them through the modal.

- [ ] **Step 1: Read the current handler**

Confirm it still reads (around line 1023-1029):

```javascript
      assigneePanel.addEventListener("change", (e) => {
        if (!e.target.classList.contains("assigneeOption")) return;
        const m = e.target.dataset.member;
        if (e.target.checked) selectedAssignees = new Set([m]); else selectedAssignees.delete(m);
        renderAssigneeChips();
        renderAssigneePanel(assigneeSearchInput.value);
      });
```

- [ ] **Step 2: Fix it**

```javascript
      assigneePanel.addEventListener("change", (e) => {
        if (!e.target.classList.contains("assigneeOption")) return;
        const m = e.target.dataset.member;
        if (e.target.checked) selectedAssignees.add(m); else selectedAssignees.delete(m);
        renderAssigneeChips();
        renderAssigneePanel(assigneeSearchInput.value);
      });
```

- [ ] **Step 3: Verify the embedded script still parses**

```bash
cd "__staging_multiassignee2"
awk '/<script>/{flag=1;next}/<\/script>/{flag=0}flag' index.html > check_tmp.js
node --check check_tmp.js
rm check_tmp.js
```

Expected: no output, exit code 0.

- [ ] **Step 4: Confirm no other place in the file still replaces the set**

```bash
grep -n 'selectedAssignees = new Set' "__staging_multiassignee2/index.html"
```

Expected: one match only — the initial assignment in `openModal` (`selectedAssignees = new Set(alert.assigned_to || []);`), not the change handler.

- [ ] **Step 5: Commit**

```bash
cd "__staging_multiassignee2"
git add index.html
git commit -m "Fix modal assignee picker to add rather than replace selections"
```

---

### Task 5: Deploy to staging and run full manual verification

**Files:** none (deployment + manual browser verification only)

**Interfaces:**
- Consumes: all changes from Tasks 1-4.
- Produces: confirmation that the feature works end-to-end against the real staging backend and DB.

- [ ] **Step 1: Confirm all four prior commits are present**

```bash
cd "__staging_multiassignee2"
git log --oneline -5
```

Expected: the four commits from Tasks 1-4 at the top, in order.

- [ ] **Step 2: Publish the Function App backend first — ask the user for explicit confirmation before deploying anything**

The static frontend (`index.html`) and the Python backend (`function_app.py`) deploy through two independent pipelines: the GitHub Actions workflow (`.github/workflows/azure-static-web-apps-gray-stone-074e90d0f.yml`) only uploads static content (`api_location: ""`), it does NOT publish the Function App. The backend at `https://alerts-functions-staging.azurewebsites.net` is published separately (e.g. `func azure functionapp publish <staging-function-app-name>` from this directory, or via the VS Code Azure Functions extension).

Publish the backend **before** pushing the frontend. This order is safe either way (Task 1's validation change only becomes stricter-to-looser, never the other way — old and new frontends both work against the new backend), but publishing backend-first means there's never a window where the new frontend sends multi-length arrays to an old backend that still 400s on them.

Do not run either deploy without the user explicitly confirming it's OK to deploy right now.

- [ ] **Step 3: Sanity-check the backend deploy directly**

Before touching the UI, confirm the deployed backend actually accepts multi-length `assigned_to` arrays — a PATCH against any real staging alert id with a 2-element array should return 200, not 400. If it 400s, the Function App publish didn't take; stop and re-publish before proceeding to the frontend push.

- [ ] **Step 4: Push the frontend to the branch staging deploys from — ask the user for explicit confirmation first**

This pushes to a shared remote and triggers the GitHub Actions deploy workflow.

```bash
git push origin staging
```

- [ ] **Step 5: Wait for the deploy workflow to finish**

Check the Actions run for the pushed commit (via `gh run list --branch staging` or the GitHub UI) and confirm it succeeded before testing.

- [ ] **Step 6: Manually verify in the browser**

1. Log into the deployed staging app. Multi-select several alerts via checkboxes — include some with zero existing assignees and some with one existing assignee.
2. Pick a teammate from the batch dropdown (now visible in the batch bar) and click "Assign selected."
3. Confirm: alerts that had no assignee now show exactly the chosen person; alerts that already had someone now show both people; the success/fail count shown matches what actually happened.
4. Open the modal for one of the two-assignee alerts and check a different teammate's checkbox. Confirm all three people remain checked/listed (not collapsed to one).
5. Re-run the bulk action with the same person on an already-assigned alert. Confirm no duplicate entry appears.
6. Switch the Assignee filter to "Me" on an alert that now has you as one of 2+ assignees. Confirm it still shows up (this exercises the `assignee=me` read-path fix that shipped alongside this task's other fixes — if it's missing from the deployed alerts view, stop and report it rather than assuming it's a UI refresh issue).
7. Select some alerts, then change a status/type filter or type in the search box *without* deselecting, then click "Assign selected." Confirm the popup reports the stale ones as failed/skipped rather than the assignment silently overwriting anything (this is the regression commit `1aa230a` fixed).

- [ ] **Step 7: Report results**

If any step in Step 4 doesn't match the expected outcome, stop and report exactly what happened instead — do not attempt further fixes without checking back in, since this is the point where real user-facing bugs would surface for the first time.
