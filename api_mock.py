# api_mock.py
import flask
import random
import time
from flask import Flask, jsonify

app = Flask(__name__)

# In-memory state to simulate job changes and responses
mock_jobs = {
    "ETL Pipeline A": {"status": "Log", "last_run": time.time(), "responding_incident_id": None},
    "Data Backup Job": {"status": "Warning", "last_run": time.time(), "responding_incident_id": None},
    "Login Service Check": {"status": "Log", "last_run": time.time(), "responding_incident_id": None},
    "Payment Gateway Monitor": {"status": "Critical", "last_run": time.time(), "responding_incident_id": None},
    "User Profile Sync": {"status": "Error", "last_run": time.time(), "responding_incident_id": None},
    "Reporting Service Gen": {"status": "Log", "last_run": time.time(), "responding_incident_id": None},
}

STATUS_CHOICES = ["Critical", "Error", "Warning", "Log", "Log", "Log"] # Skew towards Log

@app.route('/api/job_status', methods=['GET'])
def get_job_status():
    # Simulate status changes randomly
    for job_name in mock_jobs:
        # Only change status sometimes, not every call
        if random.random() < 0.15: # 15% chance of status change per job
             # If responding, less likely to randomly flip back to Log/Warning
            if mock_jobs[job_name]["responding_incident_id"] is not None and mock_jobs[job_name]["status"] in ["Critical", "Error"]:
                 if random.random() < 0.1: # Low chance to self-heal while responding
                      mock_jobs[job_name]["status"] = random.choice(["Warning", "Log"])
                      mock_jobs[job_name]["responding_incident_id"] = None # Assume self-heal resolves it
                 # else: keep critical/error status
            else:
                mock_jobs[job_name]["status"] = random.choice(STATUS_CHOICES)
                mock_jobs[job_name]["last_run"] = time.time()
                # If it flipped away from Critical/Error, clear any simulated response state
                if mock_jobs[job_name]["status"] not in ["Critical", "Error"]:
                     mock_jobs[job_name]["responding_incident_id"] = None


    # Return current state
    jobs_list = [{"name": name, "status": data["status"]} for name, data in mock_jobs.items()]
    return jsonify(jobs_list)

# --- Simulation of Incident Response State (Not real API endpoints) ---
# These endpoints are just for the *mock* to know if the dashboard
# thinks it's responding, so it doesn't randomly flip the status back to Log too quickly.
# In a real scenario, the dashboard reads the actual job status API only.

@app.route('/api/mock/start_response/<job_name>/<int:incident_id>', methods=['POST'])
def mock_start_response(job_name, incident_id):
    if job_name in mock_jobs:
        mock_jobs[job_name]["responding_incident_id"] = incident_id
        # Ensure status stays Critical/Error for a bit
        if mock_jobs[job_name]["status"] not in ["Critical", "Error"]:
             mock_jobs[job_name]["status"] = random.choice(["Critical", "Error"])
        print(f"MOCK: Started responding to {job_name} (Incident {incident_id})")
        return jsonify({"message": "Mock response started"}), 200
    return jsonify({"error": "Job not found"}), 404

@app.route('/api/mock/resolve_response/<job_name>/<int:incident_id>', methods=['POST'])
def mock_resolve_response(job_name, incident_id):
    if job_name in mock_jobs and mock_jobs[job_name]["responding_incident_id"] == incident_id:
        mock_jobs[job_name]["responding_incident_id"] = None
        # Optionally simulate resolution fixes the status
        if random.random() < 0.8: # 80% chance resolution fixes it
             mock_jobs[job_name]["status"] = "Log"
        print(f"MOCK: Resolved response for {job_name} (Incident {incident_id})")
        return jsonify({"message": "Mock response resolved"}), 200
    elif job_name in mock_jobs:
         # Incident ID mismatch or already resolved in mock
         print(f"MOCK: Resolve requested for {job_name} (Incident {incident_id}), but mock state is different ({mock_jobs[job_name]['responding_incident_id']})")
         return jsonify({"message": "Mock incident ID mismatch or already resolved"}), 200 # Still OK from dashboard perspective
    return jsonify({"error": "Job not found"}), 404


if __name__ == '__main__':
    app.run(port=5001, debug=True) # Run on a different port than default 5000