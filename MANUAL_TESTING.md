# JobPilot Manual Testing Quick Start

This file provides the fastest path to start testing JobPilot locally.

## One-Time Setup (First Time Only)

### 1. Environment Setup

```bash
# Copy environment template
cp .env.example .env

# Edit .env if needed (optional - defaults should work)
# For real AI features, add your ANTHROPIC_API_KEY
```

### 2. Backend Setup

```bash
cd backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Seed with test data (creates test user and jobs)
python -m scripts.seed_manual_test_data
```

### 3. Frontend Setup

```bash
cd frontend
npm install
```

### 4. Start PostgreSQL (First Time)

```bash
# From project root, in a separate terminal
docker-compose up -d postgres

# Wait ~10 seconds for database to be ready
docker-compose logs postgres | grep "ready to accept"
```

## Quick Start (Every Time You Want to Test)

### Terminal 1: Start PostgreSQL
```bash
docker-compose up postgres
```

### Terminal 2: Start Backend
```bash
cd backend
source venv/bin/activate  # On Windows: venv\Scripts\activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Terminal 3: Start Frontend
```bash
cd frontend
npm run dev
```

### Open Browser
```
http://localhost:3000
```

## Login Credentials

After seeding, use:
- **Email**: `test.user@example.local`
- **Password**: `TestPassword123!`

## Test Data Included

After seeding, your local environment has:

✓ **1 Test User** with complete profile:
- 8 skills (Python, TypeScript, React, PostgreSQL, AWS, Docker, FastAPI, GraphQL)
- 3 languages (German native, English C1, Spanish basic)
- 7 years experience at Senior level
- Located in Munich, prefers hybrid/remote work

✓ **1 Realistic Resume** with:
- 3 employers and 7 years experience
- Technical skills and education
- Multiple job titles and dates

✓ **8 Test Jobs** covering different scenarios:
- Job A: Excellent match (Senior Backend Engineer)
- Job B: Good match (Full-Stack Engineer)
- Job C: Weak match (Java Developer)
- Job D: Missing info (no salary/work model)
- Job E: Deal breaker (fully on-site)
- Job F: Cross-source duplicate (DevOps Engineer on Greenhouse + Lever)
- Job G: German posting (Senior Softwareentwickler)
- Job H: Inactive/expired job

✓ **5 Sample Applications** in different states:
- DRAFT (Job C)
- PREPARING (Job B)
- IN_PROGRESS (Job D)
- INTERVIEWING (Job G, German)
- REJECTED (Job H)
- Plus space to create your own for Job A

## Complete Test Checklist

Go through these steps to verify everything works:

### Phase 1: Authentication & Profile (5 min)
- [ ] Login works
- [ ] Dashboard displays profile completeness
- [ ] Can view profile
- [ ] Can edit profile and changes persist

### Phase 2: Resume & Jobs (10 min)
- [ ] Resume displays (no upload needed, pre-seeded)
- [ ] Job list shows 8 jobs
- [ ] Can view job details
- [ ] Can save/unsave jobs

### Phase 3: Matching (5 min)
- [ ] Can see match scores on Job A
- [ ] Match details show strengths/gaps/deal-breakers
- [ ] Job E shows deal-breaker (on-site)
- [ ] Job D shows "unknown" handling

### Phase 4: Application & Documents (10 min)
- [ ] Create application for Job A
- [ ] Generate CV (should complete quickly)
- [ ] Generate Cover Letter
- [ ] Verify CV and letter content are accurate
- [ ] No invented information

### Phase 5: Submission & Tracking (5 min)
- [ ] Review application (final step)
- [ ] Submit application
- [ ] Status changes to "SUBMITTED"
- [ ] View in Application Tracking
- [ ] Change status to "INTERVIEWING"
- [ ] Dashboard KPIs update

**Total Time**: ~35 minutes for complete manual test

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

## Troubleshooting

### PostgreSQL won't start
```bash
# Check if container is running
docker-compose ps

# View logs
docker-compose logs postgres

# Force restart
docker-compose down
docker-compose up postgres
```

### Backend migration fails
```bash
# Verify database connection
psql -U jobpilot -h localhost -d jobpilot -c "SELECT 1"

# Re-run migrations
alembic upgrade head

# If that fails, reset and reseed
python -m scripts.seed_manual_test_data --reset
```

### Frontend won't start
```bash
# Clear Next.js cache
rm -rf .next

# Reinstall dependencies
npm install

# Start again
npm run dev
```

### Port already in use
```bash
# Kill process on port 3000 (frontend)
lsof -ti:3000 | xargs kill -9

# Kill process on port 8000 (backend)
lsof -ti:8000 | xargs kill -9

# Kill postgres container
docker-compose down

# Start fresh
```

## What to Test

1. **Happy Path**: Login → Profile → Resume → Jobs → Match → Create App → Generate Docs → Submit → Track
2. **Job Matching**: Verify Job A matches well, Job E shows deal-breaker, Job D handles unknown data
3. **Documents**: CV/CL generation should complete quickly with realistic content
4. **Data Persistence**: Changes should survive navigation and page reloads
5. **Cross-source Handling**: Job F (DevOps) should show as coming from Greenhouse + Lever
6. **Application Lifecycle**: Track application through multiple statuses

## Important Notes

- ✓ **Real Database**: Everything uses the real database, not mocks
- ✓ **Real API**: All backend code is production code
- ✓ **Real Frontend**: All frontend code is production code  
- ✓ **Seeded Data Only**: Test data is created via seed script, not hardcoded
- ✓ **No Backend Bypassing**: Browser automation would test real form filling (if enabled)
- ✗ **Not a Demo**: Not a fake UI or hardcoded dashboard

## Next: Manual Testing Guide

After setup, follow `docs/manual-product-test.md` for detailed test steps.

After testing, record findings in `docs/product-validation.md`.

## Questions?

- Check backend logs: Look in Terminal 2 for API errors
- Check frontend logs: Open browser DevTools (F12) → Console
- Check database: `psql -U jobpilot -d jobpilot`
- Read the detailed guides: `docs/manual-product-test.md`

## Production Checklist

Before shipping (not needed for V0.12 manual testing):

- [ ] No hardcoded test data in production
- [ ] No fake job boards in production
- [ ] Real AI provider configured
- [ ] Real job sources enabled
- [ ] Real database backend
- [ ] HTTPS enforced
- [ ] Monitoring configured
- [ ] Error handling tested
- [ ] Performance validated
- [ ] Security audit passed

---

**Ready to test?** Start with: `docker-compose up postgres` in one terminal, then follow "Quick Start" above.
