#!/bin/bash

set -e

# Set default values
SERVICE_TYPE=${SERVICE_TYPE:-backend}
ENV_TYPE=${ENV_TYPE:-development}
DJANGO_SETTINGS_MODULE=${DJANGO_SETTINGS_MODULE:-tfc.settings.settings}
ENV_PROJECT_ROOT=${ENV_PROJECT_ROOT:-/app/backend}
# Fast startup mode - skip non-essential checks for faster local dev
FAST_STARTUP=${FAST_STARTUP:-false}

# Disable bytecode compilation to speed up imports (optional)
# export PYTHONDONTWRITEBYTECODE=1

# Configurable settings
# Granian settings
# Default to 1 worker for development, scale up via GRANIAN_WORKERS for production
GRANIAN_WORKERS=${GRANIAN_WORKERS:-1}
# Enable/disable gRPC server (useful to disable for local development to save memory)
ENABLE_GRPC=${ENABLE_GRPC:-true}
# Enable/disable HTTP server (useful for running gRPC-only microservice)
ENABLE_HTTP=${ENABLE_HTTP:-true}
GRANIAN_THREADS=${GRANIAN_THREADS:-2}
DB_WAIT_TIMEOUT=${DB_WAIT_TIMEOUT:-60}
CELERY_QUEUE=${CELERY_QUEUE:-celery}

# Function to get concurrency settings for a queue
get_queue_concurrency() {
    local queue=$1
    local var_name=""
    local default_value=""

    case "$queue" in
        "tasks_xl")
            var_name="CELERY_TASKS_XL_CONCURRENCY"
            default_value="8"
            ;;
        "tasks_l")
            var_name="CELERY_TASKS_L_CONCURRENCY"
            default_value="8"
            ;;
        "tasks_s")
            var_name="CELERY_TASKS_S_CONCURRENCY"
            default_value="16"
            ;;
        "trace_ingestion")
            var_name="CELERY_TRACE_INGESTION_CONCURRENCY"
            default_value="16"
            ;;
        "agent_compass")
            var_name="CELERY_AGENT_COMPASS_CONCURRENCY"
            default_value="8"
            ;;
        *) # Default (includes 'celery' queue)
            var_name="CELERY_DEFAULT_CONCURRENCY"
            default_value="16"
            ;;
    esac

    # Use environment variable if set, otherwise use default
    echo "${!var_name:-$default_value}"
}

# Function to get max tasks for a queue
get_queue_max_tasks() {
    local queue=$1
    local var_name=""
    local default_value=""

    case "$queue" in
        "tasks_xl")
            var_name="CELERY_TASKS_XL_MAX_TASKS"
            default_value="100"
            ;;
        "tasks_l")
            var_name="CELERY_TASKS_L_MAX_TASKS"
            default_value="100"
            ;;
        "tasks_s")
            var_name="CELERY_TASKS_S_MAX_TASKS"
            default_value="1000"
            ;;
        "trace_ingestion")
            var_name="CELERY_TRACE_INGESTION_MAX_TASKS"
            default_value="1000"
            ;;
        "agent_compass")
            var_name="CELERY_AGENT_COMPASS_MAX_TASKS"
            default_value="100"
            ;;
        *) # Default (includes 'celery' queue)
            var_name="CELERY_DEFAULT_MAX_TASKS"
            default_value="1000"
            ;;
    esac

    # Use environment variable if set, otherwise use default
    echo "${!var_name:-$default_value}"
}

# Legacy single queue support - set defaults for backwards compatibility
C_CONCURRENCY=$(get_queue_concurrency "$CELERY_QUEUE")
C_MAX_TASKS=$(get_queue_max_tasks "$CELERY_QUEUE")
WORKER_CONCURRENCY=${WORKER_CONCURRENCY:-$C_CONCURRENCY}
MAX_TASKS_PER_CHILD=${MAX_TASKS_PER_CHILD:-$C_MAX_TASKS}

# Array to store background process PIDs
declare -a WORKER_PIDS=()

# Export environment variables
export DJANGO_SETTINGS_MODULE
export ENV_PROJECT_ROOT

# Change to the backend directory
cd /app/backend

# Install any missing dependencies (2FA/WebAuthn added after Docker image build)
pip install --quiet "pyotp>=2.9.0" "qrcode[pil]>=7.4" "webauthn>=2.2.0" 2>/dev/null || true

# Create logs directory if it doesn't exist
mkdir -p logs media static

# Signal handling for graceful shutdown
trap 'echo "Received shutdown signal, exiting gracefully..."; exit 0' TERM INT

# Function to wait for database with retry logic
wait_for_db() {
    echo "Waiting for database connection..."
    local timeout=$DB_WAIT_TIMEOUT
    local count=0

    while [ $count -lt $timeout ]; do
        if python manage.py check --database default >/dev/null 2>&1; then
            echo "Database is ready!"
            return 0
        fi
        echo "Database not ready, waiting... ($count/$timeout)"
        sleep 2
        count=$((count + 2))
    done

    echo "ERROR: Database connection timeout after ${timeout}s"
    exit 1
}

# Function to run migrations with error handling
run_migrations() {
    echo "Running database migrations..."
    if ! python manage.py migrate --noinput; then
        echo "ERROR: Database migrations failed"
        exit 1
    fi
    echo "Migrations completed successfully"
}

# Function to create cache table if it doesn't exist
create_cache_table() {
    echo "Creating cache table if it doesn't exist..."
    if ! python manage.py createcachetable --database default; then
        echo "WARNING: Cache table creation failed, but continuing..."
    else
        echo "Cache table created/verified successfully"
    fi
}

# Function to collect static files with error handling
collect_static() {
    echo "Collecting static files..."
    if ! python manage.py collectstatic --noinput --clear; then
        echo "WARNING: Static file collection failed, continuing..."
    else
        echo "Static files collected successfully"
    fi
}

# Function to validate Django settings
validate_django() {
    echo "Validating Django configuration..."
    if ! python manage.py check --deploy 2>/dev/null; then
        echo "WARNING: Django deployment checks failed, but continuing..."
        python manage.py check
    fi
    echo "Django configuration validated"
}

# Function to print service info
print_service_info() {
    echo "============================================"
    echo "FutureAGI Backend Service Starting"
    echo "Service Type: $SERVICE_TYPE"
    echo "Environment: $ENV_TYPE"
    echo "Django Settings: $DJANGO_SETTINGS_MODULE"
    echo "Project Root: $ENV_PROJECT_ROOT"
    if [ "$SERVICE_TYPE" = "worker" ]; then
        echo "Celery Queue(s): $CELERY_QUEUE"
        # Count number of queues
        IFS=',' read -ra QUEUE_COUNT <<< "$CELERY_QUEUE"
        echo "Number of Workers: ${#QUEUE_COUNT[@]}"
        if [ "${#QUEUE_COUNT[@]}" -eq 1 ]; then
            echo "Worker Concurrency: $WORKER_CONCURRENCY"
            echo "Max Tasks Per Child: $MAX_TASKS_PER_CHILD"
        fi
    elif [ "$SERVICE_TYPE" = "backend" ]; then
        if [ "$ENABLE_HTTP" = "true" ]; then
            echo "HTTP Server: Enabled (Granian ASGI, Port 80)"
            echo "Granian Workers: $GRANIAN_WORKERS"
            echo "Granian Threads: $GRANIAN_THREADS"
        else
            echo "HTTP Server: Disabled (set ENABLE_HTTP=true to enable)"
        fi
        if [ "$ENABLE_GRPC" = "true" ]; then
            echo "gRPC Server: Enabled (Port 50051)"
        else
            echo "gRPC Server: Disabled (set ENABLE_GRPC=true to enable)"
        fi
    fi
    echo "============================================"
}

# Initialize
print_service_info

# Run database checks for services that need it (skip in FAST_STARTUP mode)
if [ "$FAST_STARTUP" != "true" ]; then
    case "$SERVICE_TYPE" in
        "backend"|"worker"|"beat"|"grpc")
            wait_for_db
            ;;
    esac

    # Create cache table for services that need it
    case "$SERVICE_TYPE" in
        "backend"|"worker")
            create_cache_table
            ;;
    esac

    # Run backend-specific setup
    if [ "$SERVICE_TYPE" = "backend" ]; then
        run_migrations
        collect_static
        if [ "$ENV_TYPE" = "prod" ] || [ "$ENV_TYPE" = "staging" ]; then
            validate_django
        fi
    fi
else
    echo "FAST_STARTUP mode: skipping DB checks, migrations, and static collection"
fi

REGISTER_TEMPORAL_SCHEDULES=${REGISTER_TEMPORAL_SCHEDULES:-true}
TEMPORAL_SCHEDULE_REGISTRATION_TIMEOUT_SECONDS=${TEMPORAL_SCHEDULE_REGISTRATION_TIMEOUT_SECONDS:-45}

if [ "$REGISTER_TEMPORAL_SCHEDULES" = "true" ]; then
    if command -v timeout >/dev/null 2>&1; then
        timeout "${TEMPORAL_SCHEDULE_REGISTRATION_TIMEOUT_SECONDS}s" python manage.py register_temporal_schedules || echo "WARNING: Temporal schedule registration failed or timed out (non-fatal), continuing startup..."
    else
        python manage.py register_temporal_schedules || echo "WARNING: Temporal schedule registration failed (non-fatal), continuing startup..."
    fi
else
    echo "Skipping Temporal schedule registration because REGISTER_TEMPORAL_SCHEDULES is not true"
fi

# Start the appropriate service based on SERVICE_TYPE
case "$SERVICE_TYPE" in
    "backend")
        echo "Starting backend server..."

        # Enhanced signal handling to cleanup gRPC process
        cleanup() {
            echo "Shutting down services..."
            if [ ! -z "$GRPC_PID" ]; then
                echo "Stopping gRPC server (PID: $GRPC_PID)..."
                kill -TERM $GRPC_PID 2>/dev/null || true
                wait $GRPC_PID 2>/dev/null || true
            fi
            exit 0
        }
        trap cleanup TERM INT

        # Start gRPC server in background if enabled
        if [ "$ENABLE_GRPC" = "true" ]; then
            echo "Starting gRPC server in background..."
            if [ "$ENV_TYPE" = "prod" ] || [ "$ENV_TYPE" = "staging" ]; then
                python manage.py grpcrunaioserver &
            else
                python manage.py grpcrunaioserver --dev &
            fi

            # Store gRPC PID for cleanup
            GRPC_PID=$!
            echo "gRPC server started with PID: $GRPC_PID"
        else
            echo "gRPC server disabled (ENABLE_GRPC=false)"
        fi

        # Start HTTP server with Granian
        if [ "$ENABLE_HTTP" = "true" ]; then
            if [ "$ENV_TYPE" = "prod" ] || [ "$ENV_TYPE" = "staging" ]; then
                echo "Starting production backend with Granian..."
                granian --interface asgi tfc.asgi:application \
                    --workers $GRANIAN_WORKERS \
                    --runtime-threads $GRANIAN_THREADS \
                    --runtime-mode mt \
                    --loop uvloop \
                    --host 0.0.0.0 \
                    --port 80 \
                    --log-level warning \
                    --access-log \
                    --respawn-failed-workers
            else
                echo "Starting development backend server with Granian..."
                # Use Granian's native --reload with ignore patterns for logs/media/static
                # Note: --reload-ignore-patterns takes regex, not glob
                granian --interface asgi tfc.asgi:application \
                    --workers $GRANIAN_WORKERS \
                    --host 0.0.0.0 \
                    --port 80 \
                    --log-level info \
                    --reload \
                    --respawn-failed-workers \
                    --reload-ignore-dirs "logs" \
                    --reload-ignore-dirs "media" \
                    --reload-ignore-dirs "static" \
                    --reload-ignore-dirs "__pycache__" \
                    --reload-ignore-dirs "minio_data" \
                    --reload-ignore-patterns '^\..*' \
                    --reload-ignore-patterns '.*\.log$' \
                    --reload-ignore-patterns '.*\.pyc$'
            fi
        else
            echo "HTTP server disabled (ENABLE_HTTP=false)"
            # If gRPC is running, wait for it; otherwise nothing to do
            if [ "$ENABLE_GRPC" = "true" ] && [ ! -z "$GRPC_PID" ]; then
                echo "Running in gRPC-only mode, waiting for gRPC server..."
                wait $GRPC_PID
            else
                echo "ERROR: Both ENABLE_HTTP and ENABLE_GRPC are disabled. Nothing to run."
                exit 1
            fi
        fi
        ;;

    "worker")
        echo "Starting Celery worker for queue(s): $CELERY_QUEUE..."

        # Parse comma-separated queue list
        IFS=',' read -ra QUEUE_ARRAY <<< "$CELERY_QUEUE"

        # Signal handler to cleanup all worker processes
        cleanup_workers() {
            echo "Shutting down all Celery workers..."
            for pid in "${WORKER_PIDS[@]}"; do
                if [ ! -z "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                    echo "Stopping worker with PID: $pid"
                    kill -TERM "$pid" 2>/dev/null || true
                fi
            done
            # Wait for all workers to finish
            for pid in "${WORKER_PIDS[@]}"; do
                wait "$pid" 2>/dev/null || true
            done
            exit 0
        }
        trap cleanup_workers TERM INT

        # Start a worker for each queue
        for queue in "${QUEUE_ARRAY[@]}"; do
            # Trim whitespace
            queue=$(echo "$queue" | xargs)

            # Get queue-specific settings
            queue_concurrency=$(get_queue_concurrency "$queue")
            queue_max_tasks=$(get_queue_max_tasks "$queue")

            # Override with environment variables if set
            final_concurrency=${WORKER_CONCURRENCY:-$queue_concurrency}
            final_max_tasks=${MAX_TASKS_PER_CHILD:-$queue_max_tasks}

            echo "Starting worker for queue: $queue (concurrency=$final_concurrency, max_tasks=$final_max_tasks)"

            if [ "$ENV_TYPE" = "prod" ] || [ "$ENV_TYPE" = "staging" ]; then
                celery -A tfc worker \
                    -Ofair \
                    -n worker-${queue}@%h \
                    --without-gossip \
                    --without-mingle \
                    --without-heartbeat \
                    --loglevel=info \
                    --concurrency=$final_concurrency \
                    --max-memory-per-child=512000 \
                    -Q "$queue" \
                    --max-tasks-per-child=$final_max_tasks \
                    -P prefork \
                    --without-heartbeat \
                    --without-mingle \
                    --without-gossip \
                    -Ofair \
                    --logfile=logs/celery-worker-${queue}.log &
            else
                # Development: use watchfiles (Rust-based, works better in Docker)
                watchfiles --filter python \
                    "celery -A tfc worker -Ofair -n worker-${queue}@%h --without-gossip --without-mingle --without-heartbeat --loglevel=INFO --concurrency=$final_concurrency -P prefork --max-memory-per-child=2048000 -Q $queue --max-tasks-per-child=$final_max_tasks" \
                    ./tfc ./accounts ./analytics ./model_hub ./sockets ./tracer ./usage ./utils ./simulate ./agent_playground &
            fi

            WORKER_PIDS+=($!)
            echo "Worker started for queue $queue with PID: ${WORKER_PIDS[-1]}"
        done

        echo "All ${#WORKER_PIDS[@]} worker(s) started. PIDs: ${WORKER_PIDS[*]}"

        # Wait for all background workers
        for pid in "${WORKER_PIDS[@]}"; do
            wait "$pid"
        done
        ;;

    "beat")
        echo "Starting Celery beat scheduler..."
        if [ "$ENV_TYPE" = "prod" ] || [ "$ENV_TYPE" = "staging" ]; then
            echo "Starting production Celery beat scheduler..."
            celery -A tfc beat \
                --loglevel=info \
                --pidfile=logs/celerybeat.pid \
                --schedule=logs/celerybeat-schedule
        else
            echo "Starting development Celery beat scheduler..."
            # Development: use watchfiles (Rust-based, works better in Docker)
            watchfiles --filter python \
                "celery -A tfc beat --loglevel=INFO" \
                ./tfc ./accounts ./analytics ./model_hub ./sockets ./tracer ./usage ./utils ./simulate ./agent_playground
        fi
        ;;

    "flower")
        echo "Starting Flower monitoring interface..."
        if [ "$ENV_TYPE" = "prod" ] || [ "$ENV_TYPE" = "staging" ]; then
            echo "Starting production Flower monitoring interface..."
            celery -A tfc flower \
                --port=5555 \
                --address=0.0.0.0 \
                --loglevel=info \
                --persistent=true \
                --db=logs/flower.db \
                --max_tasks=10000
        else
            echo "Starting development Flower monitoring interface..."
            # Development: use watchfiles (Rust-based, works better in Docker)
            watchfiles --filter python \
                "celery -A tfc flower --loglevel=INFO" \
                ./tfc ./accounts ./analytics ./model_hub ./sockets ./tracer ./usage ./utils ./simulate ./agent_playground
        fi
        ;;

    "grpc")
        echo "Starting standalone gRPC server..."
        echo "NOTE: gRPC is now integrated into the 'backend' service by default."
        echo "Use this standalone mode only if you need gRPC without the HTTP server."
        if [ "$ENV_TYPE" = "prod" ] || [ "$ENV_TYPE" = "staging" ]; then
            python manage.py grpcrunaioserver
        else
            python manage.py grpcrunaioserver --dev
        fi
        ;;

    "temporal-worker")
        echo "Starting Temporal worker..."
        TEMPORAL_TASK_QUEUE=${TEMPORAL_TASK_QUEUE:-default}
        TEMPORAL_ALL_QUEUES=${TEMPORAL_ALL_QUEUES:-false}

        # Check for --all-queues mode (for local development)
        ALL_QUEUES_ARG=""
        if [ "$TEMPORAL_ALL_QUEUES" = "true" ] || [ "$TEMPORAL_ALL_QUEUES" = "1" ]; then
            echo "Mode: ALL-QUEUES (single worker polling all queues)"
            ALL_QUEUES_ARG="--all-queues"
        else
            echo "Task Queue: $TEMPORAL_TASK_QUEUE"
        fi

        echo "Temporal Host: ${TEMPORAL_HOST:-localhost:7233}"
        echo "Temporal Namespace: ${TEMPORAL_NAMESPACE:-default}"
        echo "Max Concurrent Activities: ${TEMPORAL_MAX_CONCURRENT_ACTIVITIES:-100}"
        echo "Max Concurrent Workflow Tasks: ${TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS:-100}"

        # Resource-based tuning is opt-in (set TEMPORAL_TARGET_MEMORY_USAGE to enable)
        RESOURCE_TUNING_ARGS=""
        if [ -n "$TEMPORAL_TARGET_MEMORY_USAGE" ]; then
            TEMPORAL_TARGET_CPU_USAGE=${TEMPORAL_TARGET_CPU_USAGE:-1.0}
            echo "Resource-based tuning: ENABLED"
            echo "  Target Memory Usage: ${TEMPORAL_TARGET_MEMORY_USAGE}"
            echo "  Target CPU Usage: ${TEMPORAL_TARGET_CPU_USAGE}"
            RESOURCE_TUNING_ARGS="--target-memory-usage $TEMPORAL_TARGET_MEMORY_USAGE --target-cpu-usage $TEMPORAL_TARGET_CPU_USAGE"
        else
            echo "Resource-based tuning: DISABLED (using fixed concurrency)"
        fi

        if [ "$ENV_TYPE" = "prod" ] || [ "$ENV_TYPE" = "staging" ]; then
            # Production: run worker with graceful shutdown handling
            ./bin/temporal-worker --task-queue "$TEMPORAL_TASK_QUEUE" $RESOURCE_TUNING_ARGS
        else
            # Build list of watch paths, skipping any that don't exist.
            # ``watchfiles`` exits immediately with a non-zero code if any
            # watch target is missing (e.g. ``./utils`` is absent in some
            # checkouts), which makes the container crash-loop under
            # ``restart: unless-stopped`` and the Temporal worker never
            # stays alive long enough to poll activities.
            WATCH_PATHS=()
            for d in tfc accounts analytics model_hub sockets tracer usage utils simulate agent_playground; do
                [ -d "./$d" ] && WATCH_PATHS+=("./$d")
            done
            watchfiles --filter python \
                "python manage.py start_temporal_worker --task-queue $TEMPORAL_TASK_QUEUE $ALL_QUEUES_ARG $RESOURCE_TUNING_ARGS" \
                "${WATCH_PATHS[@]}"
        fi
        ;;

    *)
        echo "ERROR: Unknown SERVICE_TYPE: $SERVICE_TYPE"
        echo "Available options: backend, worker, beat, flower, grpc, temporal-worker"
        echo "Current environment:"
        echo "  SERVICE_TYPE=$SERVICE_TYPE"
        echo "  ENV_TYPE=$ENV_TYPE"
        exit 1
        ;;
esac
