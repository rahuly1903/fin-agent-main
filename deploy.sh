#!/bin/bash
# deploy.sh - Manual deployment script for EC2
# Usage: ./deploy.sh [command]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if .env file exists
check_env() {
    if [ ! -f .env ]; then
        log_error ".env file not found!"
        log_info "Copy .env.example to .env and fill in your values:"
        log_info "  cp .env.example .env"
        exit 1
    fi
}

# Pull latest code from git
pull() {
    log_info "Pulling latest code..."
    git pull origin main
}

# Build all containers
build() {
    log_info "Building Docker images..."
    docker compose build --no-cache
}

# Run database migrations
migrate() {
    check_env
    log_info "Running database migrations..."
    
    # Start db if not running
    docker compose up -d db
    
    # Wait for db to be healthy
    log_info "Waiting for database to be ready..."
    until docker compose exec -T db pg_isready -U ${POSTGRES_USER:-user} -d ${POSTGRES_DB:-fin_agent}; do
        sleep 2
    done
    
    # Run migrations
    docker compose run --rm migrate
    log_info "Migrations complete!"
}

# Start all services (except migrate which runs once)
up() {
    check_env
    log_info "Starting all services..."
    
    # Run migrations first
    migrate
    
    # Start application services
    docker compose up -d backend frontend nginx
    log_info "Services started! Access at http://localhost"
}

# Stop all services
down() {
    log_info "Stopping all services..."
    docker compose down
}

# View logs
logs() {
    docker compose logs -f --tail=100
}

# Restart all services
restart() {
    down
    up
}

# Full deploy: pull, build, restart
deploy() {
    log_info "Starting full deployment..."
    pull
    build
    up
    log_info "Deployment complete!"
}

# Health check
health() {
    log_info "Checking service health..."
    docker compose ps
    echo ""
    log_info "Backend health:"
    curl -s http://localhost/api/health 2>/dev/null || echo "Backend not responding"
    echo ""
}

# Show migration status
migrate_status() {
    check_env
    log_info "Migration status:"
    docker compose run --rm migrate version
}

# Rollback last migration
rollback() {
    check_env
    log_warn "Rolling back last migration..."
    docker compose run --rm migrate down 1
    log_info "Rollback complete!"
}

# Show usage
usage() {
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  deploy         - Full deploy (pull + build + migrate + up)"
    echo "  pull           - Pull latest code from git"
    echo "  build          - Build Docker images"
    echo "  up             - Start all services (runs migrations first)"
    echo "  down           - Stop all services"
    echo "  restart        - Restart all services"
    echo "  logs           - View container logs"
    echo "  health         - Check service health"
    echo ""
    echo "  migrate        - Run pending database migrations"
    echo "  migrate-status - Show current migration version"
    echo "  rollback       - Rollback last migration"
    echo ""
}

# Main
case "${1:-deploy}" in
    deploy)         deploy ;;
    pull)           pull ;;
    build)          build ;;
    up)             up ;;
    down)           down ;;
    restart)        restart ;;
    logs)           logs ;;
    health)         health ;;
    migrate)        migrate ;;
    migrate-status) migrate_status ;;
    rollback)       rollback ;;
    *)              usage ;;
esac
