import os
import glob
import pandas as pd
from flask import Flask, request, jsonify

app = Flask(__name__)

# Output directory on host
OUTPUT_DIR = os.path.join("dev", "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. Target URL registry mapped to scraper module identifiers
TASK_QUEUE = [
    {"id": "tcd", "url": "https://www.tacomadome.org/events", "status": "pending"},
    {"id": "pyf", "url": "https://www.thefair.com/events-calendar/", "status": "pending"},
    {"id": "fwpaec", "url": "https://fwpaec.org/event-calendar/", "status": "pending"},
    {"id": "eqc", "url": "https://emeraldqueen.com/tickets/", "status": "pending"},
    {"id": "tcp", "url": "https://www.parkstacoma.gov/events/list/", "status": "pending"},
    {"id": "mcm", "url": "https://www.mcmenamins.com/elks-temple/spanish-ballroom", "status": "pending"},
    {"id": "pdza", "url": "https://www.pdza.org/events/", "status": "pending"},
    {"id": "tcct", "url": "https://www.tacomacitytheaters.org/events", "status": "pending"},
    {"id": "tccc", "url": "https://www.tacomacomedyclub.com/events", "status": "pending"},
    {"id": "val", "url": "https://www.thevalleytacoma.com/new-events-1", "status": "pending"},
    {"id": "nfl", "url": "https://www.newfrontierlounge.com/calendar#/events", "status": "pending"},
    {"id": "jzb", "url": "https://jazzbones.com/#/events", "status": "pending"},
    {"id": "dph", "url": "https://www.doylespublichouse.com/events", "status": "pending"},
    {"id": "tpd", "url": "https://visitgigharbor.com/events-festivals/", "status": "pending"},
    {"id": "gh", "url": "https://visitgigharbor.com/events-festivals/list/page/", "status": "pending"},
    {"id": "tam", "url": "https://www.tacomaartmuseum.org/events/", "status": "pending"},
    {"id": "tst", "url": "https://www.thespartavern.com/live-music", "status": "pending"},
    {"id": "cnc", "url": "https://www.ciderandcedar.com/events", "status": "pending"},
    {"id": "cup", "url": "https://www.cityofup.com/132/Events", "status": "pending"},
    {"id": "tcam", "url": "https://calendar.time.ly/api/calendars/54705487/events", "status": "pending"},
    {"id": "tcal", "url": "https://www.tacomaartslive.org/upcoming-events/", "status": "pending"},
    {"id": "tcpc", "url": "https://www.visitpiercecounty.com/", "status": "pending"}
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