import json

def add_read_field_to_feedback(json_file_path):
    # Load the existing feedback data
    with open(json_file_path, 'r') as file:
        feedback_data = json.load(file)

    # Add 'read': False to each feedback entry
    for feedback in feedback_data:
        if 'read' not in feedback:
            feedback['read'] = False

    # Save the updated feedback data back to the file
    with open(json_file_path, 'w') as file:
        json.dump(feedback_data, file, indent=4)

    print(f"Updated {len(feedback_data)} feedback entries with 'read': False")

# Example usage:
json_file_path = 'feedback.json'  # Replace with your actual file path
add_read_field_to_feedback(json_file_path)

