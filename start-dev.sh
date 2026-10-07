#!/bin/bash

# JobPilot Development Server Startup Script
# This script helps you start the full development environment

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
POSTGRES_PORT=${POSTGRES_PORT:-5432}
BACKEND_PORT=${BACKEND_PORT:-8000}
FRONTEND_PORT=${FRONTEND_PORT:-3000}
DATABASE_URL="postgresql+psycopg://jobpilot:jobpilot@localhost:${POSTGRES_PORT}/jobpilot"

# Functions
print_header() {
    echo -e "\n${GREEN}=== $1 ===${NC}\n"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}ℹ $1${NC}"
}

check_postgres() {
    print_header "Checking PostgreSQL"

    if ! command -v psql &> /dev/null; then
        print_error "psql not found. Please install PostgreSQL client."
        return 1
    fi

    print_success "PostgreSQL client found"
    return 0
}

check_python() {
    print_header "Checking Python"

    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 not found. Please install Python 3.11+"
        return 1
    fi

    PYTHON_VERSION=$(python3 --version | awk '{print $2}')
    print_success "Python $PYTHON_VERSION found"
    return 0
}

check_node() {
    print_header "Checking Node.js"

    if ! command -v node &> /dev/null; then
        print_error "Node.js not found. Please install Node.js 20+"
        return 1
    fi

    NODE_VERSION=$(node --version)
    print_success "Node.js $NODE_VERSION found"
    return 0
}

check_docker() {
    print_header "Checking Docker"

    if ! command -v docker &> /dev/null; then
        print_error "Docker not found. Please install Docker."
        return 1
    fi

    print_success "Docker found"

    if ! command -v docker-compose &> /dev/null; then
        print_error "docker-compose not found. Please install Docker Compose."
        return 1
    fi

    print_success "docker-compose found"
    return 0
}

start_postgres() {
    print_header "Starting PostgreSQL"

    if docker-compose ps | grep -q "jobpilot-postgres"; then
        print_info "PostgreSQL already running"
        return 0
    fi

    docker-compose up -d postgres
    print_success "PostgreSQL started"

    # Wait for it to be ready
    print_info "Waiting for PostgreSQL to be ready..."
    for i in {1..30}; do
        if psql -U jobpilot -h localhost -d jobpilot -c "SELECT 1" &> /dev/null; then
            print_success "PostgreSQL is ready"
            return 0
        fi
        echo -n "."
        sleep 1
    done

    print_error "PostgreSQL failed to start"
    return 1
}

setup_backend() {
    print_header "Setting up Backend"

    cd backend

    if [ ! -d "venv" ]; then
        print_info "Creating virtual environment..."
        python3 -m venv venv
    fi

    source venv/bin/activate

    if ! pip show -q sqlalchemy &> /dev/null; then
        print_info "Installing Python dependencies..."
        pip install -q -r requirements.txt
    fi
    print_success "Python dependencies installed"

    # Run migrations
    print_info "Running database migrations..."
    alembic upgrade head
    print_success "Migrations complete"

    # Check if database needs seeding
    USER_COUNT=$(psql -U jobpilot -h localhost -d jobpilot -t -c "SELECT COUNT(*) FROM users" 2>/dev/null || echo "0")
    if [ "$USER_COUNT" -eq "0" ]; then
        print_info "Seeding test data..."
        python -m scripts.seed_manual_test_data
        print_success "Test data seeded"
    else
        print_info "Database already has data, skipping seed"
    fi

    cd ..
}

setup_frontend() {
    print_header "Setting up Frontend"

    cd frontend

    if [ ! -d "node_modules" ]; then
        print_info "Installing Node dependencies..."
        npm install -q
    fi
    print_success "Node dependencies installed"

    cd ..
}

usage() {
    cat << EOF

${GREEN}JobPilot Development Environment${NC}

Usage:
    $0 [COMMAND]

Commands:
    help         Show this help message
    check        Check prerequisites
    postgres     Start PostgreSQL only
    setup        Setup backend and frontend (without starting)
    backend      Start backend server
    frontend     Start frontend dev server
    all          Start everything (postgres, backend, frontend)

Examples:
    # First time: check everything and setup
    $0 check
    $0 setup

    # Start everything
    $0 all

    # Or start each component separately
    $0 postgres    # Terminal 1
    $0 backend     # Terminal 2
    $0 frontend    # Terminal 3

Test Credentials:
    Email: test.user@example.local
    Password: TestPassword123!

Frontend URL: http://localhost:3000
Backend URL: http://localhost:8000/api/v1

EOF
}

# Main script
if [ $# -eq 0 ]; then
    usage
    exit 0
fi

COMMAND=$1

case $COMMAND in
    help)
        usage
        ;;
    check)
        check_docker || exit 1
        check_python || exit 1
        check_node || exit 1
        check_postgres || exit 1
        print_success "All prerequisites met!"
        ;;
    postgres)
        check_docker || exit 1
        start_postgres || exit 1
        print_info "Press Ctrl+C to stop PostgreSQL"
        docker-compose logs -f postgres
        ;;
    setup)
        check_docker || exit 1
        check_python || exit 1
        check_node || exit 1
        check_postgres || exit 1
        start_postgres || exit 1
        setup_backend || exit 1
        setup_frontend || exit 1
        print_success "Setup complete!"
        echo ""
        print_info "Next steps:"
        echo "  1. Start PostgreSQL in a separate terminal: $0 postgres"
        echo "  2. Start backend in another terminal: $0 backend"
        echo "  3. Start frontend in another terminal: $0 frontend"
        echo "  Or start everything at once: $0 all"
        ;;
    backend)
        cd backend
        if [ ! -d "venv" ]; then
            print_error "Virtual environment not found. Run: $0 setup"
            exit 1
        fi
        source venv/bin/activate
        print_header "Starting Backend Server"
        print_info "Backend running at http://localhost:${BACKEND_PORT}/api/v1"
        print_info "Press Ctrl+C to stop"
        uvicorn app.main:app --reload --host 0.0.0.0 --port ${BACKEND_PORT}
        ;;
    frontend)
        cd frontend
        if [ ! -d "node_modules" ]; then
            print_error "node_modules not found. Run: $0 setup"
            exit 1
        fi
        print_header "Starting Frontend Dev Server"
        print_info "Frontend running at http://localhost:${FRONTEND_PORT}"
        print_info "Press Ctrl+C to stop"
        npm run dev
        ;;
    all)
        print_header "Starting Full Development Environment"
        print_info "Note: This will start processes in this terminal."
        print_info "For better control, run each in separate terminals:"
        echo "  Terminal 1: $0 postgres"
        echo "  Terminal 2: $0 backend"
        echo "  Terminal 3: $0 frontend"
        echo ""
        read -p "Continue starting all services? (y/N) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            check_docker || exit 1
            check_python || exit 1
            check_node || exit 1
            start_postgres || exit 1
            print_success "All services ready"
            print_info "Open http://localhost:3000 in your browser"
        fi
        ;;
    *)
        print_error "Unknown command: $COMMAND"
        usage
        exit 1
        ;;
esac
