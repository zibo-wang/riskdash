"""SRE Automation Dashboard Streamlit Application.

This module implements a Streamlit-based dashboard for monitoring and managing SRE automation jobs.
It provides real-time status monitoring, incident response management, and engineer coordination.

The dashboard features:
- Real-time job status monitoring
- Incident response tracking
- Engineer management
- Job link management
- Auto-refresh functionality
"""

import time
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from db_utils import (
    add_engineer,
    add_job_link,
    get_active_incidents,
    get_db_connection,
    get_engineers,
    get_incident_history,
    get_job_links,
    init_db,
    log_incident_resolve,
    log_incident_start,
    remove_engineer,
    remove_job_link,
)

# --- Configuration ---
API_ENDPOINT = (
    "http://127.0.0.1:5001/api/job_status"  # Change to your real API endpoint
)
REFRESH_INTERVAL_SECONDS = 10  # How often to fetch API status

# Enable auto-refresh
st.set_page_config(
    layout="wide",
    page_title="SRE Automation Dashboard",
    initial_sidebar_state="expanded",
    menu_items={"Get Help": None, "Report a bug": None, "About": None},
)

# --- State Management ---
# Initialize all session state variables first
if "show_respond_form" not in st.session_state:
    st.session_state.show_respond_form = {}  # Dict: {job_name: boolean}
if "selected_priority" not in st.session_state:
    st.session_state.selected_priority = {}  # Dict: {job_name: priority}
if "selected_assignee" not in st.session_state:
    st.session_state.selected_assignee = {}  # Dict: {job_name: assignee}
if "show_engineer_form" not in st.session_state:
    st.session_state.show_engineer_form = False
if "last_refresh_time" not in st.session_state:
    st.session_state.last_refresh_time = datetime.now()
if "last_incident_update" not in st.session_state:
    st.session_state.last_incident_update = datetime.now()
if "job_links" not in st.session_state:
    st.session_state.job_links = get_job_links()
if "is_editing_link" not in st.session_state:
    st.session_state.is_editing_link = False
if "is_editing_engineers" not in st.session_state:
    st.session_state.is_editing_engineers = False
if "clicked_link_button" not in st.session_state:
    st.session_state.clicked_link_button = False
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = time.time()

# --- Auto-refresh logic ---
# Check if any forms are being edited
any_forms_open = (
    st.session_state.is_editing_link
    or st.session_state.is_editing_engineers
    or any(st.session_state.show_respond_form.values())
    or st.session_state.clicked_link_button
)

# Only refresh if no forms are being edited
if not any_forms_open:
    current_time = time.time()
    if current_time - st.session_state.last_refresh >= REFRESH_INTERVAL_SECONDS:
        st.session_state.last_refresh = current_time
        st.rerun()

# Add auto-refresh meta tag only if no forms are open
if not any_forms_open:
    st.markdown(
        f"""
        <meta http-equiv="refresh" content="{REFRESH_INTERVAL_SECONDS}">
        """,
        unsafe_allow_html=True,
    )

# Update last refresh time on each page load
st.session_state.last_refresh_time = datetime.now()

# Status levels for ranking
STATUS_ORDER = {"Critical": 0, "Error": 1, "Warning": 2, "Log": 3}
STATUS_EMOJI = {"Critical": "🔥", "Error": "❌", "Warning": "⚠️", "Log": "📄"}
PRIORITY_LEVELS = ["P1", "P2", "P3", "P4"]

# --- Initialize Database ---
# Ensure the table exists when the app starts
init_db()

# --- Helper Functions ---


def check_for_updates():
    """Checks if there have been any changes to active incidents since last refresh.

    Returns:
        bool: True if updates are needed, False otherwise.
    """
    current_active = get_active_incidents()
    current_time = datetime.now()

    # If the active incidents have changed, update the last incident update time
    if current_active != st.session_state.get("last_active_incidents", {}):
        st.session_state.last_incident_update = current_time
        st.session_state.last_active_incidents = current_active
        return True

    # If it's been more than 30 seconds since last update, force a refresh
    if (
        current_time - st.session_state.last_incident_update
    ).total_seconds() > 30:
        return True

    return False


@st.cache_data(ttl=REFRESH_INTERVAL_SECONDS)  # Cache API data for interval
def fetch_job_status(api_url):
    """Fetches job status list from the API.

    Args:
        api_url (str): URL of the API endpoint to fetch job status from.

    Returns:
        list: List of job status dictionaries, or None if fetch failed.
              Each job dictionary should contain at least 'name' and 'status' keys.
    """
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
    """Sorts jobs by critical level and pins responding jobs to the top.

    Args:
        jobs (list): List of job status dictionaries.

    Returns:
        list: Sorted list of jobs with responding jobs at the top.
    """
    if jobs is None:
        return []

    # Get active incidents to check which jobs are being responded to
    active_incidents = get_active_incidents()

    # Split jobs into responding and non-responding
    responding_jobs = []
    non_responding_jobs = []

    for job in jobs:
        job_name = job.get("name", "Unknown Job")
        if job_name in active_incidents:
            responding_jobs.append(job)
        else:
            non_responding_jobs.append(job)

    # Sort non-responding jobs by status
    sorted_non_responding = sorted(
        non_responding_jobs,
        key=lambda x: STATUS_ORDER.get(x.get("status", "Log"), 99),
    )

    # Combine lists with responding jobs first
    return responding_jobs + sorted_non_responding


def display_time_ago(dt_object):
    """Displays a datetime object as 'time ago'.

    Args:
        dt_object (datetime): The datetime object to format.

    Returns:
        str: Formatted string showing how long ago the time was.
    """
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

st.title(" SRE Automation Job Status Dashboard")

# Fetch and display data
st.subheader("Job Status Overview")

# Create a container for refresh info and align it to the right
refresh_container = st.container()
with refresh_container:
    col1, col2 = st.columns([6, 1])  # Use most of the space for padding
    with col2:
        refresh_col1, refresh_col2 = st.columns([2, 1])
        with refresh_col1:
            st.markdown(
                f"Last refresh: {st.session_state.last_refresh_time.strftime('%H:%M:%S')}",
                unsafe_allow_html=True,
            )
        with refresh_col2:
            if st.button("🔄", key="refresh_button"):
                st.session_state.last_refresh_time = datetime.now()
                st.cache_data.clear()  # Clear the cache to force a new API fetch
                st.rerun()

# Check for updates and force refresh if needed
if check_for_updates():
    st.session_state.last_refresh_time = datetime.now()
    st.cache_data.clear()
    st.rerun()

api_jobs_raw = fetch_job_status(API_ENDPOINT)
ranked_jobs = rank_jobs(api_jobs_raw)
active_incidents = (
    get_active_incidents()
)  # Get jobs currently being responded to

# Display Issue Statistics at the very top of sidebar
st.sidebar.subheader("Issue Statistics")

# Calculate statistics
if ranked_jobs:
    # Only count critical and error issues
    critical_error_issues = [
        job for job in ranked_jobs if job.get("status") in ["Critical", "Error"]
    ]
    total_issues = len(critical_error_issues)

    # Count how many of these critical/error issues are being responded to
    responded_issues = sum(
        1
        for job in critical_error_issues
        if job.get("name") in active_incidents
    )
    pending_issues = total_issues - responded_issues

    # Add color block indicator
    if pending_issues > 0:
        st.sidebar.markdown(
            '<div style="background-color: #ff4b4b; height: 10px; border-radius: 5px; margin-bottom: 10px;"></div>',
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown(
            '<div style="background-color: #00cc96; height: 10px; border-radius: 5px; margin-bottom: 10px;"></div>',
            unsafe_allow_html=True,
        )

    # Display statistics with color indicators
    st.sidebar.markdown(f"**Total Issues:** {total_issues}")
    st.sidebar.markdown(f"**Responded:** {responded_issues}")

    # Add color indicator for pending issues
    if pending_issues > 0:
        st.sidebar.markdown(
            f"**Pending:** <span style='color: red;'>{pending_issues}</span>",
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown(f"**Pending:** {pending_issues}")

st.sidebar.markdown("---")

# Display L1/L2 Engineers
st.sidebar.subheader("On-Call Engineers")

# Get current engineers from database
engineers = get_engineers()
L1_ENGINEERS = engineers["L1"]
L2_ENGINEERS = engineers["L2"]
ALL_ENGINEERS = sorted(L1_ENGINEERS + L2_ENGINEERS)

# Always display current engineers
st.sidebar.markdown("**L1 Support:**")
for name in L1_ENGINEERS:
    st.sidebar.markdown(f"- {name}")

st.sidebar.markdown("**L2 Support:**")
for name in L2_ENGINEERS:
    st.sidebar.markdown(f"- {name}")

# Edit engineers button
if st.sidebar.button("✏️ Edit Engineers"):
    st.session_state.show_engineer_form = True

if st.session_state.show_engineer_form:
    with st.sidebar.form("engineer_management_form"):
        st.markdown("**Engineer Management**")

        # Remove engineer section
        st.markdown("**Remove Engineer**")
        engineer_to_remove = st.selectbox(
            "Select engineer to remove",
            [""] + ALL_ENGINEERS,
            format_func=lambda x: "Select an engineer..." if x == "" else x,
        )
        if st.form_submit_button("Remove Selected"):
            if engineer_to_remove:
                if remove_engineer(engineer_to_remove):
                    st.rerun()

        st.markdown("---")
        st.markdown("**Add New Engineer**")
        new_name = st.text_input("Name")
        new_level = st.radio("Level", ["L1", "L2"], horizontal=True)

        col1, col2 = st.columns(2)
        if col1.form_submit_button("Add"):
            if new_name and new_name not in ALL_ENGINEERS:
                if add_engineer(new_name, new_level):
                    st.session_state.show_engineer_form = False
                    st.rerun()
                else:
                    st.error("Failed to add engineer. Please try again.")
            else:
                st.error("Please enter a valid, unique name.")

        if col2.form_submit_button("Close"):
            st.session_state.show_engineer_form = False
            st.rerun()

if not ranked_jobs:
    st.warning(
        "No job data available. Check API connection or wait for refresh."
    )
else:
    # Create columns for layout
    col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(
        [3, 1, 1, 1, 2, 2, 2, 2]
    )
    col1.markdown("**Job Name**")
    col2.markdown("**Status**")
    col3.markdown("**Responder**")
    col4.markdown("**Priority**")
    col5.markdown("**Response Time**")
    col6.markdown("**Duration**")
    col7.markdown("**Links**")
    col8.markdown("**Action**")

    st.markdown("---")  # Separator

    for job in ranked_jobs:
        job_name = job.get("name", "Unknown Job")
        status = job.get("status", "Unknown")
        status_icon = STATUS_EMOJI.get(status, "❓")

        col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(
            [3, 1, 1, 1, 2, 2, 2, 2]
        )
        col1.markdown(f"**{job_name}**")
        col2.markdown(f"{status_icon} {status}")

        # --- Incident Handling Logic ---
        is_critical_or_error = status in ["Critical", "Error"]
        active_incident_info = active_incidents.get(job_name)

        with col3:  # Responder column
            if active_incident_info:
                st.markdown(f"**{active_incident_info['responder']}**")
            else:
                st.markdown("-")

        with col4:  # Priority column
            if active_incident_info:
                if st.button("✏️", key=f"edit_priority_{job_name}"):
                    st.session_state.show_priority_form = True
                    st.session_state.editing_priority_job = job_name
                    st.rerun()

                if (
                    st.session_state.get("show_priority_form", False)
                    and st.session_state.get("editing_priority_job") == job_name
                ):
                    with st.form(key=f"priority_form_{job_name}"):
                        new_priority = st.radio(
                            "Priority:",
                            PRIORITY_LEVELS,
                            key=f"new_priority_{job_name}",
                            horizontal=True,
                            index=PRIORITY_LEVELS.index(
                                active_incident_info["priority"]
                            ),
                        )
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.form_submit_button("✓"):
                                # Update priority in database
                                conn = get_db_connection()
                                try:
                                    conn.execute(
                                        """
                                        UPDATE incidents
                                        SET priority = ?
                                        WHERE incident_id = ?
                                        """,
                                        (
                                            new_priority,
                                            active_incident_info["incident_id"],
                                        ),
                                    )
                                    st.session_state.show_priority_form = False
                                    st.session_state.editing_priority_job = None
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to update priority: {e}")
                                finally:
                                    conn.close()
                        with col2:
                            if st.form_submit_button("✗"):
                                st.session_state.show_priority_form = False
                                st.session_state.editing_priority_job = None
                                st.rerun()
                else:
                    st.markdown(f"**{active_incident_info['priority']}**")
            else:
                st.markdown("-")

        with col5:  # Response Time column
            if active_incident_info:
                start_time = active_incident_info.get("start_time")
                st.markdown(f"Ongoing: {display_time_ago(start_time)}")
            else:
                st.markdown("-")  # Placeholder if not active

        with col6:  # Duration column
            if active_incident_info and "start_time" in active_incident_info:
                start_time = active_incident_info["start_time"]
                duration = datetime.now() - start_time
                hours = int(duration.total_seconds() // 3600)
                minutes = int((duration.total_seconds() % 3600) // 60)
                seconds = int(duration.total_seconds() % 60)
                st.markdown(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
            else:
                st.markdown("-")

        with col7:  # Links column
            # Create a container for the link and button
            link_container = st.container()
            with link_container:
                link_col1, link_col2 = st.columns([4, 1])

                with link_col1:
                    # Display existing link if any
                    if (
                        active_incident_info
                        and active_incident_info["incident_id"]
                        in st.session_state.job_links
                    ):
                        link_data = st.session_state.job_links[
                            active_incident_info["incident_id"]
                        ]
                        st.markdown(
                            f"🔗 [{link_data['text']}]({link_data['url']})"
                        )
                    else:
                        st.markdown("•")

                with link_col2:
                    # Only show link button if there's an active incident
                    if job_name in active_incidents:
                        # Add/Edit link button
                        if st.button("🔗", key=f"edit_link_{job_name}"):
                            # Set all states to pause refresh
                            st.session_state.is_editing_link = True
                            st.session_state.editing_job = job_name
                            st.session_state.clicked_link_button = True
                            # Force a rerun to show the form
                            st.rerun()

            # Link form
            if (
                st.session_state.is_editing_link
                and st.session_state.editing_job == job_name
            ):
                with st.form(key=f"link_form_{job_name}"):
                    st.text_input(
                        "Link Text",
                        value=st.session_state.job_links.get(
                            active_incident_info["incident_id"], {}
                        ).get("text", ""),
                        key=f"link_text_{job_name}",
                    )
                    st.text_input(
                        "URL",
                        value=st.session_state.job_links.get(
                            active_incident_info["incident_id"], {}
                        ).get("url", ""),
                        key=f"link_url_{job_name}",
                    )
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.form_submit_button("✓"):
                            link_text = st.session_state[
                                f"link_text_{job_name}"
                            ]
                            link_url = st.session_state[f"link_url_{job_name}"]
                            if link_url and active_incident_info:
                                if add_job_link(
                                    active_incident_info["incident_id"],
                                    link_url,
                                    link_text,
                                ):
                                    st.session_state.job_links = get_job_links()
                                    # Reset all states to resume refresh
                                    st.session_state.is_editing_link = False
                                    st.session_state.editing_job = None
                                    st.session_state.clicked_link_button = False
                                    st.rerun()
                    with col2:
                        if st.form_submit_button("✗"):
                            # Reset all states to resume refresh
                            st.session_state.is_editing_link = False
                            st.session_state.editing_job = None
                            st.session_state.clicked_link_button = False
                            st.rerun()

        with col8:  # Action button column
            if active_incident_info:
                # Job is currently being responded to
                incident_id = active_incident_info["incident_id"]
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
                                    # Force immediate refresh to update statistics
                                    st.cache_data.clear()
                                    st.rerun()
                                else:
                                    st.error(
                                        "Failed to log incident start (already active or DB error)."
                                    )  # log_incident_start handles check now

                            # Reset form state regardless of success/failure logging
                            st.session_state.show_respond_form[job_name] = False
                            st.session_state.selected_priority[job_name] = (
                                priority
                            )
                            st.session_state.selected_assignee[job_name] = (
                                assignee
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
                st.markdown("*(No action)*")

        st.markdown("---")  # Separator between jobs

# --- Incident History ---
st.subheader("Recent Incident History")
history_df = get_incident_history(limit=20)

if history_df is not None and not history_df.empty:
    # Filter out incidents older than 5 days
    five_days_ago = pd.Timestamp.now() - pd.Timedelta(days=5)
    history_df = history_df[history_df["response_start_time"] >= five_days_ago]

    # Create columns for layout
    col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(
        [1, 2, 2, 1, 2, 2, 2, 1]
    )
    col1.markdown("**ID**")
    col2.markdown("**Job Name**")
    col3.markdown("**Responder**")
    col4.markdown("**Priority**")
    col5.markdown("**Start Time**")
    col6.markdown("**End Time**")
    col7.markdown("**Duration**")
    col8.markdown("**Links**")

    st.markdown("---")  # Separator

    for _, row in history_df.iterrows():
        col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(
            [1, 2, 2, 1, 2, 2, 2, 1]
        )

        # Format the time
        start_time = pd.to_datetime(row["response_start_time"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        resolution_time = (
            pd.to_datetime(row["resolution_time"]).strftime("%Y-%m-%d %H:%M:%S")
            if pd.notna(row["resolution_time"])
            else "-"
        )

        # Format duration in HH:MM:SS
        if pd.notna(row["resolution_duration_seconds"]):
            total_seconds = int(row["resolution_duration_seconds"])
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            duration = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            duration = "-"

        col1.markdown(f"**{row['incident_id']}**")
        col2.markdown(f"**{row['job_name']}**")
        col3.markdown(f"{row['responder_name']}")
        col4.markdown(f"**{row['priority']}**")
        col5.markdown(start_time)
        col6.markdown(resolution_time)
        col7.markdown(duration)

        with col8:  # Links column
            if row["incident_id"] in st.session_state.job_links:
                link_data = st.session_state.job_links[row["incident_id"]]
                st.markdown(f"🔗 [{link_data['text']}]({link_data['url']})")
            else:
                st.markdown("•")

        st.markdown("---")  # Separator between incidents
else:
    st.markdown("No incident history recorded yet.")
