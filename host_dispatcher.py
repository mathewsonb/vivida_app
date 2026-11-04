import os
import glob
import pandas as pd
from flask import Flask, request, jsonify

app = Flask(__name__)

# Output directory on host
OUTPUT_DIR = os.path.join("dev", "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. Target URL registry mapped to scraper module identifiers
# ============================================================
# NOTE: The 'id' values here should correspond to the scraper module names in the VM,
# and should be the first part of each df output name in each scraper module.
TASK_QUEUE = [
    {"id": "td", "status": "pending"},
    {"id": "eqc", "status": "pending"},
    {"id": "pfg", "status": "pending"},
    {"id": "tp", "status": "pending"},
    {"id": "fw", "status": "pending"},
    {"id": "mcm", "status": "pending"},
    {"id": "pdz", "status": "pending"},
    {"id": "rpt", "status": "pending"},
    {"id": "tcc", "status": "pending"},
    {"id": "val", "status": "pending"},
    {"id": "nf", "status": "pending"},
    {"id": "jb", "status": "pending"},
    {"id": "dph", "status": "pending"},
    {"id": "gh", "status": "pending"},
    {"id": "tam", "status": "pending"},
    {"id": "tst", "status": "pending"},
    {"id": "tpd", "status": "pending"},
    {"id": "cup", "status": "pending"},
    {"id": "cnc", "status": "pending"},
    {"id": "amt", "status": "pending"},
    {"id": "tal", "status": "pending"},
    {"id": "tpc", "status": "pending"},
    {"id": "oo", "status": "pending"},
    # Add any additional URLs here...
]

@app.route("/pop_task", methods=["GET"])
def pop_task():
    """VM calls this endpoint to grab the next available pending task."""
    for task in TASK_QUEUE:
        if task["status"] == "pending":
            task["status"] = "in_progress"
            print(f"[Dispatcher] Assigned {task['id']} to worker.")
            return jsonify({"status": "available", "task": task})
    
    return jsonify({"status": "empty", "message": "No remaining tasks in queue."})

@app.route("/submit_csv", methods=["POST"])
def submit_csv():
    """VM posts its completed scraper output CSV file here."""
    if 'file' not in request.files:
        return jsonify({"error": "No file attached"}), 400

    file = request.files['file']
    task_id = request.form.get("task_id", "unknown")
    
    output_filepath = os.path.join(OUTPUT_DIR, f"{task_id}.csv")
    file.save(output_filepath)
    
    # Mark task completed
    for task in TASK_QUEUE:
        if task["id"] == task_id:
            task["status"] = "completed"
            break

    print(f"[Dispatcher] Successfully saved result for: {task_id}")
    return jsonify({"status": "success", "saved_path": output_filepath})

@app.route("/pipeline_status", methods=["GET"])
def pipeline_status():
    """Check remaining, in-progress, and completed tasks."""
    summary = {
        "pending": sum(1 for t in TASK_QUEUE if t["status"] == "pending"),
        "in_progress": sum(1 for t in TASK_QUEUE if t["status"] == "in_progress"),
        "completed": sum(1 for t in TASK_QUEUE if t["status"] == "completed"),
        "total": len(TASK_QUEUE)
    }
    return jsonify(summary)

if __name__ == "__main__":
    print("Host Task Dispatcher listening on http://0.0.0.0:5000...")
    app.run(host="0.0.0.0", port=5000)