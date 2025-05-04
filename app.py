# app.py
import time
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from db_utils import (
    add_engineer,
    get_active_incidents,
    get_engineers,
    get_incident_history,
    init_db,
    log_incident_resolve,
    log_incident_start,
    remove_engineer,
)

# --- Configuration ---
API_ENDPOINT = (
    "http://127.0.0.1:5001/api/job_status"  # Change to your real API endpoint
)
REFRESH_INTERVAL_SECONDS = 60  # How often to fetch API status

# Status levels for ranking
STATUS_ORDER = {"Critical": 0, "Error": 1, "Warning": 2, "Log": 3}
STATUS_EMOJI = {"Critical": "🔥", "Error": "❌", "Warning": "⚠️", "Log": "📄"}
PRIORITY_LEVELS = ["P1", "P2", "P3", "P4"]

# --- Initialize Database ---
# Ensure the table exists when the app starts
init_db()

# --- State Management ---
# Use Streamlit's session state to manage temporary UI states like form visibility
if "show_respond_form" not in st.session_state:
    st.session_state.show_respond_form = {}  # Dict: {job_name: boolean}
if "selected_priority" not in st.session_state:
    st.session_state.selected_priority = {}  # Dict: {job_name: priority}
if "selected_assignee" not in st.session_state:
    st.session_state.selected_assignee = {}  # Dict: {job_name: assignee}
if "show_engineer_form" not in st.session_state:
    st.session_state.show_engineer_form = False

# --- Helper Functions ---


@st.cache_data(ttl=REFRESH_INTERVAL_SECONDS)  # Cache API data for interval
def fetch_job_status(api_url):
    """Fetches job status list from the API."""
    try:
        response = requests.get(api_url, timeout=10)  # Add timeout
        response.raise_for_status()  # Raise exception for bad status codes (4xx or 5xx)
        jobs = response.json()
        # Basic validation
        if not isinstance(jobs, list):
            st.error(f"API Error: Expected a list of jobs, got {type(jobs)}")
            return None
        for job in jobs:
            if (
                not isinstance(job, dict)
                or "name" not in job
                or "status" not in job
            ):
                st.error(f"API Error: Invalid job format found: {job}")
                return None  # Or filter out invalid ones
        return jobs
    except requests.exceptions.RequestException as e:
        st.error(f"API Error: Could not fetch job status: {e}")
        return None
    except Exception as e:
        st.error(f"An unexpected error occurred during API fetch: {e}")
        return None


def rank_jobs(jobs):
    """Sorts jobs by critical level."""
    if jobs is None:
        return []
    return sorted(
        jobs, key=lambda x: STATUS_ORDER.get(x.get("status", "Log"), 99)
    )


def display_time_ago(dt_object):
    """Displays a datetime object as 'time ago'."""
    if not dt_object:
        return "N/A"
    now = datetime.now(dt_object.tzinfo)  # Ensure timezone awareness if needed
    diff = now - dt_object
    seconds = diff.total_seconds()
    if seconds < 60:
        return f"{int(seconds)}s ago"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    elif seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    else:
        return f"{int(seconds // 86400)}d ago"


# --- Streamlit App Layout ---

st.set_page_config(layout="wide", page_title="SRE Automation Dashboard")

st.title(" SRE Automation Job Status Dashboard")

# Display L1/L2 Engineers
st.sidebar.markdown("---")
st.sidebar.subheader("On-Call Engineers")

# Get current engineers from database
engineers = get_engineers()
L1_ENGINEERS = engineers["L1"]
L2_ENGINEERS = engineers["L2"]
ALL_ENGINEERS = sorted(L1_ENGINEERS + L2_ENGINEERS)

# Display current engineers
st.sidebar.markdown("**L1 Support:**")
for name in L1_ENGINEERS:
    col1, col2 = st.sidebar.columns([3, 1])
    col1.markdown(f"- {name}")
    if col2.button("❌", key=f"remove_{name}"):
        if remove_engineer(name):
            st.rerun()

st.sidebar.markdown("**L2 Support:**")
for name in L2_ENGINEERS:
    col1, col2 = st.sidebar.columns([3, 1])
    col1.markdown(f"- {name}")
    if col2.button("❌", key=f"remove_{name}"):
        if remove_engineer(name):
            st.rerun()

# Add new engineer form
if st.sidebar.button("➕ Add Engineer"):
    st.session_state.show_engineer_form = True

if st.session_state.show_engineer_form:
    with st.sidebar.form("add_engineer_form"):
        st.markdown("**Add New Engineer**")
        new_name = st.text_input("Name")
        new_level = st.radio("Level", ["L1", "L2"], horizontal=True)
        submitted = st.form_submit_button("Add")
        if submitted:
            if new_name and new_name not in ALL_ENGINEERS:
                if add_engineer(new_name, new_level):
                    st.session_state.show_engineer_form = False
                    st.rerun()
                else:
                    st.error("Failed to add engineer. Please try again.")
            else:
                st.error("Please enter a valid, unique name.")
        if st.form_submit_button("Cancel"):
            st.session_state.show_engineer_form = False
            st.rerun()

st.sidebar.markdown("---")

# Fetch and display data
api_jobs_raw = fetch_job_status(API_ENDPOINT)
ranked_jobs = rank_jobs(api_jobs_raw)
active_incidents = (
    get_active_incidents()
)  # Get jobs currently being responded to

st.subheader("Job Status Overview")

if not ranked_jobs:
    st.warning(
        "No job data available. Check API connection or wait for refresh."
    )
else:
    # Create columns for layout
    col1, col2, col3, col4 = st.columns([3, 1, 3, 2])  # Adjust widths as needed
    col1.markdown("**Job Name**")
    col2.markdown("**Status**")
    col3.markdown("**Action / Incident Details**")
    col4.markdown("**Response Time**")

    st.markdown("---")  # Separator

    for job in ranked_jobs:
        job_name = job.get("name", "Unknown Job")
        status = job.get("status", "Unknown")
        status_icon = STATUS_EMOJI.get(status, "❓")

        col1, col2, col3, col4 = st.columns(
            [3, 1, 3, 2]
        )  # Columns for each job row
        col1.markdown(f"**{job_name}**")
        col2.markdown(f"{status_icon} {status}")

        # --- Incident Handling Logic ---
        is_critical_or_error = status in ["Critical", "Error"]
        active_incident_info = active_incidents.get(job_name)

        with col3:  # Action / Incident Details column
            if active_incident_info:
                # Job is currently being responded to
                incident_id = active_incident_info["incident_id"]
                responder = active_incident_info["responder"]
                priority = active_incident_info["priority"]
                start_time = active_incident_info["start_time"]
                st.info(f"Responding: {responder} ({priority})")

                resolve_key = f"resolve_{job_name}_{incident_id}"
                if st.button(
                    "Resolve Incident", key=resolve_key, type="primary"
                ):
                    resolved = log_incident_resolve(incident_id)
                    if resolved:
                        st.success(
                            f"Incident {incident_id} for {job_name} marked as resolved."
                        )
                        # Clear any lingering form state for this job
                        st.session_state.show_respond_form.pop(job_name, None)
                        st.session_state.selected_priority.pop(job_name, None)
                        st.session_state.selected_assignee.pop(job_name, None)
                        st.rerun()  # Force immediate refresh
                    else:
                        st.error(
                            "Failed to resolve incident (already resolved or DB error)."
                        )
                        st.rerun()  # Refresh anyway to potentially clear state

            elif is_critical_or_error:
                # Job needs response, show button or form
                respond_key = f"respond_{job_name}"
                form_key = f"form_{job_name}"

                if st.session_state.show_respond_form.get(job_name, False):
                    # Display the response form
                    with st.form(key=form_key):
                        st.markdown("**Respond to Incident**")
                        priority = st.radio(
                            "Priority:",
                            PRIORITY_LEVELS,
                            key=f"priority_{job_name}",
                            horizontal=True,
                            index=PRIORITY_LEVELS.index(
                                st.session_state.selected_priority.get(
                                    job_name, "P3"
                                )
                            ),  # Default P3
                        )
                        assignee = st.selectbox(
                            "Assign To:",
                            ALL_ENGINEERS,
                            key=f"assignee_{job_name}",
                            index=ALL_ENGINEERS.index(
                                st.session_state.selected_assignee.get(
                                    job_name, ALL_ENGINEERS[0]
                                )
                            ),  # Default first engineer
                        )
                        submitted = st.form_submit_button("Confirm Response")
                        if submitted:
                            # --- CRITICAL SECTION for starting response ---
                            # Re-check active incidents *just before* logging
                            current_active = get_active_incidents()
                            if job_name in current_active:
                                st.warning(
                                    f"{job_name} is already being handled by {current_active[job_name]['responder']}. Refreshing."
                                )
                            else:
                                incident_id = log_incident_start(
                                    job_name, status, assignee, priority
                                )
                                if incident_id != -1:
                                    st.success(
                                        f"Response logged for {job_name} (Incident {incident_id}). Assigned to {assignee} ({priority})."
                                    )
                                    # Add debug logging
                                    st.write(
                                        f"Debug: Active incidents after logging: {get_active_incidents()}"
                                    )
                                else:
                                    st.error(
                                        "Failed to log incident start (already active or DB error)."
                                    )  # log_incident_start handles check now

                            # Reset form state regardless of success/failure logging
                            st.session_state.show_respond_form[job_name] = False
                            st.session_state.selected_priority.pop(
                                job_name, None
                            )
                            st.session_state.selected_assignee.pop(
                                job_name, None
                            )
                            st.rerun()  # Force immediate refresh
                else:
                    # Show the "Respond Incident" button
                    if st.button("Respond Incident", key=respond_key):
                        # Set state to show the form on the next rerun
                        st.session_state.show_respond_form[job_name] = True
                        # Pre-populate state for form defaults if needed
                        st.session_state.selected_priority[job_name] = "P3"
                        st.session_state.selected_assignee[job_name] = (
                            ALL_ENGINEERS[0]
                        )
                        st.rerun()  # Rerun to display the form

            else:
                # Status is Warning or Log, no action needed
                st.markdown("*(No immediate action)*")

        with col4:  # Response Time column
            if active_incident_info:
                start_time = active_incident_info.get("start_time")
                st.markdown(f"Ongoing: {display_time_ago(start_time)}")
            else:
                st.markdown("-")  # Placeholder if not active

        st.markdown("---")  # Separator between jobs

# --- Incident History ---
st.subheader("Recent Incident History")
history_df = get_incident_history(limit=20)

if history_df is not None and not history_df.empty:
    # Format for display
    history_df_display = history_df.copy()
    time_cols = ["response_start_time", "resolution_time"]
    for col in time_cols:
        # Ensure the column exists and convert to datetime if needed
        if col in history_df_display.columns:
            history_df_display[col] = pd.to_datetime(
                history_df_display[col]
            ).dt.strftime("%Y-%m-%d %H:%M:%S")

    if "resolution_duration_seconds" in history_df_display.columns:
        history_df_display["duration_m"] = (
            history_df_display["resolution_duration_seconds"] / 60
        ).round(1)
        history_df_display.drop(
            columns=["resolution_duration_seconds"], inplace=True
        )  # Drop original seconds col
        # Reorder columns slightly
        cols_order = [
            "incident_id",
            "job_name",
            "status_at_incident",
            "priority",
            "responder_name",
            "response_start_time",
            "resolution_time",
            "duration_m",
        ]
        # Filter out columns that might not exist if the table is empty initially
        cols_order = [c for c in cols_order if c in history_df_display.columns]
        history_df_display = history_df_display[cols_order]

    st.dataframe(history_df_display, use_container_width=True)
else:
    st.markdown("No incident history recorded yet.")


# --- Auto-refresh mechanism ---
# Streamlit doesn't have a built-in reliable background scheduler visible to the user.
# The @st.cache_data handles data refresh. Forcing a UI refresh needs a trick.
# This component forces a rerun every N seconds.
# Note: This causes the *entire* script to rerun.
components.html(
    f"""
    <script>
        const interval = {REFRESH_INTERVAL_SECONDS * 1000}; // Convert to milliseconds

        // Function to reload the page (forces Streamlit rerun)
        const reloadPage = () => {{
            // Check if any form is currently active to avoid interrupting user input
            const forms = window.parent.document.querySelectorAll('form');
            let formActive = false;
            forms.forEach(form => {{
                // Simple check if form has focus or child elements have focus
                 if (form.contains(window.parent.document.activeElement)) {{
                     formActive = true;
                 }}
            }});

            // Also check for common Streamlit modal/dialog elements if necessary
            const modals = window.parent.document.querySelectorAll('[data-testid="stModal"]'); // Streamlit modal selector
             let modalActive = modals.length > 0 && modals[0].style.display !== 'none'; // Check if modal exists and is visible

            // Only reload if no form or modal seems active
            if (!formActive && !modalActive) {{
                window.parent.location.reload();
            }} else {{
                console.log('Auto-refresh skipped due to active form/modal.');
            }}
        }};

        // Set interval
        const intervalId = setInterval(reloadPage, interval);

        // Optional: Clear interval if the component is ever removed
        // (Streamlit usually re-runs the whole script, so this might not be strictly necessary)
        // return () => clearInterval(intervalId); // This syntax doesn't work directly in st.html

    </script>
    """,
    height=0,  # Make the component invisible
    width=0,
)
# Make sure to import `
