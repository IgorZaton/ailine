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
        print(f"{'Snapshot Path':<15}: {item['snapshot_path'] or 'None'}")
        print(f"{'Timestamp':<15}: {format_timestamp(item['timestamp'])}")
        print(f"{'Git URL':<15}: {item['git_url'] or 'None'}")
    
    print("-" * 80)

def print_table(data):
    print("\nCommits and Snapshots:")
    print("-" * 84)
    print(f"{'#':<3} {'Type':<10} {'ID':<20} {'Version':<20} {'Timestamp':<16}")
    print("-" * 84)
    
    for idx, item in enumerate(data, 1):
        print(f"{idx:<3} {item['type']:<10} {item['id'][:8]:<20} {item['dvc_version']:<20} {format_timestamp(item['timestamp']):<16}")
    
    print("-" * 84)