from datetime import datetime


def format_timestamp(timestamp):
    """Convert timestamp to readable format"""
    dt = datetime.fromisoformat(timestamp)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def print_formatted_data(data):
    """Print formatted data in terminal"""
    print("\nDataset Versions:")
    print("-" * 80)
    
    for idx, item in enumerate(data, 1):
        print(f"\nVersion {idx}:")
        print("-" * 20)
        
        print(f"{'ID':<15}: {item['id']}")
        print(f"{'Type':<15}: {item['type']}")
        print(f"{'Parent':<15}: {item['parent'] or 'None'}")
        print(f"{'MLflow Run':<15}: {item['mlflow_run']}")
        print(f"{'DVC Version':<15}: {item['dvc_version']}")
        print(f"{'DVC Status':<15}: {item.get('dvc_linkage_status', 'missing')}")
        print(f"{'DVC Paths':<15}: {item.get('dvc_linkage_count', 0)}")
        print(f"{'Env Status':<15}: {item.get('env_fingerprint_status', 'missing')}")
        print(f"{'Run Command':<15}: {item.get('run_command_summary') or 'None'}")
        print(f"{'Snapshot Path':<15}: {item['snapshot_path'] or 'None'}")
        print(f"{'Timestamp':<15}: {format_timestamp(item['timestamp'])}")
        print(f"{'Git URL':<15}: {item['git_url'] or 'None'}")
        env = item.get("env_fingerprint") or {}
        if env:
            print(f"{'Env Python':<15}: {env.get('python_version')}")
            print(f"{'Env Platform':<15}: {env.get('platform')}")
            print(f"{'Poetry Lock':<15}: {env.get('poetry_lock_sha256') or 'None'}")
            if env.get("packages"):
                print(f"{'Env Packages':<15}:")
                for pkg, version in env["packages"].items():
                    print(f"  - {pkg}={version}")
        if item.get("dvc_linkage_items"):
            print(f"{'DVC Details':<15}:")
            for dvc_item in item["dvc_linkage_items"]:
                print(
                    f"  - {dvc_item.get('path')} "
                    f"[{dvc_item.get('hash_algo')}={dvc_item.get('hash_value')}] "
                    f"in_cache={dvc_item.get('is_in_cache')} remote={dvc_item.get('remote_probe_status')}"
                )
        run_payload = item.get("run_command_payload") or {}
        if run_payload:
            print(f"{'Run Details':<15}:")
            print(f"  - entrypoint={run_payload.get('entrypoint')}")
            print(f"  - script={run_payload.get('script')}")
            print(f"  - dataset={run_payload.get('dataset')}")
            print(f"  - storage={run_payload.get('storage')}")
            print(f"  - cwd={run_payload.get('cwd')}")
    
    print("-" * 80)

def print_table(data):
    print("\nCommits and Snapshots:")
    print("-" * 84)
    print(f"{'#':<3} {'Type':<10} {'ID':<20} {'Version':<20} {'DVC':<12} {'Env':<10} {'Cmd':<18} {'Paths':<6} {'Timestamp':<16}")
    print("-" * 84)
    
    for idx, item in enumerate(data, 1):
        print(
            f"{idx:<3} {item['type']:<10} {item['id'][:8]:<20} {item['dvc_version']:<20} "
            f"{item.get('dvc_linkage_status', 'missing'):<12} {item.get('env_fingerprint_status', 'missing'):<10} "
            f"{(item.get('run_command_summary') or 'None')[:18]:<18} "
            f"{item.get('dvc_linkage_count', 0):<6} "
            f"{format_timestamp(item['timestamp']):<16}"
        )
    
    print("-" * 84)