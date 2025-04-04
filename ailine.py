import os
import sqlite3
import subprocess
import shutil
import git
import mlflow
import click
import logging
from datetime import datetime
from flask import Flask, render_template, jsonify, request

# Config (move to env vars or config file later)
DB_PATH = os.environ.get("AILINE_DB_PATH", "ailine_tree.db")
CONFIG_PATH = os.environ.get("AILINE_CONFIG_PATH", "ailine_config.txt")
REPO_DIR = os.environ.get("AILINE_REPO_DIR", "repo")
MLFLOW_TRACKING_URI = os.environ.get("AILINE_MLFLOW_URI", os.path.abspath("mlruns"))
LOG_PATH = os.environ.get("AILINE_LOG_PATH", "ailine.log")

# Setup logging
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
app = Flask(__name__)
REPO_URL = None

def init_db():
    if os.path.exists(DB_PATH):
        logging.info(f"Database found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tree (
        id TEXT PRIMARY KEY,
        type TEXT,
        parent TEXT,
        mlflow_run TEXT,
        dvc_version TEXT,
        snapshot_path TEXT,
        timestamp TEXT,
        git_url TEXT
    )''')
    conn.commit()
    conn.close()
    logging.info("Database initialized")

def create_snapshot(snapshot_id):
    snapshot_dir = os.path.abspath(os.path.join(REPO_DIR, "snapshots"))
    snapshot_base = os.path.join(snapshot_dir, snapshot_id)
    snapshot_path = f"{snapshot_base}.zip"
    os.makedirs(snapshot_dir, exist_ok=True)
    original_dir = os.getcwd()
    os.chdir(REPO_DIR)
    shutil.make_archive(snapshot_base, "zip", ".")
    os.chdir(original_dir)
    if not os.path.exists(snapshot_path):
        raise FileNotFoundError(f"Snapshot not created at {snapshot_path}")
    logging.info(f"Snapshot created: {snapshot_path}")
    return snapshot_path

def load_config():
    global REPO_URL
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            REPO_URL = f.read().strip()
        logging.info(f"Loaded REPO_URL: {REPO_URL}")
    return REPO_URL

@click.group()
def cli():
    init_db()
    load_config()
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    logging.info(f"MLflow tracking URI set to {MLFLOW_TRACKING_URI}")

@cli.command()
@click.argument("repo_url")
def init(repo_url):
    global REPO_URL
    REPO_URL = repo_url
    if os.path.exists(REPO_DIR):
        raise click.UsageError(f"Directory {REPO_DIR} already exists. Run 'cleanup' first.")
    subprocess.run(["git", "clone", repo_url, REPO_DIR], check=True)
    with open(CONFIG_PATH, "w") as f:
        f.write(repo_url)
    subprocess.run(["git", "fetch"], check=True, cwd=REPO_DIR)
    logging.info(f"Initialized AIline with {repo_url} in {REPO_DIR}")
    print(f"Initialized AIline with {repo_url} in {REPO_DIR}")

@cli.command()
@click.option("--script", default="train.py", help="Script to run")
@click.option("--dataset", default="data.csv", help="Dataset file")
def run(script, dataset):
    if not REPO_URL:
        raise click.UsageError("AIline not initialized. Run 'ailine init <repo_url>' first.")
    if not os.path.exists(REPO_DIR):
        raise click.UsageError(f"Repo directory {REPO_DIR} not found. Re-run 'init'.")
    if not os.path.exists(os.path.join(REPO_DIR, script)):
        raise click.UsageError(f"Script {script} not found in {REPO_DIR}")
    if not os.path.exists(os.path.join(REPO_DIR, dataset)):
        raise click.UsageError(f"Dataset {dataset} not found in {REPO_DIR}")

    repo = git.Repo(REPO_DIR)
    latest_commit = repo.head.commit.hexsha[:7]
    git_url = f"{REPO_URL.replace('.git', '')}/commit/{repo.head.commit.hexsha}"

    if repo.is_dirty():
        snapshot_id = f"snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        snapshot_path = create_snapshot(snapshot_id)
        commit_id = snapshot_id
        commit_type = "snapshot"
        parent = latest_commit
    else:
        commit_id = latest_commit
        commit_type = "git"
        snapshot_path = None
        parent = None

    original_dir = os.getcwd()
    os.chdir(REPO_DIR)
    subprocess.run(["dvc", "add", dataset], check=True)
    dvc_version = f"dataset_001_v{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    with mlflow.start_run(run_name=f"exp_{commit_id[:8]}"):
        subprocess.run(["python", script], check=True)
        mlflow.log_param("lr", 0.02)
        mlflow.log_metric("accuracy", 0.87)
        mlflow.set_tag("commit" if commit_type == "git" else "snapshot", commit_id)
        mlflow.set_tag("dataset", dvc_version)
        run_id = mlflow.active_run().info.run_id
    
    os.chdir(original_dir)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO tree (id, type, parent, mlflow_run, dvc_version, snapshot_path, timestamp, git_url)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (commit_id, commit_type, parent, run_id, dvc_version, snapshot_path, datetime.now().isoformat(), git_url if commit_type == "git" else None))
    conn.commit()
    conn.close()
    logging.info(f"Experiment logged: {run_id} tied to {commit_id}")
    print(f"Experiment logged: {run_id} tied to {commit_id}")

@cli.command()
def cleanup():
    global REPO_URL
    items_to_remove = [MLFLOW_TRACKING_URI, REPO_DIR, DB_PATH, CONFIG_PATH]
    for item in os.listdir("."):
        if item.startswith("temp_") and os.path.isdir(item):
            items_to_remove.append(item)
    
    for item in items_to_remove:
        if os.path.isdir(item):
            shutil.rmtree(item, ignore_errors=True)
            logging.info(f"Removed directory: {item}")
            print(f"Removed directory: {item}")
        elif os.path.isfile(item):
            os.remove(item)
            logging.info(f"Removed file: {item}")
            print(f"Removed file: {item}")
    
    REPO_URL = None
    logging.info("Cleanup complete")
    print("Cleanup complete. Run 'ailine init <repo_url>' to start fresh.")

@app.route("/commits")
def commits():
    load_config()
    if not os.path.exists(DB_PATH):
        return "Database not found. Run 'ailine init' and 'ailine run' first.", 500
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, type, parent, mlflow_run, dvc_version, snapshot_path, timestamp, git_url FROM tree")
    tree = [{"id": r[0], "type": r[1], "parent": r[2], "mlflow_run": r[3], "dvc_version": r[4], 
             "snapshot_path": r[5], "timestamp": r[6], "git_url": r[7]} for r in c.fetchall()]
    conn.close()
    logging.info("Commits page accessed")
    return render_template("commits.html", tree=tree)

@app.route("/experiments")
def experiments():
    load_config()
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    runs = mlflow.search_runs()
    runs_data = [{"run_id": r["run_id"], "accuracy": r.get("metrics.accuracy", "N/A"), 
                  "commit": r.get("tags.commit"), "snapshot": r.get("tags.snapshot"), 
                  "dataset": r["tags.dataset"], "timestamp": r.get("info.start_time", "N/A")} 
                 for r in runs.to_dict(orient="records")]
    logging.info(f"Experiments page accessed, found {len(runs_data)} runs")
    return render_template("experiments.html", runs=runs_data, repo_url=REPO_URL)

@app.route("/commit/<commit_id>")
def commit_view(commit_id):
    load_config()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT git_url FROM tree WHERE id = ?", (commit_id,))
    result = c.fetchone()
    conn.close()
    if not result or not result[0]:
        logging.warning(f"Commit {commit_id} not found")
        return "Commit not found", 404

    repo = git.Repo(REPO_DIR)
    original_branch = repo.active_branch.name
    repo.git.checkout(commit_id)
    
    files = []
    original_dir = os.getcwd()
    os.chdir(REPO_DIR)
    for root, _, filenames in os.walk("."):
        for filename in filenames:
            file_path = os.path.join(root, filename)
            rel_path = os.path.relpath(file_path, ".")
            with open(file_path, "r", errors="ignore") as f:
                content = f.read()
            files.append({"path": rel_path, "content": content})
    
    repo.git.checkout(original_branch)
    os.chdir(original_dir)
    logging.info(f"Viewed commit {commit_id}")
    return render_template("commit.html", commit_id=commit_id, files=files, git_url=result[0])

@app.route("/snapshot/<snapshot_id>")
def snapshot_view(snapshot_id):
    load_config()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT snapshot_path, parent FROM tree WHERE id = ?", (snapshot_id,))
    result = c.fetchone()
    conn.close()
    if not result:
        logging.warning(f"Snapshot {snapshot_id} not found")
        return "Snapshot not found", 404
    
    snapshot_path, parent = result
    if not os.path.exists(snapshot_path):
        logging.error(f"Snapshot file not found at {snapshot_path}")
        return f"Snapshot file not found at {snapshot_path}", 500
    
    temp_dir = os.path.abspath(f"temp_{snapshot_id}")
    try:
        shutil.unpack_archive(snapshot_path, temp_dir)
    except Exception as e:
        logging.error(f"Failed to unpack snapshot {snapshot_id}: {str(e)}")
        return f"Failed to unpack snapshot: {str(e)}", 500
    
    files = []
    for root, _, filenames in os.walk(temp_dir):
        for filename in filenames:
            file_path = os.path.join(root, filename)
            rel_path = os.path.relpath(file_path, temp_dir)
            try:
                with open(file_path, "r", errors="ignore") as f:
                    content = f.read()
                files.append({"path": rel_path, "content": content})
            except Exception as e:
                files.append({"path": rel_path, "content": f"Error reading file: {str(e)}"})
    
    shutil.rmtree(temp_dir, ignore_errors=True)
    parent_url = f"{REPO_URL.replace('.git', '')}/commit/{parent}" if parent and REPO_URL else None
    logging.info(f"Viewed snapshot {snapshot_id}")
    return render_template("snapshot.html", snapshot_id=snapshot_id, files=files, parent_url=parent_url)

if __name__ == "__main__":
    cli()