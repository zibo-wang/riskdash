"""Database utilities for the SRE Automation Dashboard.

This module provides functions for managing the DuckDB database used by the SRE Automation Dashboard.
It handles incident logging, engineer management, and job link storage.

The database uses the following tables:
- incidents: Stores incident response data
- engineers: Stores L1 and L2 engineer information
- job_links: Stores links associated with jobs
"""

import datetime
import os
import threading

import duckdb

DB_FILE = "incidents.duckdb"
# Use a lock for database write operations to prevent race conditions
# from near-simultaneous requests in Streamlit's execution model.
db_lock = threading.Lock()


def get_db_connection():
    """Creates or connects to the DuckDB database.

    Returns:
        duckdb.DuckDBPyConnection: A connection to the DuckDB database.
    """
    conn = duckdb.connect(DB_FILE)
    return conn


def init_db():
    """Initializes the database schema if it doesn't exist.

    Creates the following tables if they don't exist:
    - incidents: For storing incident response data
    - engineers: For storing engineer information
    - job_links: For storing job-related links

    Also populates the engineers table with default values if empty.
    """
    with db_lock:
        conn = get_db_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id INTEGER PRIMARY KEY,
                    job_name VARCHAR NOT NULL,
                    status_at_incident VARCHAR NOT NULL,
                    detection_time TIMESTAMP NOT NULL,
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

            # Add job links table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_links (
                    incident_id INTEGER PRIMARY KEY,
                    url VARCHAR NOT NULL,
                    link_text VARCHAR,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    """Logs the start of an incident response.

    Args:
        job_name (str): Name of the job experiencing the incident.
        status (str): Status of the job at the time of incident.
        responder (str): Name of the engineer responding to the incident.
        priority (str): Priority level of the incident (P1-P4).

    Returns:
        int: The incident ID if successful, -1 if failed or if incident already exists.
    """
    start_time = datetime.datetime.now()
    detection_time = start_time  # For now, use the same time as start time
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
                    INSERT INTO incidents (incident_id, job_name, status_at_incident, detection_time, response_start_time, responder_name, priority, resolution_time, resolution_duration_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                    RETURNING incident_id;
                    """,
                    (
                        next_id,
                        job_name,
                        status,
                        detection_time,
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
    """Logs the resolution of an incident and calculates duration.

    Args:
        incident_id (int): The ID of the incident to resolve.

    Returns:
        bool: True if resolution was successful, False otherwise.
    """
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
    """Fetches details of currently active (unresolved) incidents.

    Returns:
        dict: A dictionary mapping job names to incident details. Each incident detail
              contains incident_id, responder, priority, and start_time.
    """
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
    """Fetches recent incident history (resolved and active).

    Args:
        limit (int, optional): Maximum number of incidents to return. Defaults to 50.

    Returns:
        pandas.DataFrame: DataFrame containing incident history with columns:
            - incident_id
            - job_name
            - status_at_incident
            - priority
            - responder_name
            - detection_time
            - response_start_time
            - resolution_time
            - resolution_duration_seconds
    """
    history_df = None
    conn = get_db_connection()
    try:
        history_df = conn.execute(
            f"""
             SELECT incident_id, job_name, status_at_incident, priority, responder_name,
                    detection_time, response_start_time, resolution_time, resolution_duration_seconds
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
    """Returns a dictionary with L1 and L2 engineers.

    Returns:
        dict: A dictionary with two keys:
            - 'L1': List of L1 engineer names
            - 'L2': List of L2 engineer names
    """
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
    """Adds a new engineer to the database.

    Args:
        name (str): Name of the engineer.
        level (str): Level of the engineer ('L1' or 'L2').

    Returns:
        bool: True if engineer was added successfully, False otherwise.
    """
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
    """Removes an engineer from the database.

    Args:
        name (str): Name of the engineer to remove.

    Returns:
        bool: True if engineer was removed successfully, False otherwise.
    """
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


def get_job_links():
    """Returns a dictionary of job links.

    Returns:
        dict: A dictionary mapping incident IDs to link details. Each link detail
              contains 'url' and 'text' keys.
    """
    links = {}
    conn = get_db_connection()
    try:
        results = conn.execute(
            "SELECT incident_id, url, link_text FROM job_links"
        ).fetchall()
        for incident_id, url, link_text in results:
            links[incident_id] = {"url": url, "text": link_text or url}
    except Exception as e:
        print(f"Error fetching job links: {e}")
    finally:
        conn.close()
    return links


def add_job_link(incident_id, url, link_text=None):
    """Adds or updates a link for an incident.

    Args:
        incident_id (int): ID of the incident to add/update the link for.
        url (str): URL of the link.
        link_text (str, optional): Display text for the link. If None, uses the URL.

    Returns:
        bool: True if link was added/updated successfully, False otherwise.
    """
    success = False
    with db_lock:
        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO job_links (incident_id, url, link_text)
                VALUES (?, ?, ?)
            """,
                (incident_id, url, link_text),
            )
            success = True
        except Exception as e:
            print(f"Error adding job link: {e}")
        finally:
            conn.close()
    return success


def remove_job_link(incident_id):
    """Removes a link for an incident.

    Args:
        incident_id (int): ID of the incident to remove the link for.

    Returns:
        bool: True if link was removed successfully, False otherwise.
    """
    success = False
    with db_lock:
        conn = get_db_connection()
        try:
            conn.execute(
                "DELETE FROM job_links WHERE incident_id = ?", (incident_id,)
            )
            success = True
        except Exception as e:
            print(f"Error removing job link: {e}")
        finally:
            conn.close()
    return success


# --- Ensure DB is initialized on module load ---
if not os.path.exists(DB_FILE):
    print(f"Database file {DB_FILE} not found, initializing...")
    init_db()
# --- Or ensure schema exists on every start ---
# init_db() # Call always to ensure table exists, safe due to IF NOT EXISTS
