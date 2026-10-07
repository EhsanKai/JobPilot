# JobPilot Manual Product Test Guide

This guide provides step-by-step instructions to manually test JobPilot as an end user, from registration through application tracking.

## Setup

Only Docker is needed (Docker Desktop, or Docker Engine with the Compose plugin). No
PostgreSQL, Python, Node.js or `psql` on your machine. Details: `docs/docker-development.md`.

```bash
cp .env.example .env                                  # optional: defaults work as they are
docker compose --profile manual-test up --build       # database, migrations, API, UI, test data
```

Wait for `seed-1 | Seeded synthetic manual-test data:` in the output, then open
http://localhost:3000. (In another terminal, `./start-dev.sh status` shows the services.)

The database schema is created and upgraded automatically every time the backend starts;
you never run migrations by hand.

## Test Account Credentials

- **Email**: `test.user@example.com`
- **Password**: `TestPassword123!`

`example.com` is a domain reserved for examples (RFC 2606) and passes JobPilot's real
e-mail validation. (Earlier notes used `test.user@example.local`; `.local` is a special-use
name that the validation correctly rejects, so that account could never log in.)

### What the seed creates

All synthetic -- the person, "Musterfirma Demo GmbH" and its postings do not exist:

- Max Mustermann, Senior Software Engineer in Munich: profile, 8 skills, German / English /
  Spanish, job preferences (Senior Backend Engineer, Munich/Berlin, hybrid/remote, EUR
  75-90k, deal breaker: working-student roles).
- One resume (DOCX), uploaded but **not analyzed**: analysis needs an AI provider
  (`ANTHROPIC_API_KEY` in `.env`).
- 7 jobs from the real discovery pipeline (8 postings: one job is on Greenhouse AND Lever):

  | Job | Scenario |
  |---|---|
  | Senior Python Backend Engineer (Munich) | excellent match; on both sources |
  | Senior Full-Stack Engineer (TypeScript / Python), remote | good match |
  | Staff Platform Engineer (Berlin) | partial match (Kubernetes, Go missing) |
  | Data Engineer (Spark / Scala), Frankfurt | weak match |
  | Junior Frontend Developer (Vue.js), Hamburg | seniority mismatch; **saved** |
  | Engineering Manager Backend (Munich) | management role |
  | Working Student Software Development (Köln, Lever only) | deal breaker |

- Applications: PREPARING (Senior Python Backend Engineer), APPLIED with a follow-up date
  in 7 days (Full-Stack), INTERVIEW (Staff Platform), REJECTED (Data Engineer), WITHDRAWN
  (Engineering Manager).

The postings' "apply" links point at pages that do not exist; use the Browser Assistant
against a real posting (or the E2E fake site) to test form filling.

## Test Flow

### Test 1: Login and Dashboard

**Objective**: Verify basic navigation and dashboard appearance.

**Steps**:
1. Open http://localhost:3000
2. You should see the login page
3. Enter the test account credentials
4. Click "Sign In"

**Expected Results**:
- ✓ Login succeeds
- ✓ Redirected to dashboard
- ✓ Dashboard shows:
  - Profile completeness (should be ~90%)
  - Number of relevant jobs found
  - Number of applications
  - Number of tailored CVs created
  - Response rate (should be calculable: Responses / Applications)

**Notes**:
- The dashboard should display real numbers from the database, not hardcoded values
- Response rate calculation: (INTERVIEW + OFFER + REJECTED) / total applications
- NO_RESPONSE, APPLIED, FOLLOW_UP are not counted as responses

**Document Findings**:
- [ ] Dashboard loaded correctly
- [ ] All KPI cards display real data
- [ ] Numbers are consistent with database

---

### Test 2: Profile

**Objective**: Verify the profile editing works correctly and profile data persists.

**Steps**:
1. From dashboard, click "Profile" in navigation
2. Review the populated profile data:
   - Personal info (name, email, phone, location)
   - Professional info (current job, years of experience, seniority)
   - Skills (8 skills should be visible)
   - Languages (German, English, Spanish)
   - Job preferences
   - Deal breakers

3. Edit one field:
   - Change location to "Berlin, Deutschland"
   - Click save

4. Navigate away and back to profile

**Expected Results**:
- ✓ All profile data displays correctly
- ✓ Edit succeeds
- ✓ Changes persist after navigation
- ✓ Profile completeness indicator updates if needed

**UX Quality Checks**:
- [ ] Can I understand what each field is for?
- [ ] Are errors clear?
- [ ] Is the saved state obvious?

**Document Findings**:
- [ ] Profile loads and displays correctly
- [ ] Edits persist
- [ ] No confusing or technical language

---

### Test 3: Resume

**Objective**: Verify resume upload, parsing, and review flow.

**Steps**:
1. From profile, go to "Resume" tab
2. You should see an existing resume version

**For Testing Resume Upload** (Optional, only if you want to test upload):
3. Click "Upload New Resume"
4. Upload a PDF resume (you can create a simple one or use a real resume)
5. Wait for parsing to complete
6. Review the extracted information:
   - Employment history
   - Education
   - Skills
   - Languages

**Expected Results**:
- ✓ Resume displays
- ✓ If uploading: extraction completes successfully
- ✓ Extracted data matches the resume content
- ✓ Can accept, edit, or reject facts
- ✓ Confirmation saves the data

**Edge Cases to Test**:
- Upload invalid PDF (if test PDFs available)
- Refresh page during parsing
- Navigate away and back

**Document Findings**:
- [ ] Resume parsing works
- [ ] Extracted facts are accurate
- [ ] UI clearly shows what's happening
- [ ] User can understand the extraction process

---

### Test 4: Job Discovery

**Objective**: Verify job discovery, filtering, and display.

**Steps**:
1. From dashboard, click "Jobs" in navigation
2. Review the job list:
   - Should show 7 jobs (8 postings; one job has two sources)
   - Jobs have source labels (Greenhouse, Lever, etc.)
   - Can see job title, company, location, salary range

3. Test filtering/sorting:
   - Apply filters (by location, salary, work model)
   - Sort by relevance/date
   - Verify results update correctly

4. Click on a job to view details:
   - Should see full job posting
   - Company info
   - Location and work model
   - Salary range
   - Required skills and technologies
   - Original posting link
   - Sources (if job is from multiple sources)

5. Test save/unsave:
   - Click "Save Job"
   - Verify button state changes
   - Navigate back to job list
   - Verify save status persists

**Expected Results**:
- ✓ Job list loads with 7 jobs
- ✓ Job details page is readable
- ✓ Filters work correctly
- ✓ Save/unsave works
- ✓ The cross-source job (Senior Python Backend Engineer) shows both sources

**Seeded jobs**: see "What the seed creates" above. The cross-source job is
"Senior Python Backend Engineer" (Greenhouse + Lever).

**Document Findings**:
- [ ] All jobs display correctly
- [ ] Filtering works
- [ ] Save functionality works
- [ ] Source labels are helpful

---

### Test 5: Job Matching

**Objective**: Verify matching engine and match explanations.

**Steps**:
1. From job list, click on "Senior Python Backend Engineer"
2. Review the match card (should be visible on job details):
   - Job Match score (technical fit)
   - Preference Match score (preference fit)
   - Overall Match score (70% job + 30% preference)
   - Recommendation (Strong Match, etc.)

3. Click "View Matching Details" or "See Full Analysis"
4. Review the detailed analysis:
   - **Strengths**: What aligns well
   - **Gaps**: What's missing
   - **Unknowns**: What information is missing
   - **Deal Breakers**: Any deal breakers triggered
   - **Evidence**: Where information comes from

5. Test a few different jobs:
   - Job A: Should be Strong Match
   - Job B: Should be Good Match
   - Job C: Should be Weak Match
   - Job E: Should show deal breaker (on-site requirement)

**Expected Results**:
- ✓ Match scores calculate correctly
- ✓ Scores are explained, not just numbers
- ✓ Evidence is provided for each claim
- ✓ Unknown information is not counted against you
- ✓ Deal breakers are clearly highlighted
- ✓ AI explanation (if available) is readable and accurate

**UX Quality Checks**:
- [ ] Can I understand why a job scored what it did?
- [ ] Is the explanation fair to weaker matches?
- [ ] Are deal breakers obvious?
- [ ] Does the score seem reasonable?

**Document Findings**:
- [ ] Matching engine works correctly
- [ ] Scores make sense
- [ ] Deal breakers are enforced
- [ ] Explanation is clear

---

### Test 6: Application Workspace

**Objective**: Verify the application creation and preparation flow.

**Steps**:
1. From Job A details, click "Start Application"
2. Review the application workspace:
   - Job snapshot (as it was when you started)
   - Company and position info
   - Original posting link
   - Selected resume
   - Salary expectation (should be pre-filled from profile)
   - Availability date
   - Work authorization
   - Notes field

3. Make one change:
   - Edit the salary expectation
   - Add a note like "Strong interest in this role"
   - Click save

4. Navigate away and back to the application

**Expected Results**:
- ✓ Application workspace opens
- ✓ All data fields display correctly
- ✓ Changes persist
- ✓ Job snapshot matches what was shown in discovery
- ✓ Resume is pre-selected correctly

**UX Quality Checks**:
- [ ] Is it clear what each field means?
- [ ] Are required fields marked?
- [ ] Is the saved state obvious?

**Document Findings**:
- [ ] Application workspace works
- [ ] Data persists correctly
- [ ] Job snapshot is accurate

---

### Test 7: Tailored CV Generation

**Objective**: Verify CV generation and editing.

**Steps**:
1. From the application workspace for Job A, go to "Documents" section
2. Click "Generate CV" or "Generate Tailored CV"
3. Wait for generation to complete (should show a spinner/progress)
4. Review the generated CV:
   - Should include resume facts
   - Should be tailored to job requirements
   - Should be factually accurate
   - Should not invent employers, skills, or dates

5. Test editing:
   - Click "Edit CV"
   - Make a small change (edit a bullet point)
   - Save the changes

6. Test regeneration:
   - Click "Generate New Version"
   - Verify new version is created with higher version number
   - Verify previous edits are preserved in the old version

**Expected Results**:
- ✓ CV generation completes successfully
- ✓ Generated CV is readable and well-formatted
- ✓ Content matches resume facts (no invented information)
- ✓ Can edit the CV
- ✓ Regeneration creates new versions (v1, v2, etc.)
- ✓ User edits are not overwritten by regeneration

**Truth Validation**:
- [ ] No invented employers
- [ ] No invented skills or technologies
- [ ] No invented employment dates
- [ ] All claims are supported by resume

**Document Findings**:
- [ ] CV generation works
- [ ] Generated content is accurate
- [ ] Edits persist
- [ ] Version history works

---

### Test 8: Cover Letter Generation

**Objective**: Verify cover letter (Anschreiben) generation and language handling.

**Steps**:
1. From the same application, go to "Documents" and click "Generate Cover Letter"
2. Wait for generation
3. Review the generated letter:
   - Should address the specific job
   - Should reference relevant skills
   - Should mention salary expectation if you selected "Yes"
   - Should be written in English (since Job A is English)

4. Switch to a German job (Job G) and create an application
5. Generate cover letter for German job
6. Verify it's written in German

**Expected Results**:
- ✓ Cover letter generates successfully
- ✓ Letter is tailored to job
- ✓ Salary expectation appears only if enabled
- ✓ Language follows the application language setting
- ✓ No invented information
- ✓ Can edit and regenerate

**Document Findings**:
- [ ] Cover letter generation works
- [ ] Language selection works
- [ ] Content is accurate and relevant
- [ ] Salary handling is correct

---

### Test 9: Application Questions

**Objective**: Verify question answering and AI suggestions.

**Steps**:
1. From application documents, go to "Questions" section
2. Scroll through any application questions
3. For each question:
   - Read the AI suggestion (if available)
   - Optionally edit the suggestion
   - Or write your own answer
   - Mark as complete

4. Test regeneration:
   - Click regenerate on a question
   - Verify new suggestion appears
   - Verify your manual answer is preserved if you had edited

**Expected Results**:
- ✓ Questions load correctly
- ✓ AI suggestions are reasonable
- ✓ Can accept, edit, or replace suggestions
- ✓ Manual answers are preserved during regeneration
- ✓ Character limits (if any) are enforced

**Document Findings**:
- [ ] Question answering flow works
- [ ] AI suggestions are helpful
- [ ] Manual answers are respected

---

### Test 10: Browser Assistant (If Applicable)

**Objective**: Test browser automation form filling (if configured).

**Note**: This requires the fake employer site to be running. See "Fake Employer Setup" below.

**Steps**:
1. From application, go to "Submit" or "Fill Application" section
2. Click "Start Browser Assistant" or "Auto-Fill Application"
3. The browser assistant should:
   - Discover the application form
   - Map form fields to application data
   - Request your approval before filling
   - Fill fields with prepared data
   - Handle any special fields (consents, etc.)

4. Test user takeover:
   - If a field needs manual input, pause the assistant
   - Make manual changes
   - Resume the assistant

5. Test consent handling:
   - If there's a consent checkbox, verify you must approve it
   - Never auto-accept consents

**Expected Results**:
- ✓ Form discovery works
- ✓ Field mapping is accurate
- ✓ User approvals are requested before filling
- ✓ Consent fields are never auto-filled
- ✓ Can take control mid-process
- ✓ Manual changes are preserved

**Safety Checks**:
- [ ] Consents are never auto-accepted
- [ ] User gets clear approval step
- [ ] Can pause and edit any field
- [ ] Can see what will be filled before submission

**Document Findings**:
- [ ] Browser assistant works
- [ ] Form filling is accurate
- [ ] Safety mechanisms work

---

### Test 11: Final Review

**Objective**: Verify final review page before submission.

**Steps**:
1. Before submitting an application, review the final review page:
   - All prepared data should be visible
   - Questions and answers should be shown
   - Document versions should be listed
   - Consents should be clearly marked
   - A "Submit" button should be visible

2. Verify all data is correct

**Expected Results**:
- ✓ All data is visible and editable
- ✓ Document versions are shown
- ✓ Questions and answers are displayed
- ✓ Changes to reviewed data mark the review as invalid

**Document Findings**:
- [ ] Final review is comprehensive
- [ ] All data is visible
- [ ] Clear path to submit

---

### Test 12: Application Submission

**Objective**: Test the submission flow and status tracking.

**Steps**:
1. From final review, click "Submit Application"
2. Wait for submission to complete
3. Verify success message
4. Check that application status changed to "SUBMITTED"
5. Navigate to application tracking to verify status update

**Expected Results**:
- ✓ Submission succeeds
- ✓ Success message shown
- ✓ Application status changes to "SUBMITTED"
- ✓ Submission timestamp is recorded
- ✓ Cannot re-submit same application

**Failure Testing** (If applicable):
- Test what happens if submission fails
- Verify application status remains unchanged
- Verify can retry

**Document Findings**:
- [ ] Submission works correctly
- [ ] Status updates properly
- [ ] Success is clearly communicated

---

### Test 13: Application Tracking

**Objective**: Verify application lifecycle management.

**Steps**:
1. From dashboard, go to "Applications" or "Tracking"
2. Review the application list:
   - Should show 5 seeded applications in different states
   - Can filter by status
   - Can sort by date

3. Click on an application to view details
4. Test status transitions:
   - Click application
   - Change status from PREPARING → APPLIED
   - Verify status persists
   - Try other transitions (APPLIED → INTERVIEW, etc.)

5. Test notes:
   - Add a note to the application
   - Verify note persists
   - Verify note is visible in history

6. Test the timeline:
   - Application should show key events
   - Status changes should be timestamped
   - Creation/submission dates should be visible

**Seeded Application Statuses**:
- Job B: PREPARING (most common during manual testing)
- Job C: DRAFT
- Job D: IN_PROGRESS
- Job G: INTERVIEWING
- Job H: REJECTED

**Expected Results**:
- ✓ Application list displays all applications
- ✓ Status transitions work
- ✓ Notes persist
- ✓ Timeline shows accurate history
- ✓ No invalid status transitions allowed

**Document Findings**:
- [ ] Tracking interface works
- [ ] Status transitions are clear
- [ ] History/timeline is understandable

---

### Test 14: Dashboard Verification

**Objective**: Verify dashboard KPIs update correctly.

**Steps**:
1. Note the current dashboard numbers (KPI cards)
2. Go to Applications and change one application status from APPLIED to INTERVIEW
3. Navigate back to dashboard
4. Verify the dashboard numbers updated:
   - Response rate should update if the status change affected it
   - Application count should remain the same (you didn't add a new app)

5. Create a new application (if possible) or use an existing one
6. Submit it
7. Go back to dashboard
8. Verify the applications count updated

**Expected Results**:
- ✓ All KPI numbers are real (from database)
- ✓ Numbers update when data changes
- ✓ Response rate calculation is correct
- ✓ No hardcoded or stale values

**Calculation Verification**:
- Response Rate = (INTERVIEW + OFFER + REJECTED) / Total Applications
- NO_RESPONSE, APPLIED, FOLLOW_UP are NOT responses

**Document Findings**:
- [ ] KPI cards show real data
- [ ] Numbers update correctly
- [ ] Calculations are accurate

---

## UX Quality Assessment

After completing the functional tests above, perform a UX review for each major page:

### Questions to Ask for Every Page

1. **Clarity**: Can I understand this page without reading documentation?
2. **Action**: Do I know what action to take next?
3. **Feedback**: When I do something, does the app clearly show the result?
4. **Error Handling**: Are error messages clear and actionable?
5. **Technical Debt**: Did I notice any technical terms that shouldn't be exposed to a user?
6. **Empty States**: When something is loading, empty, or failed, is it obvious?
7. **Safety**: Are destructive actions clearly warned?
8. **Help**: When I'm unsure, is there helpful guidance?

### Document Any UX Issues

For each issue found, record:

```
**Page**: [Page Name]
**Action**: [What were you trying to do?]
**Expected**: [What should have happened]
**Actual**: [What actually happened]
**Severity**: Critical / High / Medium / Low
**Notes**: [Additional context]
```

---

## Intentional Failure Testing

Do NOT only test the happy path. Intentionally try to break things:

- [ ] Upload an invalid PDF file (if UI allows)
- [ ] Refresh during job discovery
- [ ] Navigate backward in browser during application flow
- [ ] Open multiple browser tabs to the same application
- [ ] Change application data while another window is open
- [ ] Submit a form without filling required fields
- [ ] Try extremely long text in text fields
- [ ] Remove required information after entering it
- [ ] Regenerate a document multiple times
- [ ] Edit an application answer, then regenerate to see if edit is preserved
- [ ] Try to access another user's applications (should fail)

**Expected Results**:
- ✓ Application fails gracefully
- ✓ No data corruption
- ✓ Error messages are clear
- ✓ Can recover from most errors

---

## Security Smoke Test

Before finishing, do a basic security check:

- [ ] Passwords are not logged or visible in console
- [ ] Resume content is not logged unnecessarily
- [ ] Application answers are not logged
- [ ] API keys/secrets are not exposed
- [ ] Unauthenticated users cannot access protected pages
- [ ] User A cannot access User B's applications
- [ ] User A cannot access User B's resumes
- [ ] Uploaded files cannot be accessed without auth
- [ ] No sensitive data in frontend bundle (check browser console)

---

## Data Reset

Re-create only the test user (other accounts you registered stay):

```bash
./start-dev.sh seed --reset          # = docker compose run --rm seed --reset
```

Wipe everything (database AND uploaded files) and start from an empty database:

```bash
./start-dev.sh reset                 # = docker compose down -v, then up again with the seed
```

---

## Test Completion Checklist

After testing, mark these items:

### Core Functionality
- [ ] Login works with test credentials
- [ ] Dashboard displays and shows real KPI numbers
- [ ] Profile displays and can be edited
- [ ] Seeded resume is listed (analysis needs an AI key)
- [ ] Jobs list shows the 7 seeded jobs
- [ ] Job matching engine calculates scores
- [ ] Application workspace opens for a job
- [ ] CV generation creates realistic content
- [ ] Cover letter generation works
- [ ] Seeded applications display in tracking
- [ ] Application status can be changed
- [ ] Dashboard KPIs update when application status changes

### UX Quality
- [ ] Pages are clear without reading documentation
- [ ] Next steps are obvious
- [ ] Errors are clear and actionable
- [ ] Loading states are visible
- [ ] Empty states are useful
- [ ] Success is clearly communicated

### Data Integrity
- [ ] No invented information in generated documents
- [ ] Resume facts are preserved
- [ ] Application data persists
- [ ] Cross-source job (Senior Python Backend Engineer) shows both sources

### Safety & Security
- [ ] Can't access other users' data
- [ ] Consents/authorizations work correctly
- [ ] No passwords in logs/console
- [ ] Submission verification works

### Known Working Scenarios
- [ ] Senior Python Backend Engineer has the best match
- [ ] Data Engineer (Spark / Scala) scores low
- [ ] Working Student role is flagged by the deal breaker
- [ ] Junior Frontend Developer shows the seniority mismatch
- [ ] Privacy settings: download my data, AI switch, delete account (V0.13)

---

## Troubleshooting

See `MANUAL_TESTING.md` > Troubleshooting. The most useful commands:

```bash
./start-dev.sh status        # which service is up / healthy
./start-dev.sh logs backend  # migration and API log
./start-dev.sh reset         # clean slate
```

---

## Notes for Next Testing Session

Record anything you discover that should be fixed before shipping:

- [ ] [Issue 1]
- [ ] [Issue 2]
- [ ] [Issue 3]

---

## Success Criteria

The manual test is successful when:

1. ✓ You can complete the entire journey (login → profile → jobs → match → apply → track)
2. ✓ The application feels like a real product, not a prototype
3. ✓ You didn't need to read source code to understand what's happening
4. ✓ All major features work without errors
5. ✓ UX is clear and intuitive
6. ✓ Test data is realistic enough to feel like using a real job board
7. ✓ Dashboard numbers are real, not hardcoded
8. ✓ Safety mechanisms (consent, submission verification) work

---

## Questions?

If something is unclear, broken, or confusing during testing, record it in the validation guide (see `docs/product-validation.md`).
