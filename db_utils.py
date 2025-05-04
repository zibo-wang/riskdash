# db_utils.py
import datetime
import os
import threading

import duckdb

DB_FILE = "incidents.duckdb"
# Use a lock for database write operations to prevent race conditions
# from near-simultaneous requests in Streamlit's execution model.
db_lock = threading.Lock()


def get_db_connection():
    """Creates or connects to the DuckDB database."""
    conn = duckdb.connect(DB_FILE)
    return conn


def init_db():
    """Initializes the database schema if it doesn't exist."""
    with db_lock:
        conn = get_db_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id INTEGER PRIMARY KEY,
                    job_name VARCHAR NOT NULL,
                    status_at_incident VARCHAR NOT NULL,
                    response_start_time TIMESTAMP NOT NULL,
                    responder_name VARCHAR,
                    priority VARCHAR,
                    resolution_time TIMESTAMP,
                    resolution_duration_seconds BIGINT
                );
            """)

            # Add engineers table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS engineers (
                    name VARCHAR PRIMARY KEY,
                    level VARCHAR NOT NULL CHECK (level IN ('L1', 'L2'))
                );
            """)

            # Insert default engineers if table is empty
            result = conn.execute("SELECT COUNT(*) FROM engineers").fetchone()[
                0
            ]
            if result == 0:
                default_engineers = [
                    ("Alice", "L1"),
                    ("Bob", "L1"),
                    ("Charlie", "L1"),
                    ("David", "L2"),
                    ("Eve", "L2"),
                ]
                conn.executemany(
                    "INSERT INTO engineers (name, level) VALUES (?, ?)",
                    default_engineers,
                )

            print("Database initialized.")
        except Exception as e:
            print(f"Error initializing database: {e}")
        finally:
            conn.close()


def log_incident_start(job_name, status, responder, priority):
    """Logs the start of an incident response."""
    start_time = datetime.datetime.now()
    incident_id = -1
    with db_lock:
        conn = get_db_connection()
        try:
            # Check if there's already an *active* incident for this job
            existing = conn.execute(
                "SELECT incident_id FROM incidents WHERE job_name = ? AND resolution_time IS NULL",
                [job_name],
            ).fetchone()

            if existing:
                print(
                    f"Warning: Active incident already exists for {job_name}. Not creating a new one."
                )
                incident_id = existing[0]  # Return existing ID
            else:
                # Get the next incident_id
                max_id = conn.execute(
                    "SELECT COALESCE(MAX(incident_id), 0) FROM incidents"
                ).fetchone()[0]
                next_id = max_id + 1

                cursor = conn.execute(
                    """
                    INSERT INTO incidents (incident_id, job_name, status_at_incident, response_start_time, responder_name, priority, resolution_time, resolution_duration_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
                    RETURNING incident_id;
                    """,
                    (
                        next_id,
                        job_name,
                        status,
                        start_time,
                        responder,
                        priority,
                    ),
                )
                incident_id = cursor.fetchone()[0]
                print(
                    f"Logged incident start for {job_name}, ID: {incident_id}"
                )

                # --- Inform the API Mock (REMOVE THIS IN PRODUCTION) ---
                try:
                    import requests

                    requests.post(
                        f"http://127.0.0.1:5001/api/mock/start_response/{job_name}/{incident_id}"
                    )
                except Exception as api_err:
                    print(f"Could not inform mock API about start: {api_err}")
                # --- End Mock Inform ---

        except Exception as e:
            print(f"Error logging incident start: {e}")
        finally:
            conn.close()
    return incident_id


def log_incident_resolve(incident_id):
    """Logs the resolution of an incident and calculates duration."""
    end_time = datetime.datetime.now()
    resolved = False
    job_name_resolved = None  # To inform mock API
    with db_lock:
        conn = get_db_connection()
        try:
            # Fetch start time to calculate duration
            start_time_result = conn.execute(
                "SELECT response_start_time, job_name FROM incidents WHERE incident_id = ? AND resolution_time IS NULL",
                [incident_id],
            ).fetchone()

            if start_time_result:
                start_time, job_name_resolved = start_time_result
                duration = (end_time - start_time).total_seconds()
                conn.execute(
                    """
                    UPDATE incidents
                    SET resolution_time = ?, resolution_duration_seconds = ?
                    WHERE incident_id = ?
                    """,
                    (end_time, int(duration), incident_id),
                )
                resolved = True
                print(
                    f"Logged incident resolution for ID: {incident_id}, Duration: {duration:.0f}s"
                )

                # --- Inform the API Mock (REMOVE THIS IN PRODUCTION) ---
                if job_name_resolved:
                    try:
                        import requests

                        requests.post(
                            f"http://127.0.0.1:5001/api/mock/resolve_response/{job_name_resolved}/{incident_id}"
                        )
                    except Exception as api_err:
                        print(
                            f"Could not inform mock API about resolve: {api_err}"
                        )
                # --- End Mock Inform ---
            else:
                print(
                    f"Warning: Incident ID {incident_id} not found or already resolved. Cannot log resolution."
                )

        except Exception as e:
            print(f"Error logging incident resolve: {e}")
        finally:
            conn.close()
    return resolved


def get_active_incidents():
    """Fetches details of currently active (unresolved) incidents."""
    active = {}
    conn = get_db_connection()
    try:
        # Use read_committed isolation level if needed, default should be okay for reads
        results = conn.execute(
            """
            SELECT incident_id, job_name, responder_name, priority, response_start_time
            FROM incidents
            WHERE resolution_time IS NULL
            ORDER BY response_start_time DESC
            """
        ).fetchall()
        # Create a dictionary mapping job_name to incident details for easy lookup
        active = {
            row[1]: {
                "incident_id": row[0],
                "responder": row[2],
                "priority": row[3],
                "start_time": row[4],
            }
            for row in results
        }
    except Exception as e:
        print(f"Error fetching active incidents: {e}")
    finally:
        conn.close()
    return active


def get_incident_history(limit=50):
    """Fetches recent incident history (resolved and active)."""
    history_df = None
    conn = get_db_connection()
    try:
        history_df = conn.execute(
            f"""
             SELECT incident_id, job_name, status_at_incident, priority, responder_name,
                    response_start_time, resolution_time, resolution_duration_seconds
             FROM incidents
             ORDER BY response_start_time DESC
             LIMIT {limit}
             """
        ).fetchdf()  # Fetch directly as Pandas DataFrame
    except Exception as e:
        print(f"Error fetching incident history: {e}")
    finally:
        conn.close()
    return history_df


def get_engineers():
    """Returns a dictionary with L1 and L2 engineers."""
    engineers = {"L1": [], "L2": []}
    conn = get_db_connection()
    try:
        results = conn.execute(
            "SELECT name, level FROM engineers ORDER BY level, name"
        ).fetchall()
        for name, level in results:
            engineers[level].append(name)
    except Exception as e:
        print(f"Error fetching engineers: {e}")
    finally:
        conn.close()
    return engineers


def add_engineer(name, level):
    """Adds a new engineer to the database."""
    success = False
    with db_lock:
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO engineers (name, level) VALUES (?, ?)",
                (name, level),
            )
            success = True
        except Exception as e:
            print(f"Error adding engineer: {e}")
        finally:
            conn.close()
    return success


def remove_engineer(name):
    """Removes an engineer from the database."""
    success = False
    with db_lock:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM engineers WHERE name = ?", (name,))
            success = True
        except Exception as e:
            print(f"Error removing engineer: {e}")
        finally:
            conn.close()
    return success


# --- Ensure DB is initialized on module load ---
if not os.path.exists(DB_FILE):
    print(f"Database file {DB_FILE} not found, initializing...")
    init_db()
# --- Or ensure schema exists on every start ---
# init_db() # Call always to ensure table exists, safe due to IF NOT EXISTS
