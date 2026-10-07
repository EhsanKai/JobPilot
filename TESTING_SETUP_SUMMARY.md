# JobPilot V0.12 Manual Testing Setup

Everything you need to test JobPilot as a real end user is now ready.

## What's Been Created

### 1. **Seeding Script** (`backend/scripts/seed_manual_test_data.py`)
Generates realistic test data:
- 1 synthetic test user with complete profile
- 1 realistic resume with employment history
- 8 test jobs covering different scenarios
- 5 seeded applications in different states
- Cross-source duplicate detection example
- Deal breaker testing

### 2. **Manual Testing Guides**

#### `MANUAL_TESTING.md` (Quick Start)
Fast path to get running:
- Environment setup (one-time)
- Quick start (every session)
- Test credentials
- Known test data
- ~35-minute complete manual test

#### `docs/manual-product-test.md` (Detailed Guide)
Comprehensive step-by-step test of all features:
- 14 detailed test scenarios
- What to expect
- How to validate behavior
- UX quality checks
- Security smoke tests

#### `docs/product-validation.md` (Findings Log)
Template for recording issues and findings:
- Issue classification system
- Finding template
- UX quality ratings
- Data integrity verification
- Recommendations for V0.13

### 3. **Development Tools**

#### `start-dev.sh` (Development Server Script)
Helper script for starting everything:
```bash
./start-dev.sh check      # Verify prerequisites
./start-dev.sh setup      # Setup backend and frontend
./start-dev.sh postgres   # Start PostgreSQL
./start-dev.sh backend    # Start backend
./start-dev.sh frontend   # Start frontend
./start-dev.sh all        # Start everything
```

## Quick Start (5 minutes)

### First Time Setup
```bash
# Clone/open the repository
cd JobPilot

# Check everything is installed
./start-dev.sh check

# Setup backend and frontend
./start-dev.sh setup
```

### Every Time You Test

#### Terminal 1: Start PostgreSQL
```bash
./start-dev.sh postgres
# Or manually: docker-compose up postgres
```

#### Terminal 2: Start Backend
```bash
./start-dev.sh backend
# Or manually: cd backend && source venv/bin/activate && uvicorn app.main:app --reload
```

#### Terminal 3: Start Frontend
```bash
./start-dev.sh frontend
# Or manually: cd frontend && npm run dev
```

#### Open Browser
```
http://localhost:3000
```

## Test Account

**Email**: `test.user@example.local`  
**Password**: `TestPassword123!`

Use these credentials to log in after seeding.

## Test Data Overview

### Profile
- Senior Software Engineer, 7+ years experience
- Munich-based, prefers hybrid/remote work
- 8 technical skills (Python, TypeScript, React, PostgreSQL, AWS, Docker, FastAPI, GraphQL)
- 3 languages (German native, English fluent, Spanish basic)
- Salary range: €85,000 - €130,000

### Resume
Pre-seeded with:
- 3 employers and realistic employment history
- Technical skills and technologies
- Education (Bachelor's in Computer Science)
- Professional achievements

### Test Jobs (8 scenarios)

| Job | Company | Type | Purpose |
|-----|---------|------|---------|
| A | DataViz Systems | Excellent Match | Strong technical + preference alignment |
| B | WebServices | Good Match | Good technical, some preference mismatch |
| C | EnterpriseCorps | Weak Match | Skills gap but some overlap |
| D | CloudScale | Missing Info | No salary/work model data |
| E | OnSiteOnly | Deal Breaker | Fully on-site (violates preference) |
| F | MultiCloud | Cross-Source | Same job on Greenhouse + Lever |
| G | Mittelstand | German | German posting for language testing |
| H | NoLongerHiring | Inactive | Expired posting for freshness testing |

### Seeded Applications (for tracking)

| Job | Status | Purpose |
|-----|--------|---------|
| B | PREPARING | Most common state during manual testing |
| C | DRAFT | Not yet started |
| D | IN_PROGRESS | Application in process |
| G | INTERVIEWING | Interview phase (German) |
| H | REJECTED | Application rejected |

**Create your own** for Job A to test the complete journey.

## Test Workflow (35 minutes)

Follow `MANUAL_TESTING.md` for the quick test:

1. **Phase 1 (5 min)**: Login and Dashboard
2. **Phase 2 (10 min)**: Profile and Resume
3. **Phase 3 (5 min)**: Job Discovery and Matching
4. **Phase 4 (10 min)**: Create Application, Generate Documents
5. **Phase 5 (5 min)**: Submit and Track

## Resetting Test Data

If you want to start fresh:

```bash
cd backend
source venv/bin/activate
python -m scripts.seed_manual_test_data --reset

# Follow prompts (type 'yes' to confirm)
```

This will:
- Drop and recreate the database
- Run migrations
- Seed fresh test data
- Reset test credentials

## What to Look For

### Functional Testing
- ✓ Can complete entire journey without errors
- ✓ All data is real (from database, not hardcoded)
- ✓ Resume facts are accurate
- ✓ Generated documents make sense
- ✓ Application lifecycle works
- ✓ Dashboard numbers are dynamic

### UX Testing
- ✓ Can understand each page without documentation
- ✓ Next steps are obvious
- ✓ Errors are clear
- ✓ No confusing technical terms
- ✓ Loading/empty/success states are clear
- ✓ Safety mechanisms work (consents, verification)

### Data Integrity
- ✓ No invented information
- ✓ Cross-source duplicate (Job F) detected
- ✓ Deal breaker (Job E) shows properly
- ✓ Missing info (Job D) handled gracefully
- ✓ Application data persists

### Security
- ✓ Can't access other users' data
- ✓ No passwords in logs
- ✓ No secrets in frontend bundle

## Recording Findings

Use `docs/product-validation.md` to record:
- 🔴 Functional bugs
- 🟠 UX problems
- 🟡 UI issues
- 🔵 Product decisions
- 🟣 Missing features
- ⚪ Technical debt

## Troubleshooting

### PostgreSQL won't start
```bash
# Check if already running
docker-compose ps

# View logs
docker-compose logs postgres

# Force restart
docker-compose down
docker-compose up -d postgres
```

### Backend migration fails
```bash
cd backend
source venv/bin/activate

# Re-run migrations
alembic upgrade head

# If that fails, reset database
python -m scripts.seed_manual_test_data --reset
```

### Frontend won't start
```bash
cd frontend

# Clear cache
rm -rf .next

# Reinstall
npm install

# Start again
npm run dev
```

### Port conflicts
Kill processes and restart:
```bash
# macOS/Linux
lsof -ti:3000 | xargs kill -9  # Frontend
lsof -ti:8000 | xargs kill -9  # Backend

# Then restart via start-dev.sh or docker-compose
```

## Important Notes

✓ **Everything is real** — No mocks, no hardcoded data  
✓ **Use the real backend** — Production API code  
✓ **Use the real frontend** — Production Next.js app  
✓ **Real database** — All data in PostgreSQL  
✓ **Seeded data only** — Test data via seed script  
✓ **Repeatable testing** — Reset script available  

✗ **Not a demo** — Not a fake UI  
✗ **Not hardcoded** — Not fake responses  
✗ **Not bypassed** — Not mocking critical paths  

## What's NOT Included (Expected Limitations)

These features are planned for V0.13+ and are expected to be missing:

- [ ] Resume OCR for scanned PDFs
- [ ] Browser automation form filling (needs fake employer site setup)
- [ ] Email notifications
- [ ] Calendar integration
- [ ] LinkedIn integration
- [ ] Automatic application status inference
- [ ] GDPR data export/deletion
- [ ] User profile photos
- [ ] Analytics/tracking

## Next Steps

1. **Run setup**: `./start-dev.sh setup`
2. **Start services**: `./start-dev.sh postgres`, `backend`, `frontend` (3 terminals)
3. **Open browser**: `http://localhost:3000`
4. **Login**: Use test credentials above
5. **Follow guide**: `docs/manual-product-test.md`
6. **Record findings**: `docs/product-validation.md`

## Success Criteria

You'll know it's working when:

- ✓ Can log in with test credentials
- ✓ Dashboard shows real profile data and KPIs
- ✓ Can view all 8 test jobs
- ✓ Job matching engine calculates scores
- ✓ Can create an application for Job A
- ✓ Can generate CV and cover letter
- ✓ Can submit application and track it
- ✓ Dashboard updates when application changes
- ✓ Found no major bugs or confusing UX

## Support

If something breaks:

1. Check troubleshooting section above
2. Review backend logs (Terminal 2)
3. Check browser console (F12 → Console)
4. Check database: `psql -U jobpilot -d jobpilot`
5. Reset everything: `python -m scripts.seed_manual_test_data --reset`

---

**Ready to test?** Start with: `./start-dev.sh setup`
